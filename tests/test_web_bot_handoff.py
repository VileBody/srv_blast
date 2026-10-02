"""Ссылка «на сайт» из публичного бота: вход по токену + проект с треком из бота."""
from __future__ import annotations

import dataclasses
import importlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_web_asr_preview import _env

TOKEN = "t" * 32
CHAT = 777000111


class _Billing:
    def __init__(self, record: dict[str, Any] | None, *, allowed: bool = True) -> None:
        self.record = record
        self.allowed = allowed
        self.consumed: list[tuple[int, str]] = []
        self.results: list[dict[str, Any]] = []

    async def redeem_handoff(self, token: str) -> dict[str, Any] | None:
        assert token == TOKEN
        if self.record is None:
            return None
        self.record["redeem_count"] += 1
        return {**self.record, "result": dict(self.record["result"])}

    async def set_handoff_result(self, token: str, result: dict[str, Any]) -> None:
        self.results.append(result)
        self.record["result"] = result

    async def can_upload_track(self, tg_id: int, audio_hash: str) -> bool:
        return self.allowed

    async def consume_track(self, tg_id: int, audio_hash: str) -> str:
        self.consumed.append((tg_id, audio_hash))
        return "consumed"


class _Backend:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def register_bot_track(self, s3_url: str, *, filename: str) -> dict[str, str]:
        self.registered.append(s3_url)
        return {"s3_url": s3_url, "playback_url": "https://s3.example/presigned.mp3"}


def _track_record() -> dict[str, Any]:
    return {
        "tg_id": CHAT,
        "kind": "track",
        "payload": {
            "audioS3Url": "s3://raw-audio/raw_audio/777000111/20261002_x_song.mp3",
            "audioHash": "a" * 64,
            "filename": "My Song.mp3",
            "profile": {"name": "Лена", "surname": "", "username": "lena_beats"},
        },
        "result": {},
        "redeem_count": 0,
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        yield tc, main
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def _as_user(main) -> None:
    """Проверки — в воркспейсе того, кого залогинила ссылка (вне запроса контекст демо)."""
    main.store.use_user(main.auth_store.get_user_by_chat(CHAT)["id"])


def _production(monkeypatch: pytest.MonkeyPatch, main, billing: _Billing, backend: _Backend | None = None) -> None:
    monkeypatch.setattr(main, "RUNTIME", dataclasses.replace(main.RUNTIME, backend="production"))
    monkeypatch.setattr(main, "_billing_backend", lambda: billing)
    monkeypatch.setattr(main, "_production_backend", lambda: backend or _Backend())


def test_track_link_creates_account_project_and_track(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_track_record()), _Backend()
    _production(monkeypatch, main, billing, backend)

    r = tc.post("/api/auth/handoff", json={"token": TOKEN})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["redirectTo"] == f"/app/generate?project={body['projectId']}"
    assert body["track"]["s3Key"] == "s3://raw-audio/raw_audio/777000111/20261002_x_song.mp3"
    assert body["track"]["audioHash"] == "a" * 64
    user = main.auth_store.get_user_by_chat(CHAT)
    assert user is not None and user["name"] == "Лена" and user["tgUsername"] == "lena_beats"
    # бот входа этот человек не запускал — уведомления сайта пойдут от публичного бота
    assert main.auth_store.notify_bot_for_chat(CHAT) == "public"
    _as_user(main)
    assert main.store.get_project(body["projectId"]) is not None
    assert backend.registered == [body["track"]["s3Key"]]
    assert billing.consumed == [(CHAT, "a" * 64)]
    assert billing.results[-1] == {"projectId": body["projectId"], "trackId": body["track"]["id"]}


def test_reopening_the_link_lands_on_the_same_project(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_track_record()), _Backend()
    _production(monkeypatch, main, billing, backend)
    first = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()

    second = tc.post("/api/auth/handoff", json={"token": TOKEN})

    assert second.status_code == 200
    body = second.json()
    assert body["projectId"] == first["projectId"] and body["repeat"] is True
    assert body["track"]["id"] == first["track"]["id"]
    assert body["created"] is False
    _as_user(main)
    assert [p["id"] for p in main.store.ws().projects] == [first["projectId"]]
    assert len(backend.registered) == 1 and len(billing.consumed) == 1


def test_expired_link_is_410_with_code(client, monkeypatch) -> None:
    tc, main = client
    _production(monkeypatch, main, _Billing(None))
    r = tc.post("/api/auth/handoff", json={"token": TOKEN})
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "handoff_expired"


def test_no_track_slot_opens_project_without_the_track(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_track_record(), allowed=False), _Backend()
    _production(monkeypatch, main, billing, backend)
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert body["trackError"] == "tracks_limit"
    _as_user(main)
    assert body["track"] is None and main.store.get_project(body["projectId"]) is not None
    assert backend.registered == [] and billing.consumed == []


def test_site_link_only_logs_in(client, monkeypatch) -> None:
    tc, main = client
    record = {"tg_id": CHAT, "kind": "site", "payload": {"profile": {"name": "Лена"}}, "result": {}, "redeem_count": 0}
    billing = _Billing(record)
    _production(monkeypatch, main, billing)
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert body["redirectTo"] == "/app" and "projectId" not in body
    assert main.auth_store.get_user_by_chat(CHAT) is not None
    assert billing.consumed == []
    _as_user(main)
    assert main.store.ws().projects == []


def test_existing_account_is_reused(client, monkeypatch) -> None:
    tc, main = client
    existing = main.auth_store.create_user_from_telegram(CHAT, {"name": "Старое имя"})
    _production(monkeypatch, main, _Billing(_track_record()))
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert body["created"] is False
    assert main.auth_store.get_user_by_chat(CHAT)["id"] == existing["id"]
    assert main.auth_store.get_user_by_chat(CHAT)["name"] == "Старое имя"


def test_mock_backend_refuses_explicitly(client) -> None:
    tc, _ = client
    r = tc.post("/api/auth/handoff", json={"token": TOKEN})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "handoff_unavailable"


def test_bot_track_must_live_in_the_raw_audio_bucket(monkeypatch) -> None:
    _env(monkeypatch)
    sys.modules.pop("app.production_backend", None)
    pb = importlib.import_module("app.production_backend")
    heads: list[tuple[str, str]] = []
    backend = pb.ProductionBackend.__new__(pb.ProductionBackend)
    backend.config = SimpleNamespace(raw_audio_bucket="raw-audio")
    backend._s3 = SimpleNamespace(head_object=lambda Bucket, Key: heads.append((Bucket, Key)))
    monkeypatch.setattr(backend, "_presign", lambda bucket, key, **kw: f"https://s3/{bucket}/{key}")

    with pytest.raises(pb.ProductionBackendError):
        backend.register_bot_track("s3://other-bucket/x.mp3", filename="x.mp3")
    assert heads == []
    out = backend.register_bot_track("s3://raw-audio/raw_audio/1/x.mp3", filename="x.mp3")
    assert out == {"s3_url": "s3://raw-audio/raw_audio/1/x.mp3", "playback_url": "https://s3/raw-audio/raw_audio/1/x.mp3"}
    assert heads == [("raw-audio", "raw_audio/1/x.mp3")]


def test_s3_failure_leaves_no_empty_project(client, monkeypatch) -> None:
    """Сбой на проверке трека — 503 без пустого проекта; повтор заводит ровно один."""
    tc, main = client
    billing = _Billing(_track_record())

    class _Flaky(_Backend):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        def register_bot_track(self, s3_url: str, *, filename: str) -> dict[str, str]:
            if self.fail:
                raise TimeoutError("s3 head timeout")
            return super().register_bot_track(s3_url, filename=filename)

    backend = _Flaky()
    _production(monkeypatch, main, billing, backend)
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 503
    _as_user(main)
    assert main.store.ws().projects == []
    backend.fail = False
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    _as_user(main)
    assert [p["id"] for p in main.store.ws().projects] == [body["projectId"]]
    assert billing.consumed == [(CHAT, "a" * 64)]
