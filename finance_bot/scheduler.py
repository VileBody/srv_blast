"""Планировщик: ежедневный пинг и еженедельная сводка."""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from config import (
    OWNER_TG_ID, DAILY_PING_HOUR, DAILY_PING_MINUTE,
    WEEKLY_SUMMARY_DAY, TIMEZONE,
)
from db import (
    get_today_has_expenses, get_week_transactions, get_envelopes,
    get_debts, get_week_income_by_source, get_week_expenses_by_category,
    estimate_weeks_to_close, now_msk, get_full_financial_context,
)
from grok_client import generate_weekly_summary
from templates import tpl_daily_ping, tpl_daily_ping_retry, tpl_no_data, tpl_weekly_summary

logger = logging.getLogger(__name__)
MSK = ZoneInfo(TIMEZONE)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=MSK)

    scheduler.add_job(
        daily_ping, "cron",
        hour=DAILY_PING_HOUR, minute=DAILY_PING_MINUTE,
        args=[bot], id="daily_ping", replace_existing=True,
    )
    scheduler.add_job(
        daily_ping_retry, "cron",
        hour=23, minute=30,
        args=[bot], id="daily_ping_retry", replace_existing=True,
    )
    scheduler.add_job(
        daily_no_data, "cron",
        hour=0, minute=0,
        args=[bot], id="daily_no_data", replace_existing=True,
    )
    scheduler.add_job(
        weekly_summary, "cron",
        day_of_week=WEEKLY_SUMMARY_DAY, hour=20, minute=0,
        args=[bot], id="weekly_summary", replace_existing=True,
    )

    return scheduler


async def daily_ping(bot: Bot):
    try:
        if await get_today_has_expenses():
            logger.info("Сегодня уже есть расходы — пропускаем пинг")
            return
        await bot.send_message(OWNER_TG_ID, tpl_daily_ping())
        logger.info("Ежедневный пинг отправлен")
    except Exception as e:
        logger.error(f"Ошибка ежедневного пинга: {e}")


async def daily_ping_retry(bot: Bot):
    try:
        if await get_today_has_expenses():
            return
        await bot.send_message(OWNER_TG_ID, tpl_daily_ping_retry())
        logger.info("Повторный пинг отправлен")
    except Exception as e:
        logger.error(f"Ошибка повторного пинга: {e}")


async def daily_no_data(bot: Bot):
    try:
        yesterday = (now_msk() - timedelta(days=1)).strftime("%Y-%m-%d")
        import aiosqlite
        from config import DB_PATH
        db = await aiosqlite.connect(DB_PATH)
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM transactions WHERE date = ?", (yesterday,)
            )
            row = await cursor.fetchone()
            if row[0] == 0:
                display = (now_msk() - timedelta(days=1)).strftime("%d.%m")
                await bot.send_message(OWNER_TG_ID, tpl_no_data(display))
                logger.info(f"День {yesterday} — нет данных")
        finally:
            await db.close()
    except Exception as e:
        logger.error(f"Ошибка записи 'нет данных': {e}")


async def weekly_summary(bot: Bot):
    try:
        transactions = await get_week_transactions()
        envelopes = await get_envelopes()
        debts = await get_debts()

        # Прогноз для каждого долга
        for d in debts:
            d["prognosis"] = estimate_weeks_to_close(d["amount"], d.get("rate", 0), d.get("min_payment", 0))

        income_by_src = await get_week_income_by_source()
        expenses_by_cat = await get_week_expenses_by_category()
        total_income = sum(income_by_src.values())
        total_expense = sum(expenses_by_cat.values())

        today = now_msk().date()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        # Рекомендация от Groq с полным контекстом
        financial_context = await get_full_financial_context()
        recommendation = await generate_weekly_summary(financial_context)

        text = tpl_weekly_summary(
            monday, sunday, income_by_src, total_income,
            expenses_by_cat, total_expense,
            total_income - total_expense, debts, recommendation,
        )
        await bot.send_message(OWNER_TG_ID, text)
        logger.info("Еженедельная сводка отправлена")
    except Exception as e:
        logger.error(f"Ошибка еженедельной сводки: {e}")
