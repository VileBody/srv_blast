from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from services.tg_bot_public.broadcast_sender import (
    ManagerTierAlertWorker,
    build_s1_manager_alert,
)
from services.tg_bot_public.credits_db import CreditsDB


def _candidate(tg_id: int = 101, **overrides):
    row = {
        "tg_id": tg_id,
        "tier": "S1",
        "username": "artist",
        "credits": 2,
        "cohort": "organic",
        "gens_done": 2,
        "last_rating": "high",
        "feedback_form_clicked": True,
        "survey_opened_at": "2026-07-16 10:00 UTC",
        "viewed_packages_list": True,
        "viewed_package_details": False,
        "has_purchase": False,
        "bot_blocked": False,
        "manager_contacted": False,
        "last_active_at": "2026-07-16 10:01 UTC",
    }
    row.update(overrides)
    return row


class _FakeDB:
    def __init__(self, users=None, rows=None):
        self.users = {int(u["tg_id"]): deepcopy(u) for u in (users or [])}
        self.rows = deepcopy(rows or {})
        self.lock = asyncio.Lock()
        self.discover_calls = 0

    @asynccontextmanager
    async def s1_manager_alert_lock(self):
        if self.lock.locked():
            yield False
            return
        await self.lock.acquire()
        try:
            yield True
        finally:
            self.lock.release()

    async def discover_new_s1_outreach(self):
        self.discover_calls += 1
        count = 0
        for tg_id, user in self.users.items():
            if user.get("tier") != "S1" or tg_id in self.rows:
                continue
            self.rows[tg_id] = {
                "status": "todo", "baseline": False, "notified_at": None,
                "notify_attempts": 0, "notify_last_error": "",
            }
            count += 1
        return count

    def _eligible(self, tg_id):
        user = self.users.get(tg_id) or {}
        row = self.rows.get(tg_id) or {}
        return (
            user.get("tier") == "S1"
            and not user.get("has_purchase")
            and not user.get("bot_blocked")
            and not user.get("manager_contacted")
            and row.get("status") == "todo"
            and not row.get("baseline")
            and not row.get("notified_at")
        )

    async def pending_s1_manager_alert_ids(self, *, max_attempts, limit=100):
        return [
            tg_id for tg_id, row in sorted(self.rows.items())
            if self._eligible(tg_id) and row["notify_attempts"] < max_attempts
        ][:limit]

    async def get_s1_manager_alert_candidate(self, tg_id):
        return deepcopy(self.users[tg_id]) if self._eligible(tg_id) else None

    async def record_s1_manager_alert_result(self, tg_id, *, sent, error=""):
        row = self.rows[tg_id]
        row["notify_attempts"] += 1
        row["notify_last_error"] = error
        if sent:
            row["notified_at"] = "now"


class _FakeBot:
    def __init__(self, *, fail_times=0, slow=False):
        self.fail_times = fail_times
        self.slow = slow
        self.calls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text, kwargs))
        if self.slow:
            await asyncio.sleep(0.01)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("telegram unavailable")


def _worker(db, bot, *, manager_chat_id=999):
    return ManagerTierAlertWorker(
        db, [bot], manager_chat_id=manager_chat_id,
        admin_panel_public_url="https://blast808.com/admin",
        poll_interval=1, max_attempts=5,
    )


def test_new_s1_is_persisted_and_notified_exactly_once_across_ticks_and_restart():
    async def scenario():
        db = _FakeDB([_candidate()])
        bot = _FakeBot()
        await _worker(db, bot)._tick_once()
        await _worker(db, bot)._tick_once()
        await _worker(db, bot)._tick_once()  # a fresh worker models process restart
        assert len(bot.calls) == 1
        assert db.rows[101]["notified_at"] == "now"
        assert db.rows[101]["status"] == "todo"

    asyncio.run(scenario())


def test_two_concurrent_workers_send_one_message():
    async def scenario():
        db = _FakeDB([_candidate()])
        bot = _FakeBot(slow=True)
        await asyncio.gather(_worker(db, bot)._tick_once(), _worker(db, bot)._tick_once())
        assert len(bot.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"has_purchase": True},
        {"tier": "S2"},
        {"manager_contacted": True},
        {"bot_blocked": True},
    ],
)
def test_last_mile_state_change_suppresses_alert(change):
    async def scenario():
        user = _candidate(**change)
        db = _FakeDB([user], rows={
            101: {"status": "todo", "baseline": False, "notified_at": None,
                  "notify_attempts": 0, "notify_last_error": ""}
        })
        bot = _FakeBot()
        await _worker(db, bot)._tick_once()
        assert bot.calls == []

    asyncio.run(scenario())


def test_telegram_failure_is_saved_then_retried_on_next_tick(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def scenario():
        db = _FakeDB([_candidate()])
        bot = _FakeBot(fail_times=3)
        worker = _worker(db, bot)
        await worker._tick_once()
        assert db.rows[101]["notify_attempts"] == 1
        assert "telegram unavailable" in db.rows[101]["notify_last_error"]
        assert db.rows[101]["notified_at"] is None
        await worker._tick_once()
        assert db.rows[101]["notified_at"] == "now"
        assert len(bot.calls) == 4

    asyncio.run(scenario())


def test_historical_baseline_and_missing_manager_do_not_send():
    async def scenario():
        baseline = {101: {
            "status": "todo", "baseline": True, "notified_at": None,
            "notify_attempts": 0, "notify_last_error": "",
        }}
        db = _FakeDB([_candidate()], rows=baseline)
        bot = _FakeBot()
        await _worker(db, bot)._tick_once()
        assert bot.calls == []
        db2 = _FakeDB([_candidate(202)])
        await _worker(db2, bot, manager_chat_id=0)._tick_once()
        assert db2.rows[202]["status"] == "todo"
        assert bot.calls == []

    asyncio.run(scenario())


def test_message_handles_missing_username_and_escapes_html_user_data():
    no_username = build_s1_manager_alert(
        _candidate(username="", cohort="<paid & organic>"),
        "https://blast808.com/admin",
    )
    assert "Пользователь: 101" in no_username
    assert "&lt;paid &amp; organic&gt;" in no_username
    assert 'href="https://blast808.com/admin/users/101"' in no_username
    assert 'href="https://blast808.com/admin/tiers?tier=S1"' in no_username
    assert "/admin/admin/" not in no_username

    hostile = build_s1_manager_alert(
        _candidate(username='<b onclick="x">', last_rating="high<script>"),
        "https://blast808.com/admin",
    )
    assert '<b onclick="x">' not in hostile
    assert "&lt;b onclick=&quot;x&quot;&gt;" in hostile
    assert "high&lt;script&gt;" in hostile
    assert "parse_mode" not in hostile


def test_s1_sql_still_uses_survey_opened_and_alert_scope_excludes_p3_p4():
    source = inspect.getsource(CreditsDB._ensure_migrations)
    assert "event = 'survey_opened'" in source
    assert "us.feedback_form_clicked AND NOT us.has_purchase THEN 'S1'" in source
    s1_clause = source.split("THEN 'S1'", 1)[0].rsplit("WHEN", 1)[1]
    assert "survey_done" not in s1_clause

    discover_source = inspect.getsource(CreditsDB.discover_new_s1_outreach)
    assert "WHERE tier = 'S1'" in discover_source
    assert "P3" not in discover_source and "P4" not in discover_source


def test_p3_p4_users_never_enter_manager_alert_workflow():
    async def scenario():
        db = _FakeDB([_candidate(301, tier="P3"), _candidate(302, tier="P4")])
        bot = _FakeBot()
        await _worker(db, bot)._tick_once()
        assert db.rows == {}
        assert bot.calls == []

    asyncio.run(scenario())


def test_ci_deploy_wires_admin_url_and_smoke_requires_confirmation():
    deploy = inspect.getsource(CreditsDB.discover_new_s1_outreach)  # import sanity first
    assert "WHERE tier = 'S1'" in deploy

    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    deploy_script = (root / "infra/runners/deploy_branch.sh").read_text(encoding="utf-8")
    deploy_workflow = (root / ".github/workflows/deploy-split-main.yml").read_text(encoding="utf-8")
    smoke_workflow = (root / ".github/workflows/smoke-s1-manager-alert.yml").read_text(encoding="utf-8")
    smoke_module = (root / "services/tg_bot_public/s1_manager_alert_smoke.py").read_text(encoding="utf-8")

    assert "INFRA_ADMIN_PANEL_PUBLIC_URL" in deploy_workflow
    assert "set_env_file_value \"$REPO_DIR/.env\" ADMIN_PANEL_PUBLIC_URL" in deploy_script
    assert "bootstrap_infra_admin_panel_url" in deploy_script
    assert "confirm_send" in smoke_workflow
    assert "s1_manager_alert_smoke" in smoke_workflow
    assert "build_aiogram_session" in smoke_module
    assert "proxy_url=settings.tg_file_proxy_url" in smoke_module
