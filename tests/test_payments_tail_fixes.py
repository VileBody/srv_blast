from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Keep tests self-contained on environments where asyncpg is not installed.
if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    asyncpg_stub.Connection = object
    asyncpg_stub.create_pool = None
    sys.modules["asyncpg"] = asyncpg_stub

from services.tg_bot_public.admin_panel import build_app
from services.tg_bot_public.credits_db import CreditsDB


class _DummyTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeConn:
    def __init__(self, *, initial_balances: dict[int, int] | None = None) -> None:
        self.balances: dict[int, int] = dict(initial_balances or {})
        self.transactions: list[dict[str, Any]] = []

    def transaction(self) -> _DummyTx:
        return _DummyTx()

    async def execute(self, query: str, *args: Any) -> str:
        q = str(query)
        if q.startswith("INSERT INTO users"):
            tg_id = int(args[0])
            self.balances.setdefault(tg_id, 0)
            return "INSERT 0 1"

        if q.startswith("UPDATE users SET credits = $1"):
            new_balance = int(args[0])
            tg_id = int(args[1])
            self.balances[tg_id] = new_balance
            return "UPDATE 1"

        if q.startswith("INSERT INTO transactions"):
            self.transactions.append(
                {
                    "tg_id": int(args[0]),
                    "amount": int(args[1]),
                    "reason": str(args[2]),
                    "admin_note": str(args[3]),
                }
            )
            return "INSERT 0 1"

        raise AssertionError(f"Unexpected execute query: {q}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        q = str(query)
        if "SELECT credits FROM users WHERE tg_id = $1" in q:
            tg_id = int(args[0])
            return self.balances.get(tg_id)

        if "FROM transactions" in q and "reason = ANY" in q:
            tg_id = int(args[0])
            paid_reasons = set(args[1])
            for tx in self.transactions:
                if int(tx["tg_id"]) == tg_id and str(tx["reason"]) in paid_reasons:
                    return 1
            return None

        raise AssertionError(f"Unexpected fetchval query: {q}")


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def test_add_credits_records_applied_delta_for_negative_adjustment() -> None:
    conn = _FakeConn(initial_balances={42: 3})
    db = CreditsDB("postgresql://example")
    db._pool = _FakePool(conn)

    new_balance = asyncio.run(db.add_credits(42, -10, "admin_revoke", admin_note="panel"))

    assert new_balance == 0
    assert conn.balances[42] == 0
    assert conn.transactions[-1]["amount"] == -3
    assert conn.transactions[-1]["reason"] == "admin_revoke"


def test_has_paid_treats_admin_activate_as_paid_state() -> None:
    conn = _FakeConn(initial_balances={77: 0})
    db = CreditsDB("postgresql://example")
    db._pool = _FakePool(conn)

    asyncio.run(db.add_credits(77, 15, "admin_activate", admin_note="manual package"))

    assert asyncio.run(db.has_paid(77)) is True


class _FakeCreditsDBNotify:
    def __init__(self) -> None:
        self.payment = {
            "order_id": "ord-1",
            "tg_id": 777,
            "package": "Триал",
            "amount_rub": 149,
        }
        self.add_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[str, str, str]] = []
        self.events: list[tuple[int, str, str]] = []

    async def is_payment_processed(self, payment_id: str, status: str) -> bool:
        return False

    async def update_payment_status(self, order_id: str, status: str, payment_id: str = "") -> bool:
        self.update_calls.append((str(order_id), str(status), str(payment_id)))
        return True

    async def get_payment(self, order_id: str) -> dict[str, Any]:
        return dict(self.payment)

    async def add_credits(self, tg_id: int, amount: int, reason: str, admin_note: str = "") -> int:
        self.add_calls.append(
            {
                "tg_id": int(tg_id),
                "amount": int(amount),
                "reason": str(reason),
                "admin_note": str(admin_note),
            }
        )
        return 5

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        self.events.append((int(tg_id), str(event), str(detail)))

    async def get_balance(self, tg_id: int) -> int:
        return 5

    async def get_user(self, tg_id: int) -> dict[str, Any]:
        return {"username": "tester"}


class _FakeStateStore:
    def __init__(self) -> None:
        self.reset_calls: list[int] = []

    async def reset_to_wait_audio(self, tg_id: int) -> None:
        self.reset_calls.append(int(tg_id))


class _FailingBot:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        raise RuntimeError("telegram_unavailable")


class _FakeTBankClient:
    def verify_notification(self, data: dict[str, Any]) -> bool:
        return True


def test_tbank_notify_unlocks_state_even_when_user_notify_fails() -> None:
    credits_db = _FakeCreditsDBNotify()
    state_store = _FakeStateStore()
    bot = _FailingBot()

    aiogram_module = types.ModuleType("aiogram")
    aiogram_types = types.ModuleType("aiogram.types")

    class _ReplyKeyboardMarkup:
        def __init__(self, keyboard: list[list[object]], resize_keyboard: bool) -> None:
            self.keyboard = keyboard
            self.resize_keyboard = resize_keyboard

    class _KeyboardButton:
        def __init__(self, text: str) -> None:
            self.text = text

    aiogram_types.ReplyKeyboardMarkup = _ReplyKeyboardMarkup
    aiogram_types.KeyboardButton = _KeyboardButton
    aiogram_module.types = aiogram_types
    sys.modules["aiogram"] = aiogram_module
    sys.modules["aiogram.types"] = aiogram_types

    settings = SimpleNamespace(
        admin_panel_password="secret",
        tg_bot_username="",
        manager_chat_id=0,
        admin_panel_port=18080,
    )

    app = build_app(
        credits_db=credits_db,
        state_store=state_store,
        settings=settings,
        tbank_client=_FakeTBankClient(),
        bot_ref=[bot],
    )

    client = TestClient(app)
    resp = client.post(
        "/api/tbank/notify",
        json={
            "OrderId": "ord-1",
            "Status": "CONFIRMED",
            "PaymentId": "pay-1",
            "Token": "ok",
        },
    )

    assert resp.status_code == 200
    assert resp.text == "OK"
    assert state_store.reset_calls == [777]
    assert bot.calls >= 1
    assert credits_db.add_calls and credits_db.add_calls[0]["reason"] == "payment"
    assert credits_db.events and credits_db.events[0][1] == "payment_confirmed"
