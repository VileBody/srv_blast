# -*- coding: utf-8 -*-
"""Безопасность ссылок «на сайт»: одноразовые ссылки и подтверждение привязки в Telegram.

Регресс: ссылки развилки/«Докрутить»/CTA были многоразовыми 48 ч, а Telegram сохраняет
URL-кнопки в пересланном сообщении — любой, кому переслали, входил в аккаунт. Привязка
Telegram к открытому на сайте аккаунту шла по одному клику на сайте.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services.tg_bot_public import app as pub
from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public.credits_db import CreditsDB

from tests.test_tg_bot_public_web_handoff import CHAT, _make_app, _Msg


def _run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, *, row=None, val=None):
        self.calls: list[tuple[str, str, tuple]] = []
        self.row = row
        self.val = val

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.row

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.val


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _db(conn) -> CreditsDB:
    db = CreditsDB.__new__(CreditsDB)
    db._pool_or_fail = lambda: _Pool(conn)
    return db


# ── одноразовые ссылки ─────────────────────────────────────────────────────────

def test_every_minted_link_is_single_use():
    conn = _Conn()
    for kind in ("track", "remix", "site"):
        _run(_db(conn).create_web_handoff(CHAT, kind, {}, ttl_seconds=60))
    assert [c[2][-1] for c in conn.calls] == [1, 1, 1]  # max_redeems


def test_legacy_multi_use_links_count_as_single_use():
    """Ссылки, выпущенные до перехода (max_redeems NULL), гаснут после первого открытия."""
    conn = _Conn()
    _run(_db(conn).redeem_web_handoff("tok-legacy"))
    _run(_db(conn).inspect_web_handoff("tok-legacy"))
    redeem_sql, inspect_sql = conn.calls[0][1], conn.calls[1][1]
    assert "redeem_count < COALESCE(max_redeems, 1)" in redeem_sql
    assert "max_redeems IS NULL" not in redeem_sql
    assert "redeem_count >= COALESCE(max_redeems, 1) AS used" in inspect_sql


def test_inspect_tells_live_used_and_expired_apart():
    base = {"tg_id": CHAT, "kind": "track", "payload": "{}", "result": '{"projectId": "p1"}'}
    cases = [({"used": False, "expired": False}, "live"), ({"used": True, "expired": False}, "used"),
             ({"used": False, "expired": True}, "expired"), ({"used": True, "expired": True}, "used")]
    for flags, status in cases:
        info = _run(_db(_Conn(row={**base, **flags})).inspect_web_handoff("tok"))
        assert info["status"] == status and info["result"] == {"projectId": "p1"}
    assert _run(_db(_Conn(row=None)).inspect_web_handoff("tok")) is None
    assert _run(_db(_Conn()).inspect_web_handoff("")) is None


def test_site_login_deep_link_answers_with_a_fresh_link():
    """Кнопка «Получить новую ссылку в боте» на сайте → /start site_login → ссылка сразу."""
    app = _make_app()
    msg = _Msg(text=f"/start {mt.WEB_SITE_LOGIN_START}")
    assert _run(app._maybe_send_site_login_from_start(msg)) is True
    assert app.credits_db.handoffs[0]["kind"] == "site"
    assert msg.answers[0][0] == mt.WEB_SITE_TEXT
    other = _Msg(text="/start instagram_bio")
    assert _run(app._maybe_send_site_login_from_start(other)) is False
    assert len(app.credits_db.handoffs) == 1 and other.answers == []


def test_site_cta_link_carries_the_telegram_profile():
    """Сайт показывает @username и имя в вопросе «привязать этот Telegram?»."""
    app = _make_app()
    st = pub.ChatState(chat_id=CHAT, web_remix_payload={"audioS3Url": "s3://raw/x.mp3", "audioHash": "h",
                                                       "profile": {"username": "old"}})
    _run(app._send_site_unlimited_cta(_Msg(), st, mt.SITE_CTA_AFTER_PITCH))
    handoff = app.credits_db.handoffs[-1]
    assert handoff["kind"] == "remix"
    assert handoff["payload"]["profile"] == {"name": "Лена", "surname": "", "username": "lena_beats"}
    assert st.web_remix_payload["profile"] == {"username": "old"}  # снимок батча не трогаем


def test_remix_profile_comes_from_the_chat_and_survives_a_telegram_failure():
    class _Bot:
        def __init__(self, fail):
            self.fail = fail

        async def get_chat(self, chat_id):
            if self.fail:
                raise RuntimeError("telegram down")
            return SimpleNamespace(first_name="Лена", last_name="К", username="lena_beats")

    ok = _run(pub.BlastBotApp._chat_profile(_Bot(False), CHAT, username="x"))
    assert ok == {"name": "Лена", "surname": "К", "username": "lena_beats"}
    assert _run(pub.BlastBotApp._chat_profile(_Bot(True), CHAT, username="x")) == {"username": "x"}


def test_every_site_link_message_says_it_is_single_use():
    for text in (mt.WEB_FORK_TEXT, mt.WEB_FORK_REMINDER, mt.WEB_REMIX_TEXT,
                 mt.SITE_CTA_AFTER_LOW, mt.SITE_CTA_AFTER_MID, mt.SITE_CTA_AFTER_PITCH):
        assert text.endswith(mt.WEB_LINK_PERSONAL_NOTE)
    assert "один раз" in mt.WEB_SITE_TEXT and "48" not in mt.WEB_SITE_TEXT


# ── подтверждение привязки Telegram в боте ─────────────────────────────────────

def test_link_decision_is_scoped_to_the_telegram_being_linked():
    conn = _Conn(val="confirmed")
    assert _run(_db(conn).decide_web_link_request("req1", tg_id=CHAT, approve=True)) == "confirmed"
    sql, args = conn.calls[0][1], conn.calls[0][2]
    assert "tg_id = $2" in sql and "status = 'pending'" in sql and "expires_at > NOW()" in sql
    assert args == ("req1", CHAT, "confirmed")

    # не pending: отдаём текущий статус, протухший pending — expired, чужой/нет — unknown
    assert _run(_db(_Conn(row={"status": "confirmed", "expired": False})).decide_web_link_request(
        "req1", tg_id=CHAT, approve=False)) == "confirmed"
    assert _run(_db(_Conn(row={"status": "pending", "expired": True})).decide_web_link_request(
        "req1", tg_id=CHAT, approve=True)) == "expired"
    assert _run(_db(_Conn(row=None)).decide_web_link_request("req1", tg_id=999, approve=True)) == "unknown"


def test_complete_link_request_only_from_confirmed():
    conn = _Conn(val="req1")
    assert _run(_db(conn).complete_web_link_request("req1")) is True
    assert "status = 'confirmed'" in conn.calls[0][1]
    assert _run(_db(_Conn(val=None)).complete_web_link_request("req1")) is False


class _LinkDB:
    def __init__(self, status):
        self.status = status
        self.decisions: list[tuple] = []
        self.events: list[tuple] = []

    async def decide_web_link_request(self, request_id, *, tg_id, approve):
        self.decisions.append((request_id, tg_id, approve))
        return self.status

    async def log_event(self, tg_id, event, detail=""):
        self.events.append((tg_id, event, detail))


class _CbMessage:
    def __init__(self):
        self.edits: list[str] = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _Cb:
    def __init__(self, data, user_id=CHAT):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = _CbMessage()
        self.answers: list = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


def _link_app(status):
    app = pub.BlastBotApp.__new__(pub.BlastBotApp)
    app.credits_db = _LinkDB(status)
    return app


def test_link_confirm_in_bot_marks_the_request_confirmed():
    app = _link_app("confirmed")
    cb = _Cb(f"{mt.WEB_LINK_CALLBACK_PREFIX}y:req1")
    _run(app._handle_web_link_decision(cb))
    assert app.credits_db.decisions == [("req1", CHAT, True)]
    assert cb.message.edits == [mt.WEB_LINK_CONFIRMED]
    assert (CHAT, "web_link_confirmed", "req1") in app.credits_db.events


def test_not_me_cancels_and_logs_a_security_event(caplog):
    import logging

    app = _link_app("rejected")
    cb = _Cb(f"{mt.WEB_LINK_CALLBACK_PREFIX}n:req1")
    with caplog.at_level(logging.WARNING, logger="tg_bot"):
        _run(app._handle_web_link_decision(cb))
    assert app.credits_db.decisions == [("req1", CHAT, False)]
    assert cb.message.edits == [mt.WEB_LINK_REJECTED]
    assert (CHAT, "web_link_rejected", "req1") in app.credits_db.events
    assert any("security web_link_rejected" in r.getMessage() for r in caplog.records)


def test_repeat_or_stale_link_buttons_do_not_flip_the_decision():
    app = _link_app("confirmed")
    cb = _Cb(f"{mt.WEB_LINK_CALLBACK_PREFIX}n:req1")  # уже подтверждено, жмут «Это не я»
    _run(app._handle_web_link_decision(cb))
    assert cb.answers == [mt.WEB_LINK_ALREADY] and cb.message.edits == []
    assert app.credits_db.events == []

    expired = _link_app("expired")
    cb = _Cb(f"{mt.WEB_LINK_CALLBACK_PREFIX}y:req1")
    _run(expired._handle_web_link_decision(cb))
    assert cb.message.edits == [mt.WEB_LINK_EXPIRED]

    broken = _link_app("confirmed")
    cb = _Cb(f"{mt.WEB_LINK_CALLBACK_PREFIX}maybe")
    _run(broken._handle_web_link_decision(cb))
    assert broken.credits_db.decisions == [] and cb.answers == [mt.WEB_LINK_EXPIRED]


def test_link_callback_data_fits_telegram_limit():
    # id запроса — secrets.token_urlsafe(16): 22 символа; лимит callback_data — 64 байта
    assert len(f"{mt.WEB_LINK_CALLBACK_PREFIX}y:{'x' * 22}".encode()) <= 64
