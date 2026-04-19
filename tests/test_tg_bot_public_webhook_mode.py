from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.tg_bot_public import app as public_app


class _FakeStore:
    def __init__(self) -> None:
        self._seen: set[int] = set()

    async def mark_webhook_update_seen(self, update_id: int, *, ttl_s: int) -> bool:
        _ = ttl_s
        uid = int(update_id)
        if uid in self._seen:
            return False
        self._seen.add(uid)
        return True


class _FakeDispatcher:
    def __init__(self) -> None:
        self.feed_calls: list[dict] = []

    async def feed_update(self, _bot, update) -> None:
        self.feed_calls.append(dict(update))


def _new_app() -> public_app.BlastBotApp:
    app = object.__new__(public_app.BlastBotApp)
    app.settings = SimpleNamespace(
        tg_webhook_secret="secret-123",
        tg_webhook_path="/telegram/webhook",
        tg_webhook_dedup_ttl_s=3600,
        tg_webhook_url="https://blast808.com",
        tg_webhook_bind_host="0.0.0.0",
        tg_webhook_port=8081,
    )
    app.store = _FakeStore()
    app.dp = _FakeDispatcher()
    app._bot = object()
    app._bot_ref = [app._bot]
    return app


def test_webhook_rejects_invalid_secret(monkeypatch) -> None:
    app = _new_app()
    monkeypatch.setattr(public_app, "Update", SimpleNamespace(model_validate=lambda payload: payload))
    web = app._create_webhook_app()
    with TestClient(web) as client:
        resp = client.post("/telegram/webhook", json={"update_id": 1}, headers={})
    assert resp.status_code == 403


def test_webhook_dedups_updates(monkeypatch) -> None:
    app = _new_app()
    monkeypatch.setattr(public_app, "Update", SimpleNamespace(model_validate=lambda payload: payload))
    web = app._create_webhook_app()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-123"}
    with TestClient(web) as client:
        r1 = client.post("/telegram/webhook", json={"update_id": 42, "message": {"text": "a"}}, headers=headers)
        r2 = client.post("/telegram/webhook", json={"update_id": 42, "message": {"text": "a"}}, headers=headers)

    assert r1.status_code == 200
    assert r1.json()["ok"] is True
    assert r2.status_code == 200
    assert r2.json().get("dedup") is True
    assert len(app.dp.feed_calls) == 1
