from __future__ import annotations

import asyncio
import logging

from core.telegram_polling import run_polling_with_retries


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeBot:
    def __init__(self) -> None:
        self.session = _FakeSession()


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls = 0
        self.bots: list[_FakeBot] = []

    async def start_polling(self, bot: _FakeBot) -> None:
        self.calls += 1
        self.bots.append(bot)
        if self.calls == 1:
            raise TimeoutError("proxy timeout")


def test_polling_retry_recreates_bot_and_closes_failed_session() -> None:
    dispatcher = _FakeDispatcher()

    def bot_factory() -> _FakeBot:
        return _FakeBot()

    asyncio.run(
        run_polling_with_retries(
            dispatcher,
            bot_factory,
            log=logging.getLogger("test.telegram_polling"),
            label="test-bot",
            initial_delay_s=0,
            max_delay_s=0,
        )
    )

    assert dispatcher.calls == 2
    assert len(dispatcher.bots) == 2
    assert dispatcher.bots[0].session.closed is True
    assert dispatcher.bots[1].session.closed is False
