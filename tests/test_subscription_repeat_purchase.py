"""Repeat subscription purchases and admin track adjustments against real Postgres.

The behaviour lives in SQL (GREATEST over timestamps, advisory lock, row
locks), so these run on an embedded server via `pgserver`; the module is
skipped where it is not installed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pgserver = pytest.importorskip("pgserver")
pytest.importorskip("asyncpg")

from services.tg_bot_public.credits_db import CreditsDB  # noqa: E402

TG = 5001


@pytest.fixture(scope="module")
def pg_uri(tmp_path_factory):
    srv = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield srv.get_uri()
    srv.cleanup()


@pytest.fixture
def run(pg_uri):
    """Run a coroutine against a freshly-reset schema."""

    async def _reset(db: CreditsDB) -> None:
        async with db._pool_or_fail().acquire() as conn:
            await conn.execute(
                "TRUNCATE users, payments, subscriptions, transactions, track_transactions, activity_log"
            )

    def _run(fn):
        async def _main():
            db = CreditsDB(pg_uri)
            await db.init()
            try:
                await _reset(db)
                return await fn(db)
            finally:
                await db.close()

        return asyncio.run(_main())

    return _run


async def _paid_order(db: CreditsDB, order_id: str, *, rebill: str = "rb-1", created_at: datetime | None = None) -> None:
    await db.create_recurrent_payment(order_id, TG, 1990, "Бласт")
    async with db._pool_or_fail().acquire() as conn:
        await conn.execute(
            "UPDATE payments SET status = 'CONFIRMED', rebill_id = $2, "
            "created_at = COALESCE($3::timestamp, created_at) WHERE order_id = $1",
            order_id, rebill, created_at,
        )


async def _subs(db: CreditsDB) -> list[dict]:
    async with db._pool_or_fail().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, status, rebill_id, next_charge_at, source_order_id FROM subscriptions "
            "WHERE tg_id = $1 ORDER BY id",
            TG,
        )
    return [dict(r) for r in rows]


def _now() -> datetime:
    # Columns are naive TIMESTAMP in UTC (the pool sets TIME ZONE 'UTC').
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_first_purchase_creates_subscription_a_month_out(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, f"{TG}-Бласт-subaaaa0001")
        res = await db.apply_subscription_purchase(f"{TG}-Бласт-subaaaa0001", "rb-1")
        return res, await _subs(db)

    res, subs = run(body)
    assert res["action"] == "created"
    assert len(subs) == 1 and subs[0]["status"] == "active"
    assert subs[0]["source_order_id"] == f"{TG}-Бласт-subaaaa0001"
    assert abs(subs[0]["next_charge_at"] - (_now() + timedelta(days=30))) < timedelta(days=2)


def test_repeat_purchase_extends_from_current_due_date_and_is_idempotent(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "first-sub1")
        await db.apply_subscription_purchase("first-sub1", "rb-1")
        before = (await _subs(db))[0]["next_charge_at"]
        await _paid_order(db, "second-sub2", rebill="rb-2")
        res = await db.apply_subscription_purchase("second-sub2", "rb-2")
        # Webhook replay + poll loop for the same order.
        again = await db.apply_subscription_purchase("second-sub2", "rb-2")
        return before, res, again, await _subs(db)

    before, res, again, subs = run(body)
    assert res["action"] == "extended"
    assert again["action"] == "skipped"
    assert len(subs) == 1, "no second subscription row — the old one would rebill too"
    sub = subs[0]
    assert sub["rebill_id"] == "rb-2"
    # One month past the old due date, not a month from today.
    assert timedelta(days=27) < sub["next_charge_at"] - before < timedelta(days=32)
    assert sub["next_charge_at"] - _now() > timedelta(days=55)


def test_replayed_first_order_does_not_extend(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "first-sub1")
        await db.apply_subscription_purchase("first-sub1", "rb-1")
        # The flag is lost (row predates the column): source_order_id still guards it.
        async with db._pool_or_fail().acquire() as conn:
            await conn.execute("UPDATE payments SET subscription_applied_at = NULL")
        return await db.apply_subscription_purchase("first-sub1", "rb-1"), await _subs(db)

    res, subs = run(body)
    assert res["action"] == "skipped"
    assert abs(subs[0]["next_charge_at"] - (_now() + timedelta(days=30))) < timedelta(days=2)


def test_legacy_subscription_without_source_is_not_extended_by_its_own_order(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "legacy-sub1", created_at=_now() - timedelta(minutes=5))
        await db.create_subscription(TG, "Бласт", "rb-1", 1990)  # old path, no source
        return await db.apply_subscription_purchase("legacy-sub1", "rb-1"), await _subs(db)

    res, subs = run(body)
    assert res["action"] == "skipped"
    assert len(subs) == 1


def test_rebill_order_never_extends(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "first-sub1")
        await db.apply_subscription_purchase("first-sub1", "rb-1")
        before = (await _subs(db))[0]["next_charge_at"]
        # Marked by the charge loop …
        await _paid_order(db, f"{TG}-Бласт-sub-0123abcd")
        await db.mark_subscription_rebill_order(f"{TG}-Бласт-sub-0123abcd")
        a = await db.apply_subscription_purchase(f"{TG}-Бласт-sub-0123abcd", "rb-1")
        # … and a legacy/unmarked rebill recognised by its order id.
        await _paid_order(db, f"{TG}-Бласт-sub-deadbeef")
        b = await db.apply_subscription_purchase(f"{TG}-Бласт-sub-deadbeef", "rb-1")
        return before, a, b, await _subs(db)

    before, a, b, subs = run(body)
    assert a["action"] == b["action"] == "skipped"
    assert subs[0]["next_charge_at"] == before


def test_resubscribe_during_cancelled_paid_period_keeps_remaining_time(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "first-sub1")
        await db.apply_subscription_purchase("first-sub1", "rb-1")
        paid_until = (await _subs(db))[0]["next_charge_at"]
        await db.cancel_subscription(TG)
        await _paid_order(db, "again-sub2", rebill="rb-2")
        res = await db.apply_subscription_purchase("again-sub2", "rb-2")
        return paid_until, res, await _subs(db)

    paid_until, res, subs = run(body)
    assert res["action"] == "created"
    new = subs[-1]
    assert new["status"] == "active"
    assert timedelta(days=27) < new["next_charge_at"] - paid_until < timedelta(days=32)


def test_concurrent_confirmations_of_one_order_apply_once(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "first-sub1")
        await db.apply_subscription_purchase("first-sub1", "rb-1")
        await _paid_order(db, "second-sub2", rebill="rb-2")
        results = await asyncio.gather(*[
            db.apply_subscription_purchase("second-sub2", "rb-2") for _ in range(5)
        ])
        return [r["action"] for r in results], await _subs(db)

    actions, subs = run(body)
    assert sorted(actions) == ["extended", "skipped", "skipped", "skipped", "skipped"]
    assert subs[0]["next_charge_at"] - _now() > timedelta(days=55)


def test_charge_success_counts_from_later_of_now_and_due_date(run) -> None:
    async def body(db: CreditsDB):
        await _paid_order(db, "first-sub1")
        await db.apply_subscription_purchase("first-sub1", "rb-1")
        sub = (await _subs(db))[0]
        await db.subscription_charge_success(sub["id"])  # early manual charge
        return sub["next_charge_at"], (await _subs(db))[0]["next_charge_at"]

    before, after = run(body)
    assert timedelta(days=27) < after - before < timedelta(days=32)


def test_track_adjustment_clamps_at_zero_and_is_ledgered(run) -> None:
    async def body(db: CreditsDB):
        after_add = await db.add_track_credits(TG, 3, "support", "note", actor="admin", order_id="ord-1")
        after_remove = await db.add_track_credits(TG, -10, "revoke", actor="admin")
        return after_add, after_remove, await db.get_track_transactions(TG), await db.get_balance(TG)

    after_add, after_remove, ledger, video_balance = run(body)
    assert (after_add, after_remove) == (3, 0)
    assert [row["amount"] for row in ledger] == [-3, 3]
    assert ledger[1]["actor"] == "admin" and ledger[1]["order_id"] == "ord-1"
    assert video_balance == 0
