"""Протухшая CDN-ссылка TikTok-аватара: перезапрос профиля и зеркало в S3, один раз."""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from tests.test_web_asr_preview import _env

EXPIRED = "https://p16-sign-va.tiktokcdn.com/avatar.jpeg?x-expires=1700000000&x-signature=abc"


@pytest.fixture
def main(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    module = importlib.import_module("app.main")
    backend = SimpleNamespace(config=SimpleNamespace(s3_endpoint_url="https://s3.example.net"))
    monkeypatch.setattr(module, "_production_backend", lambda: backend)
    monkeypatch.setattr(module.persistence, "flush_user", lambda user_id: None)
    records: dict[str, dict] = {}
    monkeypatch.setattr(module.tiktok_token_store, "load", lambda uid: records.get(uid))
    monkeypatch.setattr(module.tiktok_token_store, "save", lambda uid, rec: records.__setitem__(uid, dict(rec)))
    monkeypatch.setattr(module, "_access_token", lambda: "token")
    module._test_records = records
    return module


def _connect(main, avatar: str | None) -> str:
    uid = main.store.current_user_id()
    main.store.connect_tiktok("artist", "open_1", {}, avatar_url=avatar)
    main._test_records[uid] = {"openId": "open_1", "avatarUrl": avatar}
    return uid


def test_foreign_detection(main) -> None:
    assert main._is_foreign_avatar(EXPIRED)
    assert not main._is_foreign_avatar("s3://bucket/avatars/a.jpg")
    assert not main._is_foreign_avatar("https://s3.example.net/bucket/avatars/a.jpg?X-Amz-Signature=1")
    assert not main._is_foreign_avatar("https://bucket.s3.example.net/avatars/a.jpg")
    assert not main._is_foreign_avatar(None)


def test_expired_cdn_avatar_is_refetched_and_mirrored_once(main, monkeypatch: pytest.MonkeyPatch) -> None:
    uid = _connect(main, EXPIRED)
    calls: list[str] = []
    monkeypatch.setattr(main.tiktok_api, "fetch_user_info", lambda token: calls.append(token) or {"avatar_url": "https://p16.tiktokcdn.com/fresh.jpeg"})
    monkeypatch.setattr(main, "_upload_tiktok_avatar", lambda url, user: f"s3://bucket/avatars/{user}.jpg")

    assert main._refresh_tiktok_avatar(uid) == f"s3://bucket/avatars/{uid}.jpg"
    assert main.store.ws().tiktok["avatarUrl"] == f"s3://bucket/avatars/{uid}.jpg"
    # в записи токена тоже — иначе рефреш токена вернул бы протухшую ссылку
    assert main._test_records[uid]["avatarUrl"] == f"s3://bucket/avatars/{uid}.jpg"
    # второй раз ничего не делаем
    assert main._refresh_tiktok_avatar(uid) == f"s3://bucket/avatars/{uid}.jpg"
    assert calls == ["token"]


def test_failed_refresh_drops_the_avatar_instead_of_a_broken_link(main, monkeypatch: pytest.MonkeyPatch) -> None:
    uid = _connect(main, EXPIRED)

    def boom(token):
        raise RuntimeError("scope user.info.basic missing")

    monkeypatch.setattr(main.tiktok_api, "fetch_user_info", boom)
    assert main._refresh_tiktok_avatar(uid) is None
    assert main.store.ws().tiktok["avatarUrl"] is None
    assert main._test_records[uid]["avatarUrl"] is None
    # больше не пробуем: ссылки на чужой CDN не осталось
    assert main._refresh_tiktok_avatar(uid) is None
