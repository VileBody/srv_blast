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
                    "admin_note": str(args[3]) if len(args) > 3 else "",
                    "actor": str(args[4]) if len(args) > 4 else "",
                    "order_id": str(args[5]) if len(args) > 5 else "",
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
            "status": "NEW",
            "payment_id": "",
            "is_recurrent": False,
            "rebill_id": "",
        }
        self.processed = False
        self.active_subscription: dict[str, Any] | None = None
        self.add_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[str, str, str]] = []
        self.rebill_updates: list[tuple[str, str]] = []
        self.subscriptions: list[tuple[int, str, str, int]] = []
        self.events: list[tuple[int, str, str]] = []

    async def is_payment_processed(self, payment_id: str, status: str) -> bool:
        return self.processed

    async def update_payment_status(self, order_id: str, status: str, payment_id: str = "") -> bool:
        self.update_calls.append((str(order_id), str(status), str(payment_id)))
        self.payment["status"] = str(status)
        if payment_id:
            self.payment["payment_id"] = str(payment_id)
        return True

    async def get_payment(self, order_id: str) -> dict[str, Any]:
        return dict(self.payment)

    async def add_credits(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        admin_note: str = "",
        *,
        actor: str = "",
        order_id: str = "",
    ) -> int:
        self.add_calls.append(
            {
                "tg_id": int(tg_id),
                "amount": int(amount),
                "reason": str(reason),
                "admin_note": str(admin_note),
                "actor": str(actor),
                "order_id": str(order_id),
            }
        )
        return 5

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        self.events.append((int(tg_id), str(event), str(detail)))

    async def update_rebill_id(self, order_id: str, rebill_id: str) -> None:
        self.rebill_updates.append((str(order_id), str(rebill_id)))
        self.payment["rebill_id"] = str(rebill_id)

    async def create_subscription(self, tg_id: int, package: str, rebill_id: str, amount_rub: int) -> None:
        self.subscriptions.append((int(tg_id), str(package), str(rebill_id), int(amount_rub)))
        self.active_subscription = {
            "id": len(self.subscriptions),
            "tg_id": int(tg_id),
            "package": str(package),
            "rebill_id": str(rebill_id),
            "amount_rub": int(amount_rub),
        }

    async def get_active_subscription(self, tg_id: int) -> dict[str, Any] | None:
        if self.active_subscription and int(self.active_subscription["tg_id"]) == int(tg_id):
            return dict(self.active_subscription)
        return None

    async def get_balance(self, tg_id: int) -> int:
        return 5

    async def get_user(self, tg_id: int) -> dict[str, Any]:
        return {"username": "tester"}


class _FakeStateStore:
    def __init__(self) -> None:
        self.reset_calls: list[int] = []
        self.redis = object()

    async def reset_to_wait_audio(self, tg_id: int) -> None:
        self.reset_calls.append(int(tg_id))


class _FakeRedis:
    async def get(self, key: str) -> None:
        return None


class _FakeStateStoreWithRedis:
    def __init__(self) -> None:
        self.redis = _FakeRedis()


class _FakeCreditsDBSubscriptions:
    def __init__(self) -> None:
        self.payment = {
            "order_id": "ord-recover",
            "tg_id": 777,
            "package": "Бласт",
            "amount_rub": 1990,
            "status": "CONFIRMED",
            "payment_id": "pay-777",
            "is_recurrent": True,
            "rebill_id": "rebill-recover-123456",
        }
        self.created_subscriptions: list[tuple[int, str, str, int]] = []
        self.events: list[tuple[int, str, str]] = []
        self.audit_events: list[tuple[str, str, str, str]] = []
        self.active_subscription: dict[str, Any] | None = None

    async def list_active_subscriptions(self) -> list[dict[str, Any]]:
        return []

    async def subscriptions_summary(self) -> dict[str, Any]:
        return {
            "active_cnt": 0,
            "paused_cnt": 0,
            "due_today_cnt": 0,
            "due_today_rub": 0,
            "due_7d_cnt": 0,
            "due_7d_rub": 0,
            "due_this_month_cnt": 0,
            "due_this_month_rub": 0,
            "overdue_cnt": 0,
            "recurrent_ok_30d": 0,
            "recurrent_fail_30d": 0,
            "recurrent_revenue_30d": 0,
        }

    async def find_orphan_recurrent_payments(self) -> list[dict[str, Any]]:
        return [
            {
                "order_id": "ord-recover",
                "tg_id": 777,
                "username": "recoverable",
                "amount_rub": 1990,
                "package": "Бласт",
                "payment_id": "pay-777",
                "rebill_id": "rebill-recover-123456",
                "has_payment_transaction": True,
                "created_at": "2026-05-16 10:00:00",
            },
            {
                "order_id": "ord-no-autopay",
                "tg_id": 888,
                "username": "qr_user",
                "amount_rub": 1990,
                "package": "Бласт",
                "payment_id": "pay-888",
                "rebill_id": "",
                "has_payment_transaction": True,
                "created_at": "2026-05-16 11:00:00",
            },
        ]

    async def get_payment(self, order_id: str) -> dict[str, Any] | None:
        if str(order_id) == self.payment["order_id"]:
            return dict(self.payment)
        return None

    async def get_active_subscription(self, tg_id: int) -> dict[str, Any] | None:
        if self.active_subscription and int(self.active_subscription["tg_id"]) == int(tg_id):
            return dict(self.active_subscription)
        return None

    async def create_subscription(self, tg_id: int, package: str, rebill_id: str, amount_rub: int) -> None:
        self.created_subscriptions.append((int(tg_id), str(package), str(rebill_id), int(amount_rub)))
        self.active_subscription = {
            "id": len(self.created_subscriptions),
            "tg_id": int(tg_id),
            "package": str(package),
            "rebill_id": str(rebill_id),
            "amount_rub": int(amount_rub),
        }

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        self.events.append((int(tg_id), str(event), str(detail)))

    async def audit_log(self, admin_user: str, action: str, target: str = "", details: str = "") -> None:
        self.audit_events.append((str(admin_user), str(action), str(target), str(details)))


class _FailingBot:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        raise RuntimeError("telegram_unavailable")


class _FakeTBankClient:
    def __init__(self, rebill_id: str = "") -> None:
        self.rebill_id = str(rebill_id)
        self.get_state_calls: list[str] = []

    def verify_notification(self, data: dict[str, Any]) -> bool:
        return True

    async def get_state(self, payment_id: str) -> dict[str, Any]:
        self.get_state_calls.append(str(payment_id))
        return {"RebillId": self.rebill_id}


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
        season_redis_prefix="test:season",
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
    assert credits_db.add_calls[0]["actor"] == "tbank_webhook"
    assert credits_db.add_calls[0]["order_id"] == "ord-1"
    assert credits_db.events and credits_db.events[0][1] == "payment_confirmed"


def test_tbank_notify_creates_subscription_for_recurrent_payment() -> None:
    credits_db = _FakeCreditsDBNotify()
    credits_db.payment["is_recurrent"] = True
    state_store = _FakeStateStore()
    tbank_client = _FakeTBankClient()

    settings = SimpleNamespace(
        admin_panel_password="secret",
        tg_bot_username="",
        manager_chat_id=0,
        admin_panel_port=18080,
        season_redis_prefix="test:season",
    )

    app = build_app(
        credits_db=credits_db,
        state_store=state_store,
        settings=settings,
        tbank_client=tbank_client,
        bot_ref=[None],
    )

    client = TestClient(app)
    resp = client.post(
        "/api/tbank/notify",
        json={
            "OrderId": "ord-1",
            "Status": "CONFIRMED",
            "PaymentId": "pay-1",
            "RebillId": "rebill-123",
            "Token": "ok",
        },
    )

    assert resp.status_code == 200
    assert tbank_client.get_state_calls == []
    assert credits_db.rebill_updates == [("ord-1", "rebill-123")]
    assert credits_db.subscriptions == [(777, "Триал", "rebill-123", 149)]
    assert ("subscription_created" in [event for _, event, _ in credits_db.events])


def test_tbank_notify_bootstraps_recurrent_subscription_on_duplicate_confirmed() -> None:
    credits_db = _FakeCreditsDBNotify()
    credits_db.payment.update(
        {
            "is_recurrent": True,
            "status": "CONFIRMED",
            "payment_id": "pay-1",
        }
    )
    credits_db.processed = True
    state_store = _FakeStateStore()

    settings = SimpleNamespace(
        admin_panel_password="secret",
        tg_bot_username="",
        manager_chat_id=0,
        admin_panel_port=18080,
        season_redis_prefix="test:season",
    )

    app = build_app(
        credits_db=credits_db,
        state_store=state_store,
        settings=settings,
        tbank_client=_FakeTBankClient(),
        bot_ref=[None],
    )

    client = TestClient(app)
    resp = client.post(
        "/api/tbank/notify",
        json={
            "OrderId": "ord-1",
            "Status": "CONFIRMED",
            "PaymentId": "pay-1",
            "RebillId": "rebill-dup",
            "Token": "ok",
        },
    )

    assert resp.status_code == 200
    assert credits_db.add_calls == []
    assert credits_db.rebill_updates == [("ord-1", "rebill-dup")]
    assert credits_db.subscriptions == [(777, "Триал", "rebill-dup", 149)]


def test_tbank_notify_bootstraps_recurrent_subscription_on_authorized_after_confirmed() -> None:
    credits_db = _FakeCreditsDBNotify()
    credits_db.payment.update(
        {
            "is_recurrent": True,
            "status": "CONFIRMED",
            "payment_id": "pay-1",
        }
    )
    state_store = _FakeStateStore()

    settings = SimpleNamespace(
        admin_panel_password="secret",
        tg_bot_username="",
        manager_chat_id=0,
        admin_panel_port=18080,
        season_redis_prefix="test:season",
    )

    app = build_app(
        credits_db=credits_db,
        state_store=state_store,
        settings=settings,
        tbank_client=_FakeTBankClient(),
        bot_ref=[None],
    )

    client = TestClient(app)
    resp = client.post(
        "/api/tbank/notify",
        json={
            "OrderId": "ord-1",
            "Status": "AUTHORIZED",
            "PaymentId": "pay-1",
            "RebillId": "rebill-auth",
            "Token": "ok",
        },
    )

    assert resp.status_code == 200
    assert credits_db.add_calls == []
    assert credits_db.rebill_updates == [("ord-1", "rebill-auth")]
    assert credits_db.subscriptions == [(777, "Триал", "rebill-auth", 149)]


def test_admin_subscriptions_splits_recoverable_and_paid_without_autopay() -> None:
    credits_db = _FakeCreditsDBSubscriptions()
    settings = SimpleNamespace(
        admin_panel_password="secret",
        tg_bot_username="",
        manager_chat_id=0,
        admin_panel_port=18080,
        season_redis_prefix="test:season",
    )
    app = build_app(
        credits_db=credits_db,
        state_store=_FakeStateStoreWithRedis(),
        settings=settings,
        tbank_client=None,
        bot_ref=[None],
    )

    client = TestClient(app)
    resp = client.get("/admin/subscriptions", auth=("admin", "secret"))

    assert resp.status_code == 200
    assert "Recoverable: есть RebillId, но нет подписки" in resp.text
    assert "Оплачено, но без автосписания" in resp.text
    assert "Создать подписку" in resp.text
    assert "Сироты:" not in resp.text


def test_admin_subscription_recover_creates_subscription_from_saved_rebill() -> None:
    credits_db = _FakeCreditsDBSubscriptions()
    settings = SimpleNamespace(
        admin_panel_password="secret",
        tg_bot_username="",
        manager_chat_id=0,
        admin_panel_port=18080,
        season_redis_prefix="test:season",
    )
    app = build_app(
        credits_db=credits_db,
        state_store=_FakeStateStoreWithRedis(),
        settings=settings,
        tbank_client=None,
        bot_ref=[None],
    )

    client = TestClient(app)
    resp = client.post(
        "/admin/subscriptions/recover",
        data={"order_id": "ord-recover"},
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert credits_db.created_subscriptions == [(777, "Бласт", "rebill-recover-123456", 1990)]
    assert credits_db.events and credits_db.events[0][1] == "subscription_created"
    assert credits_db.audit_events and credits_db.audit_events[0][1] == "subscription_recovered"
