from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter
from fastapi.testclient import TestClient

from services.orchestrator import app as orchestrator_app


class _FakeRedis:
    def ping(self) -> bool:
        return True


class _FakeStore:
    def __init__(self) -> None:
        self.r = _FakeRedis()


def _settings(**overrides):
    base = {
        "credits_db_url": "",
        "payment_webhook_secret": "",
        "payment_admin_token": "",
        "footage_inventory_json": "data/footage_inventory.json",
        "descriptions_bundle_path": "pins/descriptions_bundle.json",
        "descriptions_bundle_max_assets": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_health_is_not_green_when_llm_admission_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_app, "SETTINGS", _settings())
    monkeypatch.setattr(orchestrator_app, "create_asset_router", lambda: APIRouter())
    monkeypatch.setattr(orchestrator_app.JobStore, "from_env", classmethod(lambda cls: _FakeStore()))
    monkeypatch.setattr(orchestrator_app, "ensure_config_initialized", lambda store: None)
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_descriptions_bundle",
        lambda **_: SimpleNamespace(ok=True, action="ok", bundle_path="bundle", reason=""),
    )
    monkeypatch.setattr(
        orchestrator_app,
        "get_runtime_status",
        lambda store: {
            "sdk": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
            "openrouter": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
            "hybrid": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
        },
    )

    app = orchestrator_app.create_app()
    client = TestClient(app)
    resp = client.get("/health")
    payload = resp.json()

    assert resp.status_code == 200
    assert payload["ok"] is False
    assert payload["checks"]["llm_admission_ready"] is False


def test_health_is_not_green_when_payment_router_enabled_without_db(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        _settings(payment_webhook_secret="secret", payment_admin_token="", credits_db_url=""),
    )
    monkeypatch.setattr(orchestrator_app, "create_asset_router", lambda: APIRouter())
    monkeypatch.setattr(orchestrator_app.JobStore, "from_env", classmethod(lambda cls: _FakeStore()))
    monkeypatch.setattr(orchestrator_app, "ensure_config_initialized", lambda store: None)
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_descriptions_bundle",
        lambda **_: SimpleNamespace(ok=True, action="ok", bundle_path="bundle", reason=""),
    )
    monkeypatch.setattr(
        orchestrator_app,
        "get_runtime_status",
        lambda store: {
            "sdk": SimpleNamespace(enabled=True, weight=1, max_inflight=4),
            "openrouter": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
            "hybrid": SimpleNamespace(enabled=False, weight=0, max_inflight=4),
        },
    )

    app = orchestrator_app.create_app()
    client = TestClient(app)
    resp = client.get("/health")
    payload = resp.json()

    assert resp.status_code == 200
    assert payload["ok"] is False
    assert payload["checks"]["payment_db_ready"] is False

