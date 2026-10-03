# -*- coding: utf-8 -*-
"""Трипваер 399 ₽ (track399): не «платящий», не трогает стадию бота, комиссия партнёра 20%.

docs/BOT_TO_WEB_FLOW.md, раздел 5. SQL проверяется на sqlite: те же выражения
(подзапрос по пакету заказа, ROW_NUMBER в CASE) с подменой постгресовых плейсхолдеров.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.tg_bot_public import credits_db as cdb
from services.tg_bot_public.marketing_texts import TRIPWIRE_PAID_TEXT
from services.tg_bot_public.track_unlimited import TRIPWIRE_PACKAGE
from tests.test_payments_tail_fixes import _FakeCreditsDBNotify, _FakeStateStore, _FakeTBankClient


def _sqlite(sql: str) -> str:
    """Постгресовый SQL → sqlite: $N → ?N, ANY(массив) → json_each, касты убираем."""
    sql = sql.replace("= ANY($2::TEXT[])", "IN (SELECT value FROM json_each(?2))")
    sql = re.sub(r"::[A-Z]+", "", sql)
    return re.sub(r"\$(\d+)", r"?\1", sql)


class _SqliteConn:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    async def fetchval(self, sql, *args):
        import json

        args = [json.dumps(a) if isinstance(a, list) else a for a in args]
        row = self.db.execute(_sqlite(sql), args).fetchone()
        return None if row is None else row[0]


class _Pool:
    def __init__(self, conn) -> None:
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _payments_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE payments (order_id TEXT, tg_id INTEGER, amount_rub INTEGER, package TEXT, created_at TEXT)")
    db.execute("CREATE TABLE transactions (tg_id INTEGER, amount INTEGER, reason TEXT, context_order_id TEXT)")
    return db


def test_tripwire_payment_does_not_make_a_paying_user():
    """Купивший только трипваер — бесплатный: воронка и питч остаются. Пакет любой
    другой (и ручная активация) — платящий. Работает и для уже записанных строк."""
    db = _payments_db()
    db.execute("INSERT INTO payments VALUES ('tw-1', 1, 399, ?, '2026-10-01')", (TRIPWIRE_PACKAGE,))
    db.execute("INSERT INTO transactions VALUES (1, 0, 'payment', 'tw-1')")
    db.execute("INSERT INTO payments VALUES ('bl-1', 2, 1990, 'Бласт', '2026-10-01')")
    db.execute("INSERT INTO transactions VALUES (2, 100, 'payment', 'bl-1')")
    db.execute("INSERT INTO transactions VALUES (3, 15, 'admin_activate', '')")
    credits = cdb.CreditsDB.__new__(cdb.CreditsDB)
    credits._pool_or_fail = lambda: _Pool(_SqliteConn(db))

    assert asyncio.run(credits.has_paid(1)) is False
    assert asyncio.run(credits.has_paid(2)) is True
    assert asyncio.run(credits.has_paid(3)) is True
    # купил и трипваер, и Бласт — платящий
    db.execute("INSERT INTO payments VALUES ('bl-2', 1, 1990, 'Бласт', '2026-10-02')")
    db.execute("INSERT INTO transactions VALUES (1, 100, 'payment', 'bl-2')")
    assert asyncio.run(credits.has_paid(1)) is True


def test_partner_commission_counts_the_tripwire_at_20_and_never_as_first():
    """Трипваер — всегда 20%; 50% получает первая НЕ-трипваерная оплата, даже если
    трипваер был раньше неё."""
    db = _payments_db()
    rows = [
        ("o1", 1, 399, TRIPWIRE_PACKAGE, "2026-10-01"),  # трипваер первым
        ("o2", 1, 1990, "Бласт", "2026-10-02"),            # первая «настоящая» → 50%
        ("o3", 1, 1990, "Бласт", "2026-11-02"),            # повторная → 20%
        ("o4", 2, 1990, "Бласт", "2026-10-03"),            # первая → 50%
        ("o5", 2, 399, TRIPWIRE_PACKAGE, "2026-10-04"),  # трипваер после → 20%
    ]
    db.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?)", rows)
    ranked = db.execute(f"SELECT p.order_id, {cdb._PARTNER_RANK_SQL} FROM payments p ORDER BY p.order_id").fetchall()
    rn = dict(ranked)
    assert rn == {"o1": 0, "o2": 1, "o3": 2, "o4": 1, "o5": 0}
    earned = sum(amount * (0.5 if rn[o] == 1 else 0.2) for o, _tg, amount, _pkg, _at in rows)
    assert round(earned) == round(1990 * 0.5 * 2 + (399 + 1990 + 399) * 0.2)


def _notify_app(package: str):
    credits_db = _FakeCreditsDBNotify()
    credits_db.payment["package"] = package
    original = credits_db.confirm_payment_once

    async def confirm(order_id, payment_id, *, actor):
        if package != TRIPWIRE_PACKAGE:
            return await original(order_id, payment_id, actor=actor)
        credits_db.payment["status"] = "CONFIRMED"
        return {**credits_db.payment, "applied": True, "credits_added": 0, "tracks_added": 0}

    credits_db.confirm_payment_once = confirm
    state_store = _FakeStateStore()
    sent: list[tuple] = []

    async def send_message(chat_id, text, **kw):
        sent.append((chat_id, text))

    from services.tg_bot_public.admin_panel import build_app

    app = build_app(
        credits_db=credits_db,
        state_store=state_store,
        settings=SimpleNamespace(admin_panel_password="secret", tg_bot_username="", manager_chat_id=0,
                                 admin_panel_port=18080, season_redis_prefix="test:season"),
        tbank_client=_FakeTBankClient(),
        bot_ref=[SimpleNamespace(send_message=send_message)],
    )
    resp = TestClient(app).post(
        "/api/tbank/notify", json={"OrderId": "ord-1", "Status": "CONFIRMED", "PaymentId": "pay-1", "Token": "ok"},
    )
    assert resp.status_code == 200 and resp.text == "OK"
    return state_store, sent


def test_tripwire_webhook_leaves_the_bot_stage_alone():
    """Трипваер купили на сайте: стадию бота не сбрасываем (можно оборвать сборку там),
    человеку — отдельное сообщение про снятые лимиты."""
    state_store, sent = _notify_app(TRIPWIRE_PACKAGE)
    assert state_store.reset_calls == []
    assert sent == [(777, TRIPWIRE_PAID_TEXT)]


def test_regular_package_webhook_still_resets_the_bot_stage():
    state_store, _ = _notify_app("Триал")
    assert state_store.reset_calls == [777]
