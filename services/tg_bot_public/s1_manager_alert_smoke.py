"""Manual Telegram smoke test for the S1 manager-alert delivery path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot

from .broadcast_sender import build_s1_manager_alert, send_bot_message
from .config import Settings


async def _run() -> None:
    settings = Settings()
    if not settings.tg_bot_token:
        raise RuntimeError("TG_BOT_TOKEN is empty")
    if not settings.manager_chat_id:
        raise RuntimeError("MANAGER_CHAT_ID is empty or zero")
    if not settings.admin_panel_public_url:
        raise RuntimeError("ADMIN_PANEL_PUBLIC_URL is empty")

    now = datetime.now(timezone.utc)
    candidate = {
        "tg_id": settings.manager_chat_id,
        "username": "",
        "credits": 2,
        "cohort": "ci_smoke_test",
        "gens_done": 2,
        "last_rating": "high",
        "feedback_form_clicked": True,
        "survey_opened_at": now,
        "viewed_packages_list": True,
        "viewed_package_details": True,
        "last_active_at": now,
    }
    text = (
        "🧪 <b>SMOKE TEST — это не реальный лид</b>\n"
        "Проверяется доставка и формат S1-уведомления.\n\n"
        + build_s1_manager_alert(candidate, settings.admin_panel_public_url)
    )

    bot = Bot(token=settings.tg_bot_token)
    try:
        await send_bot_message(
            bot,
            settings.manager_chat_id,
            text=text,
            parse_mode="HTML",
        )
    finally:
        await bot.session.close()
    print("S1 manager-alert smoke delivered successfully")


if __name__ == "__main__":
    asyncio.run(_run())
