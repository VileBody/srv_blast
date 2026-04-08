from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from fastapi import APIRouter
from fastapi.testclient import TestClient

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")

    class _DummyPool:
        async def close(self):
            return None

    class _DummyConnection:
        pass

    async def _create_pool(*args, **kwargs):
        return _DummyPool()

    asyncpg_stub.Pool = _DummyPool
    asyncpg_stub.Connection = _DummyConnection
    asyncpg_stub.create_pool = _create_pool
    sys.modules["asyncpg"] = asyncpg_stub

from services.orchestrator import app as orchestrator_app


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value):
        self._data[key] = str(value)
        return True

    def delete(self, key: str):
        self._data.pop(key, None)
        return 1

    def mget(self, keys: list[str]):
        return [self._data.get(k) for k in keys]


class _FakeStore:
    def __init__(self) -> None:
        self.r = _FakeRedis()
        self.key_prefix = "blast_test"


def _settings(**overrides):
    base = {
        "credits_db_url": "",
        "payment_webhook_secret": "",
        "payment_admin_token": "",
        "footage_inventory_json": "data/footage_inventory.json",
        "descriptions_bundle_path": "pins/descriptions_bundle.json",
        "descriptions_bundle_max_assets": "",
        "windows_base_url": "http://85.239.48.31:8000",
        "windows_base_urls_csv": " http://72.56.246.24:8000 , http://85.239.48.31:8000/ ",
        "windows_node_lease_ttl_s": 7200,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_client(monkeypatch) -> TestClient:
    store = _FakeStore()
    monkeypatch.setattr(orchestrator_app, "SETTINGS", _settings())
    monkeypatch.setattr(orchestrator_app, "create_asset_router", lambda: APIRouter())
    monkeypatch.setattr(orchestrator_app.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(orchestrator_app, "ensure_config_initialized", lambda _store: None)
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_descriptions_bundle",
        lambda **_: SimpleNamespace(ok=True, action="ok", bundle_path="bundle", reason=""),
    )
    monkeypatch.setattr(
        orchestrator_app,
        "get_runtime_status",
        lambda _store: {
            "sdk": SimpleNamespace(enabled=True, weight=1, max_inflight=4),
            "openrouter": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
            "hybrid": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
        },
    )
    app = orchestrator_app.create_app()
    return TestClient(app)


def test_windows_nodes_uses_env_when_runtime_pool_is_empty(monkeypatch) -> None:
    with _build_client(monkeypatch) as client:
        resp = client.get("/windows-nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "env"
    assert body["runtime_urls"] == []
    assert body["default_urls"] == [
        "http://85.239.48.31:8000",
        "http://72.56.246.24:8000",
    ]
    assert body["effective_urls"] == body["default_urls"]


def test_windows_nodes_put_sets_runtime_override(monkeypatch) -> None:
    with _build_client(monkeypatch) as client:
        put_resp = client.put(
            "/windows-nodes",
            json={"urls": ["http://72.56.246.24:8000/", "http://72.56.246.24:8000"]},
        )
        get_resp = client.get("/windows-nodes")

    assert put_resp.status_code == 200
    put_body = put_resp.json()
    assert put_body["source"] == "runtime"
    assert put_body["runtime_urls"] == ["http://72.56.246.24:8000"]
    assert put_body["effective_urls"] == ["http://72.56.246.24:8000"]

    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body["source"] == "runtime"
    assert get_body["runtime_urls"] == ["http://72.56.246.24:8000"]


def test_windows_nodes_put_empty_clears_runtime_override(monkeypatch) -> None:
    with _build_client(monkeypatch) as client:
        client.put("/windows-nodes", json={"urls": ["http://72.56.246.24:8000"]})
        clear_resp = client.put("/windows-nodes", json={"urls": []})
        final_resp = client.get("/windows-nodes")

    assert clear_resp.status_code == 200
    assert clear_resp.json()["source"] == "env"
    assert final_resp.status_code == 200
    body = final_resp.json()
    assert body["source"] == "env"
    assert body["runtime_urls"] == []
    assert body["effective_urls"] == [
        "http://85.239.48.31:8000",
        "http://72.56.246.24:8000",
    ]


def test_windows_nodes_put_nodes_supports_disabled_entries(monkeypatch) -> None:
    with _build_client(monkeypatch) as client:
        put_resp = client.put(
            "/windows-nodes",
            json={
                "nodes": [
                    {
                        "url": "http://85.239.48.31:8000",
                        "enabled": False,
                        "disabled_reason": "poll_timeout_before_poll",
                    },
                    {"url": "http://72.56.246.24:8000", "enabled": True},
                ]
            },
        )
        get_resp = client.get("/windows-nodes")

    assert put_resp.status_code == 200
    put_body = put_resp.json()
    assert put_body["source"] == "runtime"
    assert put_body["runtime_urls"] == ["http://72.56.246.24:8000"]
    assert put_body["effective_urls"] == ["http://72.56.246.24:8000"]
    assert any(
        row["url"] == "http://85.239.48.31:8000"
        and row["enabled"] is False
        and row["disabled_reason"] == "poll_timeout_before_poll"
        for row in put_body["nodes"]
    )

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["source"] == "runtime"
    assert body["effective_urls"] == ["http://72.56.246.24:8000"]
