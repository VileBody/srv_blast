from __future__ import annotations

import asyncio
import sys
import types

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")

    class _DummyConnection: ...
    class _DummyPool: ...

    async def _dummy_create_pool(*args, **kwargs):
        raise RuntimeError("stub asyncpg.create_pool")

    asyncpg_stub.Connection = _DummyConnection
    asyncpg_stub.Pool = _DummyPool
    asyncpg_stub.create_pool = _dummy_create_pool
    sys.modules["asyncpg"] = asyncpg_stub

from services.tg_bot_public.credits_db import CreditsDB


class _FakeConn:
    def __init__(self) -> None:
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query, args))
        return 3210

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if "users_new" in query:
            return {
                "users_new": 3,
                "starts_users": 4,
                "generation_started_users": 5,
                "generation_done_users": 6,
                "generation_failed_users": 2,
                "purchase_intent_users": 1,
                "paid_orders": 7,
                "revenue_rub": 12345,
            }
        if "confirmed_orders" in query and "authorized_orders" in query:
            return {
                "confirmed_orders": 7,
                "confirmed_revenue_rub": 12345,
                "authorized_orders": 2,
                "authorized_revenue_rub": 3980,
            }
        if "confirmed_revenue_rub" in query and "authorized_revenue_rub" in query and "WHERE tg_id = ANY" in query:
            return {
                "confirmed_revenue_rub": 5670,
                "authorized_revenue_rub": 1990,
            }
        return {"orders_count": 7, "revenue_rub": 12345}


class _AcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


def test_revenue_for_users_uses_amount_rub_and_confirmed_status() -> None:
    db = CreditsDB("postgresql://example")
    conn = _FakeConn()
    db._pool = _FakePool(conn)  # type: ignore[attr-defined]

    out = asyncio.run(db.revenue_for_users([10, 11]))

    assert out == 3210
    assert conn.fetchval_calls
    query, args = conn.fetchval_calls[-1]
    assert "SUM(amount_rub)" in query
    assert "status = 'CONFIRMED'" in query
    assert list(args[0]) == [10, 11]


def test_confirmed_payments_summary_returns_orders_and_revenue() -> None:
    db = CreditsDB("postgresql://example")
    conn = _FakeConn()
    db._pool = _FakePool(conn)  # type: ignore[attr-defined]

    out = asyncio.run(db.confirmed_payments_summary())

    assert out == {"orders_count": 7, "revenue_rub": 12345}
    assert conn.fetchrow_calls
    query, _ = conn.fetchrow_calls[-1]
    assert "FROM payments" in query
    assert "status = 'CONFIRMED'" in query


def test_period_stats_uses_date_window_and_confirmed_payments() -> None:
    db = CreditsDB("postgresql://example")
    conn = _FakeConn()
    db._pool = _FakePool(conn)  # type: ignore[attr-defined]

    out = asyncio.run(db.period_stats(7))

    assert out["days"] == 7
    assert out["users_new"] == 3
    assert out["starts_users"] == 4
    assert out["generation_started_users"] == 5
    assert out["generation_done_users"] == 6
    assert out["generation_failed_users"] == 2
    assert out["purchase_intent_users"] == 1
    assert out["paid_orders"] == 7
    assert out["revenue_rub"] == 12345
    query, args = conn.fetchrow_calls[-1]
    assert "INTERVAL '1 day'" in query
    assert "p.status = 'CONFIRMED'" in query
    assert int(args[0]) == 7


def test_payments_status_summary_includes_confirmed_and_authorized() -> None:
    db = CreditsDB("postgresql://example")
    conn = _FakeConn()
    db._pool = _FakePool(conn)  # type: ignore[attr-defined]

    out = asyncio.run(db.payments_status_summary())

    assert out == {
        "confirmed_orders": 7,
        "confirmed_revenue_rub": 12345,
        "authorized_orders": 2,
        "authorized_revenue_rub": 3980,
        "visible_orders": 9,
        "visible_revenue_rub": 16325,
    }
    query, _ = conn.fetchrow_calls[-1]
    assert "status = 'CONFIRMED'" in query
    assert "status = 'AUTHORIZED'" in query


def test_revenue_breakdown_for_users_includes_authorized() -> None:
    db = CreditsDB("postgresql://example")
    conn = _FakeConn()
    db._pool = _FakePool(conn)  # type: ignore[attr-defined]

    out = asyncio.run(db.revenue_breakdown_for_users([10, 11]))

    assert out == {
        "confirmed_revenue_rub": 5670,
        "authorized_revenue_rub": 1990,
        "visible_revenue_rub": 7660,
    }
    query, args = conn.fetchrow_calls[-1]
    assert "WHERE tg_id = ANY($1::BIGINT[])" in query
    assert list(args[0]) == [10, 11]
