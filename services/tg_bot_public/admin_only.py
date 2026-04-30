from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from core.telegram_api import build_aiogram_session
from services.generation_runtime import GenerationRuntimeStore

from .admin_panel import start_admin_panel
from .config import SETTINGS, Settings
from .credits_db import CreditsDB
from .state_store import RedisChatStateStore
from .tbank_client import TBankClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_bot_public_admin: %(message)s",
)
log = logging.getLogger("tg_bot_public_admin")


async def run(settings: Settings = SETTINGS) -> None:
    if not settings.credits_db_url:
        raise RuntimeError("CREDITS_DB_URL (or POSTGRES_*) is required for tg_bot_public admin")

    credits_db = CreditsDB(settings.credits_db_url)
    state_store = RedisChatStateStore(settings)
    tbank = TBankClient(
        terminal_key=settings.tbank_terminal_key,
        password=settings.tbank_password,
        notify_url=settings.tbank_notify_url,
    ) if settings.tbank_terminal_key else None

    bot: Bot | None = None
    bot_ref: list = [None]
    tg_token = str(settings.tg_bot_token or "").strip()
    if tg_token:
        tg_proxy = str(settings.tg_file_proxy_url or "").strip()
        bot = Bot(
            token=tg_token,
            session=build_aiogram_session(api_env=settings.tg_bot_api_env, proxy_url=tg_proxy),
        )
        bot_ref[0] = bot
        log.info("telegram bot client enabled for admin actions")
    else:
        log.warning("TG_BOT_TOKEN is empty; admin actions that send Telegram messages will be unavailable")

    try:
        await credits_db.init()
        runtime_store = GenerationRuntimeStore(credits_db._pool_or_fail())
        await runtime_store.init_schema()
        await start_admin_panel(
            credits_db,
            state_store,
            settings,
            tbank_client=tbank,
            bot_ref=bot_ref,
        )
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception as exc:
                log.warning("telegram bot session close failed: %s", exc)
        await credits_db.close()
        await state_store.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
