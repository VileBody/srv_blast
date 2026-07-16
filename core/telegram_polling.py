from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any


BotFactory = Callable[[], Any]
BeforePollingHook = Callable[[Any], Awaitable[None]]


async def _close_bot_session(bot: Any) -> None:
    session = getattr(bot, "session", None)
    close = getattr(session, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def run_polling_with_retries(
    dispatcher: Any,
    bot_factory: BotFactory,
    *,
    log: logging.Logger,
    label: str,
    before_polling: BeforePollingHook | None = None,
    initial_delay_s: float = 2.0,
    max_delay_s: float = 60.0,
) -> None:
    """Run aiogram polling with process-local retries for transient egress failures."""

    delay_s = max(0.0, float(initial_delay_s))
    max_delay_s = max(delay_s, float(max_delay_s))
    attempt = 0
    while True:
        attempt += 1
        bot = bot_factory()
        try:
            if before_polling is not None:
                await before_polling(bot)
            await dispatcher.start_polling(bot)
            return
        except Exception as exc:
            log.exception(
                "telegram_polling_failed label=%s attempt=%d retry_in_s=%.1f err=%r",
                label,
                attempt,
                delay_s,
                exc,
            )
            try:
                await _close_bot_session(bot)
            except Exception as close_exc:
                log.warning(
                    "telegram_polling_session_close_failed label=%s err=%r",
                    label,
                    close_exc,
                )
            await asyncio.sleep(delay_s)
            delay_s = min(max_delay_s, delay_s * 1.7 if delay_s > 0 else 1.0)
