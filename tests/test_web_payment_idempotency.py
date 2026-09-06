from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ["MODE"] = "dev"
os.environ["BLAST_BACKEND_MODE"] = "mock"
os.environ["APP_URL"] = "http://localhost:5173"
os.environ["BLAST_SESSION_SECRET"] = "test-session-secret"
os.environ["BLAST_CORS_ORIGINS"] = "http://localhost:5173"

from services.tg_bot_public.credits_db import CreditsDB
from web_app.backend.app.billing_backend import BillingBackend, PaymentInitError


class _FakePaymentDB:
    def __init__(self) -> None:
        self.intents: dict[tuple[int, str], dict[str, Any]] = {}
        self.marked: list[tuple[str, str, str]] = []

    async def claim_web_payment_intent(self, **values: Any) -> tuple[dict[str, Any], bool]:
        key = (int(values["tg_id"]), str(values["idempotency_key"]))
        existing = self.intents.get(key)
        if existing is not None:
            return dict(existing), False
        row = {
            "order_id": str(values["order_id"]),
            "tg_id": int(values["tg_id"]),
            "amount_rub": int(values["amount_rub"]),
            "package": str(values["package"]),
            "is_recurrent": bool(values["recurrent"]),
            "idempotency_key": str(values["idempotency_key"]),
            "status": "INIT_IN_PROGRESS",
            "payment_url": "",
            "init_error": "",
        }
        self.intents[key] = row
        return dict(row), True

    async def complete_web_payment_init(self, order_id: str, payment_url: str) -> bool:
        for row in self.intents.values():
            if row["order_id"] == order_id and row["status"] == "INIT_IN_PROGRESS":
                row["status"] = "NEW"
                row["payment_url"] = str(payment_url)
                return True
        return False

    async def mark_web_payment_init(self, order_id: str, status: str, error: str = "") -> bool:
        self.marked.append((str(order_id), str(status), str(error)))
        for row in self.intents.values():
            if row["order_id"] == order_id and row["status"] == "INIT_IN_PROGRESS":
                row["status"] = str(status)
                row["init_error"] = str(error)
                return True
        return False


class _FakeTBank:
    def __init__(self, result: str | None = "https://pay.example/order") -> None:
        self.result = result
        self.error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    async def create_payment(self, **values: Any) -> str | None:
        self.calls.append(dict(values))
        if self.error is not None:
            raise self.error
        return self.result


def _backend(db: _FakePaymentDB, tbank: _FakeTBank) -> BillingBackend:
    backend = BillingBackend.__new__(BillingBackend)
    backend._db = db
    backend._tbank = tbank
    return backend


def _create(backend: BillingBackend, *, package: str = "GLOW", key: str = "attempt-123456789012345678901234"):
    return asyncio.run(
        backend.create_order(
            tg_id=777,
            package_type=package,
            email="user@example.com",
            recurrent_accepted=package == "BLAST",
            idempotency_key=key,
        )
    )


def test_web_payment_retry_reuses_saved_url_without_second_init() -> None:
    db = _FakePaymentDB()
    tbank = _FakeTBank()
    backend = _backend(db, tbank)

    first = _create(backend)
    second = _create(backend)

    assert second == first
    assert len(tbank.calls) == 1
    assert len(first["orderId"]) <= 50


def test_web_payment_key_cannot_be_reused_for_another_plan() -> None:
    db = _FakePaymentDB()
    tbank = _FakeTBank()
    backend = _backend(db, tbank)
    _create(backend, package="GLOW")

    with pytest.raises(PaymentInitError) as caught:
        _create(backend, package="IMPULSE")

    assert caught.value.code == "payment_idempotency_conflict"
    assert len(tbank.calls) == 1


def test_definite_init_failure_is_recorded_and_never_retried_implicitly() -> None:
    db = _FakePaymentDB()
    tbank = _FakeTBank(result=None)
    backend = _backend(db, tbank)

    with pytest.raises(PaymentInitError) as first:
        _create(backend)
    with pytest.raises(PaymentInitError) as replay:
        _create(backend)

    assert first.value.code == "payment_init_failed"
    assert replay.value.code == "payment_init_failed"
    assert len(tbank.calls) == 1
    assert db.marked[0][1] == "INIT_FAILED"


def test_ambiguous_init_failure_is_quarantined_without_second_order() -> None:
    db = _FakePaymentDB()
    tbank = _FakeTBank()
    tbank.error = TimeoutError("response lost")
    backend = _backend(db, tbank)

    with pytest.raises(PaymentInitError) as first:
        _create(backend)
    with pytest.raises(PaymentInitError) as replay:
        _create(backend)

    assert first.value.code == "payment_init_unknown"
    assert replay.value.code == "payment_init_unknown"
    assert len(tbank.calls) == 1
    assert db.marked[0][1] == "INIT_UNKNOWN"


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _ConfirmConn:
    def __init__(self) -> None:
        self.payments = {
            "order-1": {
                "id": 1,
                "order_id": "order-1",
                "tg_id": 777,
                "amount_rub": 7990,
                "package": "Глоу",
                "status": "NEW",
                "payment_id": "",
                "rebill_id": "",
                "is_recurrent": False,
                "payment_url": "https://pay.example/1",
                "idempotency_key": "key-1",
                "init_error": "",
                "created_at": None,
                "updated_at": None,
            },
            "order-2": {
                "id": 2,
                "order_id": "order-2",
                "tg_id": 777,
                "amount_rub": 7990,
                "package": "Глоу",
                "status": "NEW",
                "payment_id": "",
                "rebill_id": "",
                "is_recurrent": False,
                "payment_url": "https://pay.example/2",
                "idempotency_key": "key-2",
                "init_error": "",
                "created_at": None,
                "updated_at": None,
            },
        }
        self.users: dict[int, dict[str, int]] = {}
        self.transactions: list[dict[str, Any]] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM payments WHERE order_id = $1 FOR UPDATE" in query:
            row = self.payments.get(str(args[0]))
            return dict(row) if row else None
        if query.startswith("SELECT credits, track_credits FROM users"):
            return dict(self.users[int(args[0])])
        if query.startswith("UPDATE users SET credits = credits + $1"):
            credits, tracks, tg_id = int(args[0]), int(args[1]), int(args[2])
            user = self.users[tg_id]
            user["credits"] += credits
            user["track_credits"] += tracks
            return dict(user)
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetchval(self, query: str, *args: Any) -> int:
        if "SELECT COUNT(*) FROM payments" in query:
            tg_id = int(args[0])
            return sum(
                1
                for row in self.payments.values()
                if int(row["tg_id"]) == tg_id and str(row["status"]).upper() == "CONFIRMED"
            )
        raise AssertionError(f"unexpected fetchval: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if query.startswith("INSERT INTO users"):
            self.users.setdefault(int(args[0]), {"credits": 0, "track_credits": 0})
            return "INSERT 0 1"
        if query.startswith("UPDATE payments SET status = 'CONFIRMED'"):
            payment_id, order_id = str(args[0]), str(args[1])
            self.payments[order_id]["status"] = "CONFIRMED"
            self.payments[order_id]["payment_id"] = payment_id
            return "UPDATE 1"
        if query.startswith("INSERT INTO transactions"):
            self.transactions.append(
                {
                    "tg_id": int(args[0]),
                    "amount": int(args[1]),
                    "actor": str(args[3]),
                    "order_id": str(args[4]),
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


class _Acquire:
    def __init__(self, conn: _ConfirmConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _ConfirmConn:
        return self.conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _ConfirmConn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


class _GenerationConn:
    def __init__(self) -> None:
        self.balance = 20
        self.transactions: list[dict[str, Any]] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def fetchval(self, query: str, *args: Any) -> int | None:
        if "SELECT amount FROM transactions" in query:
            reason = "web_generation_refund" if "web_generation_refund" in query else "web_generation_reserve"
            row = next((item for item in self.transactions if item["reason"] == reason and item["context"] == str(args[1])), None)
            return int(row["amount"]) if row else None
        if "SELECT credits FROM users" in query:
            return self.balance
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, int] | None:
        if query.startswith("UPDATE users SET credits = credits + $1"):
            self.balance += int(args[0])
            return {"credits": self.balance}
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if query.startswith("INSERT INTO users"):
            return "INSERT 0 0"
        if query.startswith("UPDATE users SET credits = $1"):
            self.balance = int(args[0])
            return "UPDATE 1"
        if query.startswith("INSERT INTO transactions"):
            context = str(args[3])
            if any(item["context"] == context for item in self.transactions):
                raise AssertionError("duplicate global context_order_id")
            reason = "web_generation_refund" if "web_generation_refund" in query else "web_generation_reserve"
            self.transactions.append({"reason": reason, "amount": int(args[1]), "context": context})
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


class _GenerationDB:
    def __init__(self, conn: _GenerationConn) -> None:
        self.pool = _Pool(conn)

    def _pool_or_fail(self) -> _Pool:
        return self.pool

    async def get_balance(self, _tg_id: int) -> int:
        return self.pool.conn.balance


def test_generation_refund_uses_distinct_global_ledger_context() -> None:
    conn = _GenerationConn()
    backend = BillingBackend.__new__(BillingBackend)
    backend._db = _GenerationDB(conn)

    assert asyncio.run(backend.reserve(777, "job-one", 11)) == 9
    assert asyncio.run(backend.refund(777, "job-one", 11)) == 20
    assert asyncio.run(backend.refund(777, "job-one", 11)) == 20
    assert [item["context"] for item in conn.transactions] == ["job-one", "job-one:refund"]


def test_payment_confirmation_grants_video_and_track_limits_once() -> None:
    conn = _ConfirmConn()
    db = CreditsDB("postgresql://example")
    db._pool = _Pool(conn)

    first = asyncio.run(db.confirm_payment_once("order-1", "payment-1", actor="test"))
    replay = asyncio.run(db.confirm_payment_once("order-1", "payment-1", actor="test"))
    renewal = asyncio.run(db.confirm_payment_once("order-2", "payment-2", actor="test"))

    assert first["applied"] is True
    assert first["credits_added"] == 400
    assert first["tracks_added"] == 10
    assert replay["applied"] is False
    assert renewal["tracks_added"] == 1
    assert conn.users[777] == {"credits": 800, "track_credits": 11}
    assert [tx["order_id"] for tx in conn.transactions] == ["order-1", "order-2"]
