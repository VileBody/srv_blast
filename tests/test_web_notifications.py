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
    monkeypatch.setattr(outbox.telegram_bot, "deliver", lambda *args, **kwargs: calls.append(args) or outbox.telegram_bot.SendResult(False, error="Telegram 500: boom"))
    outbox.deliver_pending()
    outbox.deliver_pending()
    assert len(calls) == 1  # backoff, not a tight retry loop
    with outbox.db.read() as cursor:
        cursor.execute("SELECT attempts, delivered_at, last_error FROM notification_outbox")
        attempts, delivered, error = cursor.fetchone()
    assert attempts == 1 and delivered is None and error
    monkeypatch.setattr(outbox.time, "time", lambda: 1031)
    monkeypatch.setattr(outbox.telegram_bot, "deliver", lambda *args, **kwargs: calls.append(args) or outbox.telegram_bot.SendResult(True))
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
    monkeypatch.setattr(outbox.telegram_bot, "deliver", lambda *args, **kwargs: calls.append((args, kwargs)) or outbox.telegram_bot.SendResult(True))
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


def test_user_notifications_follow_the_account_bot_flag(outbox, monkeypatch):
    """Пришедшим по ссылке из бота пишем от публичного бота: бота входа они не запускали."""
    auth = importlib.import_module("web_app.backend.app.auth_store")
    monkeypatch.setattr(auth, "notify_bot_for_chat", lambda chat: "public" if chat == 555 else "auth")
    monkeypatch.setattr(outbox.time, "time", lambda: 1000)
    outbox.enqueue("video:a", chat_id=555, text="ready", user_route=True)
    outbox.enqueue("video:b", chat_id=666, text="ready", user_route=True)
    outbox.enqueue("login:x", chat_id=555, text="welcome")  # ответ на вход — всегда бот входа
    sent = []
    monkeypatch.setattr(outbox.telegram_bot, "deliver",
                        lambda chat, text, markup=None, **kw: sent.append((chat, text, kw.get("via")))
                        or outbox.telegram_bot.SendResult(True))
    outbox.deliver_pending()
    assert sorted(sent) == [(555, "ready", "public"), (555, "welcome", "auth"), (666, "ready", "auth")]


def test_send_via_public_bot_uses_its_token(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web_app" / "backend"))
    bot = importlib.import_module("web_app.backend.app.telegram_bot")
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, params, token=None: calls.append(token) or {"ok": True})
    monkeypatch.setenv("WEB_PUBLIC_BOT_TOKEN", "public-token")
    assert bot._send(1, "hi", via="public") is True
    monkeypatch.delenv("WEB_PUBLIC_BOT_TOKEN")
    assert bot._send(1, "hi", via="public") is False  # не настроено — явный отказ, не бот входа
    assert calls == ["public-token"]


def test_batch_done_offers_unlimited_to_free_users(outbox, monkeypatch):
    """«Батч готов» бесплатному без безлимита: вторая кнопка ведёт на модалку B."""
    auth = importlib.import_module("web_app.backend.app.auth_store")
    monkeypatch.setattr(auth, "chat_id_for_user", lambda owner: 555)
    monkeypatch.setenv("APP_URL", "https://app.blast808.com")
    done = {"id": "b1", "projectId": "p1", "userId": "owner", "status": "COMPLETED",
            "videos": [{"id": "v1", "status": "COMPLETED"}]}
    outbox.queue_job(done, unlimited_offer=True)
    partial = {"id": "b2", "projectId": "p1", "userId": "owner", "status": "FAILED",
               "videos": [{"id": "v2", "status": "COMPLETED"}, {"id": "v3", "status": "FAILED", "error": "x"}]}
    outbox.queue_job(partial, unlimited_offer=True)
    nothing = {"id": "b3", "projectId": "p1", "userId": "owner", "status": "FAILED",
               "videos": [{"id": "v4", "status": "FAILED", "error": "x"}]}
    outbox.queue_job(nothing, unlimited_offer=True)
    with outbox.db.read() as cursor:
        cursor.execute("SELECT event_key, payload FROM notification_outbox")
        rows = {key: outbox.db.json_value(raw) for key, raw in cursor.fetchall()}
    offer = [{"text": "Оценить и получить безлимит", "url": "https://app.blast808.com/app/projects/p1?unlimited=1"}]
    assert rows["job:b1:terminal"]["markup"]["inline_keyboard"][1] == offer
    assert rows["job:b2:terminal"]["markup"]["inline_keyboard"][1] == offer
    assert len(rows["job:b3:terminal"]["markup"]["inline_keyboard"]) == 1  # оценивать нечего
    # поштучные «Ролик N готов» — без второй кнопки
    assert len(rows["job:b1:video:v1"]["markup"]["inline_keyboard"]) == 1


def test_notify_batch_done_carries_the_offer_button(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web_app" / "backend"))
    bot = importlib.import_module("web_app.backend.app.telegram_bot")
    sent = []
    monkeypatch.setattr(bot, "configured", lambda: True)
    monkeypatch.setattr(bot, "_send", lambda chat, text, markup=None, **kw: sent.append(markup) or True)
    monkeypatch.setattr(bot.auth_store, "notify_bot_for_chat", lambda chat: "public")
    bot.notify_batch_done(1, 5, "p9", "https://app.blast808.com", unlimited_offer=True)
    bot.notify_batch_done(1, 5, "p9", "https://app.blast808.com")
    assert sent[0]["inline_keyboard"][1][0]["url"] == "https://app.blast808.com/app/projects/p9?unlimited=1"
    assert len(sent[1]["inline_keyboard"]) == 1


def test_production_monitor_decides_the_offer_from_the_funnel(outbox, monkeypatch):
    monitor = importlib.import_module("web_app.backend.app.production_monitor")
    funnel = importlib.import_module("web_app.backend.app.funnel")
    monkeypatch.setattr(monitor.auth_store, "chat_id_for_user", lambda owner: 777)
    seen = []

    async def due(tg_id):
        seen.append(tg_id)
        return True

    monkeypatch.setattr(funnel, "unlimited_offer_due", due)
    running = {"id": "j", "userId": "owner", "status": "PROCESSING"}
    done = {"id": "j", "userId": "owner", "status": "COMPLETED"}
    assert asyncio.run(monitor._unlimited_offer_due(running)) is False  # только итоговое
    assert asyncio.run(monitor._unlimited_offer_due(done)) is True and seen == [777]

    async def broken(tg_id):
        raise RuntimeError("pg down")

    monkeypatch.setattr(funnel, "unlimited_offer_due", broken)
    # сбой воронки не держит «батч готов»: без второй кнопки, с логом
    assert asyncio.run(monitor._unlimited_offer_due(done)) is False


def _row(outbox, key):
    with outbox.db.read() as cursor:
        cursor.execute(outbox.db.sql(
            "SELECT attempts, delivered_at, dead_at, last_error FROM notification_outbox WHERE event_key = %s"
        ), (key,))
        return cursor.fetchone()


def test_blocked_bot_is_dead_lettered_at_once(outbox, monkeypatch):
    """Регресс: 403 «bot was blocked» ретраился вечно и копился в outbox."""
    monkeypatch.setattr(outbox.time, "time", lambda: 1000)
    outbox.enqueue("video:blocked", chat_id=123, text="ready")
    send = outbox.telegram_bot.SendResult(False, permanent=True, error="Telegram 403: Forbidden: bot was blocked by the user")
    calls = []
    monkeypatch.setattr(outbox.telegram_bot, "deliver", lambda *a, **kw: calls.append(a) or send)
    outbox.deliver_pending()
    monkeypatch.setattr(outbox.time, "time", lambda: 100000)
    outbox.deliver_pending()
    attempts, delivered, dead, error = _row(outbox, "video:blocked")
    assert len(calls) == 1 and attempts == 1 and delivered is None and dead == 1000
    assert "blocked" in error


def test_transient_failures_stop_after_the_attempt_cap(outbox, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(outbox.time, "time", lambda: clock[0])
    outbox.enqueue("video:flaky", chat_id=123, text="ready")
    calls = []
    fail = outbox.telegram_bot.SendResult(False, error="delivery unknown: TimeoutError")
    monkeypatch.setattr(outbox.telegram_bot, "deliver", lambda *a, **kw: calls.append(a) or fail)
    for _ in range(outbox.MAX_ATTEMPTS + 5):
        outbox.deliver_pending()
        clock[0] += 1000
    attempts, delivered, dead, _ = _row(outbox, "video:flaky")
    assert len(calls) == outbox.MAX_ATTEMPTS == attempts and delivered is None and dead is not None


def test_fresh_events_go_before_a_backlog_of_retries(outbox, monkeypatch):
    """Завал повторов не должен задерживать свежее «Ролик готов»."""
    monkeypatch.setattr(outbox.time, "time", lambda: 1000)
    with outbox.db.transaction() as cursor:
        for i in range(outbox.BATCH_SIZE + 10):
            cursor.execute(outbox.db.sql(
                "INSERT INTO notification_outbox (event_key, payload, attempts, next_attempt) VALUES (%s, %s, 5, 0)"
            ), (f"old:{i:03d}", outbox.db.json_param({"chat_id": 1, "text": "old"})))
    outbox.enqueue("video:fresh", chat_id=2, text="fresh")
    sent = []
    monkeypatch.setattr(outbox.telegram_bot, "deliver",
                        lambda chat_id, text, *a, **kw: sent.append(text) or outbox.telegram_bot.SendResult(text == "fresh"))
    outbox.deliver_pending()
    assert sent[0] == "fresh" and len(sent) == outbox.BATCH_SIZE
    assert _row(outbox, "video:fresh")[1] == 1000


def test_bot_api_403_is_classified_as_permanent(outbox, monkeypatch):
    import io
    import json
    import urllib.error

    tg = outbox.telegram_bot

    def blocked(*_a, **_kw):
        body = json.dumps({"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
        raise urllib.error.HTTPError("https://api.telegram.org/x", 403, "Forbidden", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(tg, "_api", blocked)
    result = tg.deliver(1, "hi")
    assert not result.ok and result.permanent and "blocked" in result.error
    monkeypatch.setattr(tg, "_api", lambda *a, **kw: {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
    assert tg.deliver(1, "hi").permanent
    monkeypatch.setattr(tg, "_api", lambda *a, **kw: {"ok": False, "error_code": 429, "description": "Too Many Requests"})
    assert not tg.deliver(1, "hi").permanent
    monkeypatch.setattr(tg, "_api", lambda *a, **kw: {"ok": True})
    assert tg._send(1, "hi") is True
