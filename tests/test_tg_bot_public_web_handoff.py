# -*- coding: utf-8 -*-
"""Публичный бот → сайт: развилка после трека, /site, онбординг без подписки.

docs/BOT_TO_WEB_FLOW.md. Хэндлеры гоняются на in-process фейках (как в
test_tg_bot_public_vibe_flow): сеть, S3 и Postgres подменены.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.tg_bot_public import app as pub
from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public.credits_db import CreditsDB, WEB_HANDOFF_KINDS, _jsonb_dict
from services.tg_bot_public.state_store import (
    STAGE_WAIT_TIMING_INPUT,
    STAGE_WAIT_WEB_FORK,
    ChatState,
)

CHAT = 7


class _Store:
    def __init__(self):
        self.by_id: dict[int, ChatState] = {}

    async def get(self, chat_id):
        return self.by_id.get(int(chat_id)) or ChatState(chat_id=int(chat_id))

    async def set(self, st):
        self.by_id[int(st.chat_id)] = st


class _CreditsDB:
    def __init__(self):
        self.events: list[tuple[int, str]] = []
        self.handoffs: list[dict] = []
        self.grants: list[tuple] = []

    async def log_event(self, tg_id, event, detail=""):
        self.events.append((tg_id, event))

    async def create_web_handoff(self, tg_id, kind, payload=None, *, ttl_seconds):
        self.handoffs.append({"tg_id": tg_id, "kind": kind, "payload": payload, "ttl": ttl_seconds})
        return f"tok{len(self.handoffs)}"

    async def grant_initial_credits_once(self, tg_id, credits, track_credits, *, actor=""):
        self.grants.append((tg_id, credits, track_credits, actor))
        return {"applied": True}


class _S3:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.uploads: list[dict] = []

    def upload_file(self, *, path, bucket, key, content_type=None):
        if self.fail:
            raise RuntimeError("s3 down")
        self.uploads.append({"path": Path(path), "bucket": bucket, "key": key})
        return f"s3://{bucket}/{key}"


class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.chat = SimpleNamespace(id=CHAT)
        self.from_user = SimpleNamespace(first_name="Лена", last_name="", username="lena_beats")
        self.audio = self.voice = self.document = None
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))


def _settings(**over):
    base = dict(
        web_app_url="https://app.blast808.com",
        web_handoff_ttl_s=172800,
        web_handoff_enabled=True,
        onboarding_subscription_required=False,
        s3_bucket_raw_audio="raw-audio",
        s3_raw_audio_prefix="raw_audio",
        initial_credits=5,
        initial_track_credits=1,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _make_app(*, s3_fail=False, **settings):
    app = pub.BlastBotApp.__new__(pub.BlastBotApp)
    app.store = _Store()
    app.credits_db = _CreditsDB()
    app.s3 = _S3(fail=s3_fail)
    app.settings = _settings(**settings)
    return app


def _state_with_track(tmp_path: Path) -> ChatState:
    audio = tmp_path / "song_prepared.mp3"
    audio.write_bytes(b"ID3 prepared bytes")
    return ChatState(chat_id=CHAT, prepared_audio_local_path=str(audio), pending_audio_filename="My_Song.mp3")


def _run(coro):
    return asyncio.run(coro)


def test_fork_uploads_track_and_offers_site_link(tmp_path):
    app = _make_app()
    st = _state_with_track(tmp_path)
    msg = _Msg()

    _run(app._offer_web_fork(msg, st))

    upload = app.s3.uploads[0]
    assert upload["bucket"] == "raw-audio" and upload["key"].startswith("raw_audio/7/")
    handoff = app.credits_db.handoffs[0]
    assert handoff["kind"] == "track" and handoff["ttl"] == 172800
    payload = handoff["payload"]
    assert payload["audioS3Url"] == f"s3://raw-audio/{upload['key']}"
    # хэш — тот же, по которому бот считает треки (подготовленный mp3)
    assert payload["audioHash"] == hashlib.sha256(b"ID3 prepared bytes").hexdigest()
    assert payload["filename"] == "My_Song.mp3"
    assert payload["profile"] == {"name": "Лена", "surname": "", "username": "lena_beats"}
    saved = app.store.by_id[CHAT]
    assert saved.stage == STAGE_WAIT_WEB_FORK
    assert saved.web_handoff_url == "https://app.blast808.com/go/tok1"
    text, markup = msg.answers[-1]
    assert text == mt.WEB_FORK_TEXT
    site_btn, bot_btn = markup.inline_keyboard[0][0], markup.inline_keyboard[1][0]
    assert site_btn.text == mt.BTN_WEB_FORK_SITE and site_btn.url == saved.web_handoff_url
    assert bot_btn.text == mt.BTN_WEB_FORK_BOT and bot_btn.callback_data == mt.WEB_FORK_CALLBACK_BOT
    assert (CHAT, "web_fork_shown") in app.credits_db.events


def test_fork_failure_is_reported_and_continues_in_bot(tmp_path):
    app = _make_app(s3_fail=True)
    msg = _Msg()

    _run(app._offer_web_fork(msg, _state_with_track(tmp_path)))

    assert (CHAT, "web_fork_failed") in app.credits_db.events
    assert app.credits_db.handoffs == []
    assert msg.answers[0][0] == mt.WEB_FORK_UNAVAILABLE
    assert app.store.by_id[CHAT].stage == STAGE_WAIT_TIMING_INPUT


def test_typing_at_fork_resends_the_same_link(tmp_path):
    app = _make_app()
    st = _state_with_track(tmp_path)
    _run(app._offer_web_fork(_Msg(), st))
    msg = _Msg(text="а что дальше?")

    _run(app._handle_wait_web_fork(msg, app.store.by_id[CHAT]))

    text, markup = msg.answers[-1]
    assert text == mt.WEB_FORK_REMINDER
    assert markup.inline_keyboard[0][0].url == "https://app.blast808.com/go/tok1"
    assert len(app.credits_db.handoffs) == 1  # новый токен не выпускаем


def test_choosing_bot_continues_the_current_flow(tmp_path):
    app = _make_app()
    _run(app._offer_web_fork(_Msg(), _state_with_track(tmp_path)))
    msg = _Msg(text=mt.BTN_WEB_FORK_BOT)

    _run(app._handle_wait_web_fork(msg, app.store.by_id[CHAT]))

    assert app.store.by_id[CHAT].stage == STAGE_WAIT_TIMING_INPUT
    assert msg.answers[-1][0].startswith(mt.WEB_FORK_BOT_PREFIX)
    assert (CHAT, "web_fork_bot") in app.credits_db.events


def test_lets_go_skips_subscription_and_grants_quota(monkeypatch):
    app = _make_app()
    moved: list[int] = []

    async def _wait_audio(chat_id, message):
        moved.append(chat_id)

    async def _subscription(chat_id, message):
        raise AssertionError("subscription gate must be skipped")

    monkeypatch.setattr(app, "_move_to_wait_audio", _wait_audio, raising=False)
    monkeypatch.setattr(app, "_move_to_subscription", _subscription, raising=False)

    _run(app._handle_wait_start(_Msg(text=pub.BTN_LETS_GO), ChatState(chat_id=CHAT)))

    assert moved == [CHAT]
    assert app.credits_db.grants == [(CHAT, 5, 1, "tg_bot_public")]


def test_subscription_gate_stays_available_behind_the_flag(monkeypatch):
    app = _make_app(onboarding_subscription_required=True)
    gated: list[int] = []

    async def _subscription(chat_id, message):
        gated.append(chat_id)

    monkeypatch.setattr(app, "_move_to_subscription", _subscription, raising=False)
    _run(app._handle_wait_start(_Msg(text=pub.BTN_LETS_GO), ChatState(chat_id=CHAT)))
    assert gated == [CHAT] and app.credits_db.grants == []


def test_chat_parked_on_subscription_is_let_in_when_gate_is_off(monkeypatch):
    app = _make_app()
    moved: list[int] = []

    async def _wait_audio(chat_id, message):
        moved.append(chat_id)

    monkeypatch.setattr(app, "_move_to_wait_audio", _wait_audio, raising=False)
    _run(app._handle_wait_subscription(_Msg(text="привет"), ChatState(chat_id=CHAT)))
    assert moved == [CHAT] and len(app.credits_db.grants) == 1


def test_site_command_sends_a_fresh_login_link():
    app = _make_app()
    msg = _Msg(text="/site")
    _run(app._send_site_link(msg))
    assert app.credits_db.handoffs[0]["kind"] == "site"
    text, markup = msg.answers[0]
    assert text == mt.WEB_SITE_TEXT
    assert markup.inline_keyboard[0][0].url == "https://app.blast808.com/go/tok1"


def test_site_command_without_site_says_so():
    app = _make_app(web_app_url="", web_handoff_enabled=False)
    msg = _Msg(text="/site")
    _run(app._send_site_link(msg))
    assert msg.answers == [(mt.WEB_SITE_DISABLED, None)] and app.credits_db.handoffs == []


def test_site_more_lines_only_when_site_is_on(monkeypatch):
    from services.tg_bot_public import config

    monkeypatch.setattr(pub, "SETTINGS", config.Settings(web_app_url="https://app.blast808.com"))
    assert pub._site_more_line(mt.SITE_MORE_STYLES) == "\n\n" + mt.SITE_MORE_STYLES
    monkeypatch.setattr(pub, "SETTINGS", config.Settings(web_app_url=""))
    assert pub._site_more_line(mt.SITE_MORE_STYLES) == ""


def test_audio_step_routes_to_fork_when_site_is_on():
    import inspect

    src = inspect.getsource(pub.BlastBotApp._handle_wait_audio)
    assert "if self.settings.web_handoff_enabled:" in src
    assert "await self._offer_web_fork(message, st)" in src


def test_handoff_kinds_and_token_hash():
    assert WEB_HANDOFF_KINDS == ("track", "site")
    assert CreditsDB.hash_handoff_token("abc") == hashlib.sha256(b"abc").hexdigest()
    with pytest.raises(ValueError):
        _run(CreditsDB.__new__(CreditsDB).create_web_handoff(1, "job", {}, ttl_seconds=60))
    assert _jsonb_dict('{"a": 1}') == {"a": 1} and _jsonb_dict(None) == {} and _jsonb_dict({"b": 2}) == {"b": 2}


def test_settings_reject_relative_site_url(monkeypatch):
    from services.tg_bot_public import config

    with pytest.raises(RuntimeError):
        config.Settings(web_app_url="app.blast808.com")
    assert config.Settings(web_app_url="https://app.blast808.com").web_handoff_enabled is True
    assert config.Settings(web_app_url="").web_handoff_enabled is False
