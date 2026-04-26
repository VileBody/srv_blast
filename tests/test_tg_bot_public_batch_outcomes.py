from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from typing import Any

# Local test environments may miss runtime deps; only symbols are required at import time.
if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object  # type: ignore[attr-defined]
    sys.modules["asyncpg"] = asyncpg_stub

if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")

    class _RedisStub:  # pragma: no cover - import-time compatibility shim
        pass

    redis_asyncio.Redis = _RedisStub  # type: ignore[attr-defined]
    redis_module.asyncio = redis_asyncio
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import ChatState, STAGE_PROCESSING, STAGE_WAIT_AUDIO


class _FakeStore:
    def __init__(self) -> None:
        self.saved_states: list[ChatState] = []

    async def set(self, state: ChatState) -> None:
        self.saved_states.append(state.model_copy(deep=True))


class _FakeOrchestrator:
    def __init__(self, jobs: dict[str, dict[str, Any]]) -> None:
        self._jobs = {str(k): dict(v) for k, v in jobs.items()}

    async def get_job(self, job_id: str) -> dict[str, Any]:
        jid = str(job_id)
        if jid not in self._jobs:
            raise AssertionError(f"Unexpected job_id in test: {jid}")
        return dict(self._jobs[jid])

    async def get_jobs(self, job_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {str(jid): await self.get_job(str(jid)) for jid in job_ids}


class _FakeCreditsDB:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

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
        return 0

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        self.events.append({"tg_id": int(tg_id), "event": str(event), "detail": str(detail)})

    async def get_balance(self, tg_id: int) -> int:
        _ = tg_id
        return 0

    async def has_paid(self, tg_id: int) -> bool:
        _ = tg_id
        return False


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None, **_kwargs):
        self.messages.append(
            {
                "chat_id": int(chat_id),
                "text": str(text),
                "reply_markup": reply_markup,
            }
        )
        return SimpleNamespace(message_id=len(self.messages))


def _new_app(*, jobs: dict[str, dict[str, Any]]) -> public_app.BlastBotApp:
    app = object.__new__(public_app.BlastBotApp)
    app.settings = SimpleNamespace(bot_status_update_interval_s=5.0)
    app.store = _FakeStore()
    app.orchestrator = _FakeOrchestrator(jobs)
    app.credits_db = _FakeCreditsDB()
    app._bot = _FakeBot()
    app._manager_fail_calls: list[dict[str, Any]] = []
    app._enqueue_calls: list[dict[str, Any]] = []

    app._require_bot = lambda: app._bot

    async def _upsert_status_message(*, bot, st, text):  # noqa: ARG001
        return None

    async def _finalize_one_job(*, bot, st, job_id, job):  # noqa: ARG001
        return None

    async def _notify_manager_generation_failure(**kwargs):
        app._manager_fail_calls.append(dict(kwargs))

    async def _enqueue_batch_version(**kwargs):
        app._enqueue_calls.append(dict(kwargs))
        return "queued-next-job"

    app._upsert_status_message = _upsert_status_message
    app._finalize_one_job = _finalize_one_job
    app._notify_manager_generation_failure = _notify_manager_generation_failure
    app._enqueue_batch_version = _enqueue_batch_version
    return app


def test_master_failed_batch_refunds_all_and_resets_to_wait_audio(
    monkeypatch,
) -> None:
    async def _run() -> None:
        monkeypatch.setattr(public_app, "_kb", lambda *rows: ("kb", rows))
        app = _new_app(
            jobs={
                "master-1": {
                    "status": "FAILED",
                    "stage": "render",
                    "error": "master_failed_for_test",
                }
            }
        )

        async def _forbidden_enqueue(**_kwargs):
            raise AssertionError("enqueue must not be called for failed master")

        app._enqueue_batch_version = _forbidden_enqueue

        st = ChatState(
            chat_id=3001,
            stage=STAGE_PROCESSING,
            batch_id="batch-master-failed",
            batch_total_versions=3,
            versions_count=3,
            master_job_id="master-1",
            active_job_ids=["master-1"],
            job_order=["master-1"],
            next_version_to_enqueue=2,
        )

        await public_app.BlastBotApp._process_chat_job(app, st)

        assert st.stage == STAGE_WAIT_AUDIO
        assert app.credits_db.add_calls
        refund = app.credits_db.add_calls[-1]
        assert refund["reason"] == "generation_failed_refund"
        assert refund["amount"] == 3
        assert "job=master-1" in refund["admin_note"]

        assert any(e["event"] == "generation_failed" for e in app.credits_db.events)
        assert any(m["text"] == public_app._GENERATION_FAILED_USER_TEXT for m in app._bot.messages)
        assert app._manager_fail_calls
        assert app._manager_fail_calls[-1]["job_id"] == "master-1"
        assert app.store.saved_states and app.store.saved_states[-1].stage == STAGE_WAIT_AUDIO

    asyncio.run(_run())


def test_enqueue_next_version_failed_refunds_remaining_and_resets(
    monkeypatch,
) -> None:
    async def _run() -> None:
        monkeypatch.setattr(public_app, "_kb", lambda *rows: ("kb", rows))
        app = _new_app(
            jobs={
                "master-ok": {
                    "status": "SUCCEEDED",
                    "stage": "render",
                    "output_url": "s3://output-bucket/renders/master-ok/output.mp4",
                }
            }
        )

        async def _failing_enqueue(**_kwargs):
            raise RuntimeError("enqueue_next_version_failed_for_test")

        app._enqueue_batch_version = _failing_enqueue

        st = ChatState(
            chat_id=3002,
            stage=STAGE_PROCESSING,
            batch_id="batch-enqueue-fail",
            batch_audio_s3_url="s3://raw-bucket/audio.mp3",
            batch_total_versions=2,
            versions_count=2,
            master_job_id="master-ok",
            active_job_ids=["master-ok"],
            completed_job_ids=["master-ok"],
            job_order=["master-ok"],
            next_version_to_enqueue=2,
        )

        await public_app.BlastBotApp._process_chat_job(app, st)

        assert st.stage == STAGE_WAIT_AUDIO
        assert app.credits_db.add_calls
        refund = app.credits_db.add_calls[-1]
        assert refund["reason"] == "generation_failed_refund"
        assert refund["amount"] == 1
        assert "enqueue_next_version_failed" in refund["admin_note"]

        assert any(e["event"] == "generation_failed" for e in app.credits_db.events)
        assert any("enqueue_next_version" in e["detail"] for e in app.credits_db.events if e["event"] == "generation_failed")
        assert app._manager_fail_calls
        assert app._manager_fail_calls[-1]["job_id"] == "enqueue_next_version"
        assert app._manager_fail_calls[-1]["stage"] == "enqueue_next_version"
        assert any(m["text"] == public_app._GENERATION_FAILED_USER_TEXT for m in app._bot.messages)
        assert app.store.saved_states and app.store.saved_states[-1].stage == STAGE_WAIT_AUDIO

    asyncio.run(_run())


def test_partial_success_failed_batch_refunds_only_unsucceeded_versions(
    monkeypatch,
) -> None:
    async def _run() -> None:
        monkeypatch.setattr(public_app, "_kb", lambda *rows: ("kb", rows))
        app = _new_app(
            jobs={
                "ver-1": {
                    "status": "SUCCEEDED",
                    "stage": "render",
                    "output_url": "s3://output-bucket/renders/ver-1/output.mp4",
                },
                "ver-2": {
                    "status": "FAILED",
                    "stage": "render",
                    "error": "partial_batch_failed_for_test",
                },
            }
        )

        async def _forbidden_enqueue(**_kwargs):
            raise AssertionError("enqueue must not be called when batch already has FAILED rows")

        app._enqueue_batch_version = _forbidden_enqueue

        st = ChatState(
            chat_id=3003,
            stage=STAGE_PROCESSING,
            batch_id="batch-partial",
            batch_total_versions=2,
            versions_count=2,
            master_job_id="ver-1",
            active_job_ids=["ver-1", "ver-2"],
            completed_job_ids=["ver-1", "ver-2"],
            job_order=["ver-1", "ver-2"],
            next_version_to_enqueue=3,
        )

        await public_app.BlastBotApp._process_chat_job(app, st)

        assert st.stage == STAGE_WAIT_AUDIO
        assert app.credits_db.add_calls
        refund = app.credits_db.add_calls[-1]
        assert refund["reason"] == "generation_failed_refund"
        assert refund["amount"] == 1
        assert "job=ver-2" in refund["admin_note"]

        failed_events = [e for e in app.credits_db.events if e["event"] == "generation_failed"]
        done_events = [e for e in app.credits_db.events if e["event"] == "generation_done"]
        assert failed_events
        assert not done_events
        assert app._manager_fail_calls
        assert app._manager_fail_calls[-1]["job_id"] == "ver-2"
        assert any(m["text"] == public_app._GENERATION_FAILED_USER_TEXT for m in app._bot.messages)
        assert app.store.saved_states and app.store.saved_states[-1].stage == STAGE_WAIT_AUDIO

    asyncio.run(_run())
