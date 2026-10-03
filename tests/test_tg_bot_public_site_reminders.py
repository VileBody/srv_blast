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
    def __init__(self, *, handoffs=(), unlimited=(), batches=None, idle=(), paid=(), tripwire=()):
        self.tripwire = list(tripwire)
        self.handoffs = list(handoffs)
        self.unlimited = list(unlimited)
        self.batches = batches or {}
        self.idle = list(idle)
        self.paid = set(paid)
        self.marked: set[tuple[int, str, str]] = set()
        self.last: dict[int, datetime] = {}
        self.tokens: list[tuple[int, str, dict]] = []
        self.events: list[tuple[int, str, str]] = []

    async def site_handoff_reminder_rows(self, *, chain_days, lookback_days):
        return self.handoffs

    async def track_unlimited_rows(self, *, active_hours):
        return [{**row, "batches": self.batches.get(row["tg_id"], [])} for row in self.unlimited]

    async def purge_expired_web_handoffs(self, older_than_days):
        self.purged = getattr(self, "purged", []) + [older_than_days]
        return 0

    async def idle_generation_rows(self):
        return self.idle

    async def tripwire_offer_rows(self):
        return self.tripwire

    async def last_reminder_at(self, tg_id):
        return self.last.get(tg_id)

    async def try_mark_reminder(self, tg_id, kind, ref=""):
        key = (tg_id, kind, ref)
        if key in self.marked:
            return False
        self.marked.add(key)
        self.last[tg_id] = DAY
        return True

    async def create_web_handoff(self, tg_id, kind, payload=None, *, ttl_seconds, single_use=False):
        self.tokens.append((tg_id, kind, payload or {}))
        self.single_use = getattr(self, "single_use", []) + [single_use]
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
    assert markup.inline_keyboard[0][0].url == "https://app.blast808.com/go#t=tok1"
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

    async def execute(self, sql, *args):
        self.sql.append(sql)


def test_tripwire_payment_lifts_limits_on_the_ordered_track():
    conn = _Conn("h", {"audio_hash": "h"})
    asyncio.run(CreditsDB.__new__(CreditsDB)._apply_tripwire(conn, tg_id=1, order_id="o1"))
    # трипваер — на любой трек: отдельная запись, а не правка бесплатного безлимита
    assert "INSERT INTO track_tripwire" in conn.sql[1]


def test_tripwire_order_without_track_is_an_error():
    with pytest.raises(ValueError):
        asyncio.run(CreditsDB.__new__(CreditsDB)._apply_tripwire(_Conn(None, None), tg_id=1, order_id="o1"))


def test_tripwire_offer_is_chased_within_its_day_even_right_after_another_reminder():
    opened = DAY - timedelta(hours=3)
    db = _DB(tripwire=[{"tg_id": 8, "created_at": opened, "age_s": 3 * 3600}])
    db.last[8] = DAY - timedelta(hours=1)  # недавнее напоминание не держит догон: у оффера свой срок
    bot = _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert bot.sent and "21 ч" in bot.sent[0][1] and "399" in bot.sent[0][1]
    assert db.single_use == [True]  # ссылки из напоминаний одноразовые


def test_reminder_track_link_is_marked_so_it_does_not_restart_the_chain():
    """Ссылка из напоминания — тот же трек, но source=reminder: следующая выборка
    по-прежнему держит возраст от развилки, а не от свежего токена."""
    db, bot = _DB(handoffs=[_handoff()]), _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert db.tokens[0][2]["source"] == "reminder"
    assert db.tokens[0][2]["audioS3Url"] == "s3://x"
    # исходная строка развилки не мутирована
    assert "source" not in db.handoffs[0]["payload"]


def test_unopened_chain_moves_to_next_step_from_the_fork_age():
    """Через 3 часа после развилки (а не после последнего напоминания) — шаг 2."""
    db, bot = _DB(handoffs=[_handoff()]), _Bot()
    app = _app(db, bot)
    asyncio.run(app._site_reminders_tick(DAY))
    db.handoffs = [_handoff(age_s=4 * 3600)]
    db.last = {}  # пауза между напоминаниями прошла
    assert asyncio.run(app._site_reminders_tick(DAY)) == 1
    assert (1, "unopened", f"{'a' * 16}:1") in db.marked


def test_remix_offer_reuses_the_fork_hash_for_the_same_file(tmp_path: Path, monkeypatch):
    audio = tmp_path / "p.mp3"
    audio.write_bytes(b"prepared")
    db, bot = _DB(), _Bot()
    app = _app(db, bot)
    st = ChatState(chat_id=9, batch_audio_s3_url="s3://raw/9/p.mp3", prepared_audio_local_path=str(audio),
                   web_handoff_audio_s3_url="s3://raw/9/p.mp3", web_handoff_audio_hash="forkhash",
                   web_handoff_prepared_path=str(audio), web_handoff_prepared_sig=app._file_signature(audio))
    monkeypatch.setattr(app, "_sha256_file", lambda path: (_ for _ in ()).throw(AssertionError("rehash")))
    asyncio.run(app._offer_site_remix_best_effort(bot=bot, st=st, source=app._site_remix_source(st)))
    assert db.tokens[0][2]["audioHash"] == "forkhash"


# ── ревью: один на тик, только бесплатным, последний шаг no_gen, чистка ссылок ──

def test_one_message_per_person_per_tick_and_tripwire_goes_first():
    """После ночи у человека наступили и догон трипваера, и «ссылку не открыл»: за тик —
    одно сообщение, и это трипваер (у него свой срок)."""
    db = _DB(handoffs=[_handoff(tg_id=8)], tripwire=[{"tg_id": 8, "created_at": DAY - timedelta(hours=13), "age_s": 13 * 3600}])
    bot = _Bot()
    assert asyncio.run(_app(db, bot)._site_reminders_tick(DAY)) == 1
    assert len(bot.sent) == 1 and "399" in bot.sent[0][1]


def test_tripwire_steps_missed_at_night_send_only_the_latest():
    """Ночью наступили шаги 2 ч и 12 ч — утром уходит только последний, без пачки."""
    opened = DAY - timedelta(hours=13)
    db = _DB(tripwire=[{"tg_id": 8, "created_at": opened, "age_s": 13 * 3600}])
    bot = _Bot()
    app = _app(db, bot)
    asyncio.run(app._site_reminders_tick(DAY))
    asyncio.run(app._site_reminders_tick(DAY + timedelta(minutes=10)))
    assert len(bot.sent) == 1
    assert {ref for (_, kind, ref) in db.marked if kind == "tripwire"} == {f"{opened.isoformat()}:1"}


def test_paying_people_get_no_site_reminders_of_any_kind():
    """Платящим не пишем ни по одному поводу (общий гейт _is_free_funnel_chat)."""
    unlocked = DAY - timedelta(hours=10)
    batch = {"job_id": "j9", "videos": 5, "mode": "free", "created_at": DAY - timedelta(hours=5)}
    db = _DB(
        handoffs=[_handoff(tg_id=9), _handoff(tg_id=10, redeem_count=1, since_open_s=4 * 3600)],
        unlimited=[{"tg_id": 11, "audio_hash": "h", "unlocked_at": unlocked}], batches={11: [batch]},
        tripwire=[{"tg_id": 12, "created_at": DAY - timedelta(hours=3), "age_s": 3 * 3600}],
        paid={9, 10, 11, 12},
    )
    bot = _Bot()
    assert asyncio.run(_app(db, bot)._site_reminders_tick(DAY)) == 0 and bot.sent == []


def test_no_gen_last_step_lives_from_the_open_not_from_the_fork():
    """Открыл ссылку через 2 суток после развилки: шаг «+3 дня от открытия» всё ещё приходит
    (окно выборки считается и от открытия)."""
    db = _DB(handoffs=[_handoff(redeem_count=1, age_s=5 * 86400, since_open_s=3 * 86400 + 60)])
    bot = _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert bot.sent and bot.sent[0][1] == mt.REMIND_SITE_NO_GEN
    assert (1, "no_gen", f"{'a' * 16}:2") in db.marked


def test_idle_reminder_goes_only_to_funnel_members():
    """Затихший участник воронки (выборка уже отфильтрована SQL) — получает; бесплатный
    вне воронки в выборку не попадает вовсе (см. test_recharge_and_idle_rows_query_only_what_they_need)."""
    db = _DB(idle=[{"tg_id": 5, "idle_s": 4 * 86400, "last_ref": "202609290000"}])
    bot = _Bot()
    asyncio.run(_app(db, bot)._site_reminders_tick(DAY))
    assert [chat for chat, *_ in bot.sent] == [5]


def test_expired_handoff_links_are_purged_once_a_day():
    db, bot = _DB(), _Bot()
    app = _app(db, bot)
    asyncio.run(app._site_reminders_tick(NIGHT))  # чистка не ждёт дня
    asyncio.run(app._site_reminders_tick(NIGHT + timedelta(minutes=10)))
    assert db.purged == [sr.HANDOFF_PURGE_DAYS]
    asyncio.run(app._site_reminders_tick(NIGHT + timedelta(hours=25)))
    assert db.purged == [sr.HANDOFF_PURGE_DAYS, sr.HANDOFF_PURGE_DAYS]
    assert sr.HANDOFF_PURGE_DAYS > sr.FORK_LOOKBACK_DAYS
