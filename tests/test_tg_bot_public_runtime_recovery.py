from __future__ import annotations

import asyncio
from contextlib import suppress

from services.tg_bot_public import app as public_app


class _FakeStore:
    def __init__(self, *, restored: asyncio.Event) -> None:
        self._restored = restored

    async def list_waiting_referral(self):
        assert self._restored.is_set()
        return []

    async def list_processing_candidates(self):
        assert self._restored.is_set()
        return []


def test_recovery_loop_restores_durable_runs_periodically() -> None:
    async def _run() -> int:
        app = object.__new__(public_app.BlastBotApp)
        restored = asyncio.Event()
        calls = 0

        async def _restore_runtime_processing_states() -> None:
            nonlocal calls
            calls += 1
            restored.set()

        app.store = _FakeStore(restored=restored)
        app._restore_runtime_processing_states = _restore_runtime_processing_states
        app._recovery_interval_s = lambda: 3600.0

        task = asyncio.create_task(app._recovery_loop())
        await asyncio.wait_for(restored.wait(), timeout=1.0)
        await asyncio.sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return calls

    assert asyncio.run(_run()) == 1
