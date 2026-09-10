from __future__ import annotations

import asyncio
import importlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def outbox(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web_app" / "backend"))
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("BLAST_BACKEND_MODE", "mock")
    monkeypatch.setenv("APP_URL", "http://localhost:5173")
    monkeypatch.setenv("BLAST_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("BLAST_CORS_ORIGINS", "http://localhost:5173")
    module = importlib.import_module("web_app.backend.app.notifications")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("BLAST_DB_PATH", str(tmp_path / "notifications.db"))
    monkeypatch.setattr(module.db, "_local", threading.local())
    monkeypatch.setattr(module.db, "_ready", False)
    module.db.migrate()
    return module


def test_outbox_deduplicates_and_keeps_failed_delivery_for_retry(outbox, monkeypatch):
    monkeypatch.setattr(outbox.time, "time", lambda: 1000)
    outbox.enqueue("video:1", chat_id=123, text="ready")
    outbox.enqueue("video:1", chat_id=123, text="ready")
    calls = []
    monkeypatch.setattr(outbox.telegram_bot, "_send", lambda *args, **kwargs: calls.append(args) or False)
    outbox.deliver_pending()
    outbox.deliver_pending()
    assert len(calls) == 1  # backoff, not a tight retry loop
    with outbox.db.read() as cursor:
        cursor.execute("SELECT attempts, delivered_at, last_error FROM notification_outbox")
        attempts, delivered, error = cursor.fetchone()
    assert attempts == 1 and delivered is None and error
    monkeypatch.setattr(outbox.time, "time", lambda: 1031)
    monkeypatch.setattr(outbox.telegram_bot, "_send", lambda *args, **kwargs: calls.append(args) or True)
    outbox.deliver_pending()
    outbox.deliver_pending()
    assert len(calls) == 2
    with outbox.db.read() as cursor:
        cursor.execute("SELECT delivered_at FROM notification_outbox")
        assert cursor.fetchone()[0] == 1031


def test_job_notifications_use_owner_and_site_project_link(outbox, monkeypatch):
    auth = importlib.import_module("web_app.backend.app.auth_store")
    monkeypatch.setattr(auth, "chat_id_for_user", lambda owner: 555 if owner == "owner" else None)
    monkeypatch.setenv("APP_URL", "https://app.blast808.com")
    job = {"id": "batch", "projectId": "project", "userId": "owner", "status": "FAILED", "videos": [
        {"id": "one", "status": "COMPLETED"}, {"id": "two", "status": "FAILED", "error": "stage2 failed"}
    ]}
    outbox.queue_job(job)
    outbox.queue_job(job)
    with outbox.db.read() as cursor:
        cursor.execute("SELECT payload FROM notification_outbox")
        payloads = [outbox.db.json_value(row[0]) for row in cursor.fetchall()]
    assert len(payloads) == 3
    users = [row for row in payloads if not row["manager"]]
    assert len(users) == 2
    for row in users:
        assert row["chat_id"] == 555
        assert row["markup"]["inline_keyboard"][0][0]["url"] == "https://app.blast808.com/app/projects/project"


def test_manager_delivery_uses_explicit_route(outbox, monkeypatch):
    monkeypatch.setenv("WEB_MANAGER_CHAT_ID", "-123")
    outbox.manager_event("payment:created", "payment created")
    calls = []
    monkeypatch.setattr(outbox.telegram_bot, "_send", lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    outbox.deliver_pending()
    assert calls[0][0][0] == "-123"
    assert calls[0][1]["manager"] is True


def test_production_login_queues_a_confirmation_for_each_new_login(outbox, monkeypatch):
    auth = importlib.import_module("web_app.backend.app.auth_store")
    monkeypatch.setenv("BLAST_BACKEND_MODE", "production")
    monkeypatch.setattr(auth, "confirm_token", lambda *args, **kwargs: "ok")
    for token in ["first-token", "first-token", "repeat-login-token"]:
        outbox.telegram_bot._handle_start(777, token, {})
    with outbox.db.read() as cursor:
        cursor.execute("SELECT event_key, payload FROM notification_outbox")
        rows = cursor.fetchall()
    assert len(rows) == 2
    assert all("token" not in row[0] for row in rows)
    assert all("@blast808bot" in outbox.db.json_value(row[1])["text"] for row in rows)


def test_payment_reconciliation_recovers_saved_link_and_unknown_init_once(outbox, monkeypatch):
    billing = importlib.import_module("web_app.backend.app.billing_backend")
    rows = [
        {"order_id": "saved-web-order", "tg_id": 777, "package": "Бласт", "amount_rub": 1990,
         "status": "CONFIRMED", "payment_url": "https://pay.example/link"},
        {"order_id": "unknown-web-order", "tg_id": 777, "package": "Глоу", "amount_rub": 7990,
         "status": "INIT_UNKNOWN", "payment_url": ""},
    ]

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def fetch(self, query):
            assert "order_id LIKE '%-web-%'" in query
            return rows

    backend = billing.BillingBackend.__new__(billing.BillingBackend)
    backend._db = SimpleNamespace(_pool_or_fail=lambda: SimpleNamespace(acquire=Connection))
    # Keep SQLite access in this test's thread; production runs this on its
    # own DB connection in a threadpool.
    async def directly(fn, *args):
        return fn(*args)

    monkeypatch.setattr(importlib.import_module("starlette.concurrency"), "run_in_threadpool", directly)
    asyncio.run(backend.queue_payment_notifications())
    asyncio.run(backend.queue_payment_notifications())
    with outbox.db.read() as cursor:
        cursor.execute("SELECT event_key FROM notification_outbox ORDER BY event_key")
        assert [r[0] for r in cursor.fetchall()] == [
            "payment:saved-web-order:created", "payment:unknown-web-order:init_unknown"
        ]


def test_production_monitor_advances_without_browser_and_refunds_owner(outbox, monkeypatch):
    monitor = importlib.import_module("web_app.backend.app.production_monitor")
    production = importlib.import_module("web_app.backend.app.production_backend")
    billing = importlib.import_module("web_app.backend.app.billing_backend")
    job = {"id": "j", "userId": "owner", "orchestratorJobId": "orch", "status": "PROCESSING", "videos": []}
    monkeypatch.setattr(monitor.store, "JOBS", {"j": job})
    monkeypatch.setattr(monitor.persistence, "save_job", lambda jid: None)
    monkeypatch.setattr(monitor.auth_store, "chat_id_for_user", lambda owner: 777 if owner == "owner" else None)
    refunds = []

    def sync(current):
        current.update(status="FAILED", videos=[{"status": "FAILED"}])

    async def refund(tg_id, jid, count):
        refunds.append((tg_id, jid, count))

    async def stop_after_tick(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(production, "get_backend", lambda: SimpleNamespace(sync_job=sync))
    monkeypatch.setattr(billing, "get_billing", lambda: SimpleNamespace(refund=refund))
    monkeypatch.setattr(monitor.asyncio, "sleep", stop_after_tick)

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await monitor._run()
        await monitor.sync_job(job)

    asyncio.run(run())
    assert job["status"] == "FAILED"
    assert refunds == [(777, "j", 1)]
