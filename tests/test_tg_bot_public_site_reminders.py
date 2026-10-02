# -*- coding: utf-8 -*-
"""Публичный бот: напоминания про сайт, «Докрутить на сайте», трипваер при оплате."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.tg_bot_public import app as pub
from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public import site_reminders as sr
from services.tg_bot_public.credits_db import CreditsDB
from services.tg_bot_public.state_store import ChatState

DAY = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)      # 12:00 МСК
NIGHT = datetime(2026, 10, 3, 21, 0, tzinfo=timezone.utc)   # 00:00 МСК


def test_due_step_takes_the_latest_reached_step():
    assert sr.due_step(timedelta(minutes=10), sr.UNOPENED_STEPS) is None
    assert sr.due_step(timedelta(minutes=40), sr.UNOPENED_STEPS) == 0
    assert sr.due_step(timedelta(hours=5), sr.UNOPENED_STEPS) == 1
    assert sr.due_step(timedelta(days=2), sr.UNOPENED_STEPS) == 2


def test_quiet_hours_and_daily_gap():
    assert sr.is_daytime(DAY) and not sr.is_daytime(NIGHT)
    assert sr.can_send(DAY, None)
    assert not sr.can_send(DAY, DAY - timedelta(hours=2))
    assert sr.can_send(DAY, DAY - timedelta(hours=21))
    assert not sr.can_send(NIGHT, None)


class _Bot:
    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))


class _DB:
    def __init__(self, *, handoffs=(), unlimited=(), batches=None, idle=(), paid=()):
        self.handoffs = list(handoffs)
        self.unlimited = list(unlimited)
        self.batches = batches or {}
        self.idle = list(idle)
        self.paid = set(paid)
        self.marked: set[tuple[int, str, str]] = set()
        self.last: dict[int, datetime] = {}
        self.tokens: list[tuple[int, str, dict]] = []
        self.events: list[tuple[int, str, str]] = []

    async def site_handoff_reminder_rows(self):
        return self.handoffs

    async def track_unlimited_rows(self):
        return self.unlimited

    async def list_track_batches(self, tg_id, audio_hash):
        return self.batches.get(tg_id, [])

    async def idle_generation_rows(self):
        return self.idle

    async def last_reminder_at(self, tg_id):
        return self.last.get(tg_id)

    async def try_mark_reminder(self, tg_id, kind, ref=""):
        key = (tg_id, kind, ref)
        if key in self.marked:
            return False
        self.marked.add(key)
        self.last[tg_id] = DAY
        return True

    async def create_web_handoff(self, tg_id, kind, payload=None, *, ttl_seconds):
        self.tokens.append((tg_id, kind, payload or {}))
        return f"tok{len(self.tokens)}"

    async def log_event(self, tg_id, event, detail=""):
        self.events.append((tg_id, event, detail))

    async def has_paid(self, tg_id):
        return tg_id in self.paid


def _app(db: _DB, bot: _Bot):
    app = pub.BlastBotApp.__new__(pub.BlastBotApp)
    app.credits_db = db
    app.settings = SimpleNamespace(web_app_url="https://app.blast808.com", web_handoff_ttl_s=3600, web_handoff_enabled=True, tg_force_free_funnel_chat_ids=frozenset())
    app._require_bot = lambda: bot
    return app


def _handoff(**over):
    row = {"tg_id": 1, "token_hash": "a" * 64, "payload": {"audioS3Url": "s3://x", "profile": {"name": "Л"}},
           "redeem_count": 0, "age_s": 40 * 60, "since_open_s": None, "alive": True,
           "stayed_in_bot": False, "generated_on_site": False}
    row.update(over)
    return row


def test_unopened_link_gets_a_fresh_track_link_once():
    db, bot = _DB(handoffs=[_handoff()]), _Bot()
    app = _app(db, bot)
    assert asyncio.run(app._site_reminders_tick(DAY)) == 1
    chat, text, markup = bot.sent[0]
    assert chat == 1 and text == mt.REMIND_SITE_UNOPENED
    assert markup.inline_keyboard[0][0].url == "https://app.blast808.com/go/tok1"
    assert db.tokens[0][1] == "track" and db.tokens[0][2]["audioS3Url"] == "s3://x"
    assert asyncio.run(app._site_reminders_tick(DAY)) == 0  # тот же шаг второй раз не шлём


def test_opened_but_no_generation_gets_a_site_link():
    db, bot = _DB(handoffs=[_handoff(redeem_count=1, since_open_s=4 * 3600)]), _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert bot.sent[0][1] == mt.REMIND_SITE_NO_GEN and db.tokens[0][1] == "site"


def test_no_reminder_if_they_stayed_in_bot_or_generated_or_at_night():
    for row in (_handoff(stayed_in_bot=True), _handoff(generated_on_site=True)):
        db, bot = _DB(handoffs=[row]), _Bot()
        asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
        assert bot.sent == []
    db, bot = _DB(handoffs=[_handoff()]), _Bot()
    assert asyncio.run(_app(db, bot)._site_reminders_tick(NIGHT)) == 0


def test_recharge_is_announced_once_after_the_cooldown():
    unlocked = DAY - timedelta(hours=10)
    batch = {"job_id": "j9", "videos": 5, "mode": "free", "created_at": DAY - timedelta(hours=5)}
    db = _DB(unlimited=[{"tg_id": 3, "audio_hash": "h", "unlocked_at": unlocked}], batches={3: [batch]})
    bot = _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert bot.sent and bot.sent[0][1] == mt.REMIND_SITE_RECHARGE.format(n=5)
    assert (3, "recharge", "j9") in db.marked


def test_still_cooling_down_sends_nothing():
    batch = {"job_id": "j9", "videos": 5, "mode": "free", "created_at": DAY - timedelta(hours=1)}
    db = _DB(unlimited=[{"tg_id": 3, "audio_hash": "h", "unlocked_at": DAY - timedelta(hours=2)}], batches={3: [batch]})
    bot = _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert bot.sent == []


def test_idle_reminder_skips_paying_clients():
    db = _DB(idle=[{"tg_id": 5, "idle_s": 4 * 86400, "last_ref": "202609290000"},
                   {"tg_id": 6, "idle_s": 4 * 86400, "last_ref": "202609290000"}], paid={6})
    bot = _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert [chat for chat, *_ in bot.sent] == [5]


def test_remix_offer_carries_window_and_lyrics(tmp_path: Path):
    audio = tmp_path / "p.mp3"
    audio.write_bytes(b"prepared")
    db, bot = _DB(), _Bot()
    app = _app(db, bot)
    st = ChatState(chat_id=9, batch_audio_s3_url="s3://raw/9/p.mp3", prepared_audio_local_path=str(audio),
                   user_clip_start_sec=12.5, user_clip_end_sec=26.0, target_fragment="строки", pending_audio_filename="Song.mp3")
    source = app._site_remix_source(st)
    asyncio.run(app._offer_site_remix_best_effort(bot=bot, st=st, source=source))
    tg, kind, payload = db.tokens[0]
    assert kind == "remix" and payload["draft"] == {"clipStart": 12.5, "clipEnd": 26.0, "lyrics": "строки"}
    assert bot.sent[0][1] == mt.WEB_REMIX_TEXT
    assert bot.sent[0][2].inline_keyboard[0][0].text == mt.BTN_WEB_REMIX


def test_remix_offer_is_skipped_without_the_track():
    db, bot = _DB(), _Bot()
    app = _app(db, bot)
    assert app._site_remix_source(ChatState(chat_id=9)) is None
    asyncio.run(app._offer_site_remix_best_effort(bot=bot, st=ChatState(chat_id=9), source=None))
    assert bot.sent == [] and db.tokens == []


class _Conn:
    def __init__(self, audio_hash, row_after):
        self.audio_hash = audio_hash
        self.row_after = row_after
        self.sql: list[str] = []

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return self.audio_hash

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return self.row_after


def test_tripwire_payment_lifts_limits_on_the_ordered_track():
    conn = _Conn("h", {"audio_hash": "h"})
    asyncio.run(CreditsDB.__new__(CreditsDB)._apply_tripwire(conn, tg_id=1, order_id="o1"))
    assert "tripwire_paid_at" in conn.sql[1]


def test_tripwire_order_without_track_is_an_error():
    with pytest.raises(ValueError):
        asyncio.run(CreditsDB.__new__(CreditsDB)._apply_tripwire(_Conn(None, None), tg_id=1, order_id="o1"))
