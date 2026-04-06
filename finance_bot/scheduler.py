"""Планировщик: ежедневный пинг и еженедельная сводка."""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from config import (
    OWNER_TG_ID, DAILY_PING_HOUR, DAILY_PING_MINUTE,
    WEEKLY_SUMMARY_DAY, TIMEZONE, ENVELOPE_NAMES,
)
from db import (
    get_today_has_expenses, get_week_transactions, get_envelopes,
    get_debts, get_spent_this_week, get_weekly_budget,
    estimate_weeks_to_close, now_msk,
)
from grok_client import generate_weekly_summary

logger = logging.getLogger(__name__)
MSK = ZoneInfo(TIMEZONE)


def _fmt_money(amount: int) -> str:
    if amount < 0:
        return f"-{abs(amount):,}₽".replace(",", " ")
    return f"{amount:,}₽".replace(",", " ")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Настроить и вернуть планировщик."""
    scheduler = AsyncIOScheduler(timezone=MSK)

    # Ежедневный пинг в 23:00
    scheduler.add_job(
        daily_ping,
        "cron",
        hour=DAILY_PING_HOUR,
        minute=DAILY_PING_MINUTE,
        args=[bot],
        id="daily_ping",
        replace_existing=True,
    )

    # Повторный пинг в 23:30 (если не ответил)
    scheduler.add_job(
        daily_ping_retry,
        "cron",
        hour=23,
        minute=30,
        args=[bot],
        id="daily_ping_retry",
        replace_existing=True,
    )

    # Запись "нет данных" в 00:00
    scheduler.add_job(
        daily_no_data,
        "cron",
        hour=0,
        minute=0,
        args=[bot],
        id="daily_no_data",
        replace_existing=True,
    )

    # Еженедельная сводка (воскресенье 20:00)
    # day_of_week: 0=пн, 6=вс
    scheduler.add_job(
        weekly_summary,
        "cron",
        day_of_week=WEEKLY_SUMMARY_DAY,
        hour=20,
        minute=0,
        args=[bot],
        id="weekly_summary",
        replace_existing=True,
    )

    return scheduler


async def daily_ping(bot: Bot):
    """Ежедневный пинг: спросить про траты."""
    try:
        has_expenses = await get_today_has_expenses()
        if has_expenses:
            logger.info("Сегодня уже есть расходы — пропускаем пинг")
            return

        budget = await get_weekly_budget()
        spent = await get_spent_this_week()
        remaining = budget - spent

        await bot.send_message(
            OWNER_TG_ID,
            f"Никит, на что сегодня потратился? Напиши свободным текстом, я разберу 📝\n\n"
            f"Остаток на неделю: {_fmt_money(remaining)} из {_fmt_money(budget)}",
        )
        logger.info("Ежедневный пинг отправлен")
    except Exception as e:
        logger.error(f"Ошибка ежедневного пинга: {e}")


async def daily_ping_retry(bot: Bot):
    """Повторный пинг в 23:30, если за сегодня нет расходов."""
    try:
        has_expenses = await get_today_has_expenses()
        if has_expenses:
            return

        await bot.send_message(
            OWNER_TG_ID,
            "Напомню — скинь траты за сегодня, если были 🔔",
        )
        logger.info("Повторный пинг отправлен")
    except Exception as e:
        logger.error(f"Ошибка повторного пинга: {e}")


async def daily_no_data(bot: Bot):
    """В полночь: если за вчера нет данных — уведомить."""
    try:
        # Проверяем вчерашний день
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
                await bot.send_message(
                    OWNER_TG_ID,
                    f"📝 За {yesterday} нет данных — записал как день без трат.",
                )
                logger.info(f"День {yesterday} — нет данных")
        finally:
            await db.close()
    except Exception as e:
        logger.error(f"Ошибка записи 'нет данных': {e}")


async def weekly_summary(bot: Bot):
    """Еженедельная сводка."""
    try:
        transactions = await get_week_transactions()
        envelopes = await get_envelopes()
        debts = await get_debts()

        # Подсчёт
        income_total = sum(t["amount"] for t in transactions if t["type"] == "income")
        expense_total = sum(t["amount"] for t in transactions if t["type"] == "expense")

        expenses_by_category: dict[str, int] = {}
        for t in transactions:
            if t["type"] == "expense":
                cat = t.get("category", "другое")
                expenses_by_category[cat] = expenses_by_category.get(cat, 0) + t["amount"]

        # Даты недели
        today = now_msk().date()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        # Формируем текст
        cat_lines = "\n".join(
            f"  {k}: {_fmt_money(v)}"
            for k, v in sorted(expenses_by_category.items(), key=lambda x: -x[1])
        )

        env_lines = "\n".join(
            f"  {ENVELOPE_NAMES.get(e['name'], e['name'])}: {_fmt_money(e['balance'])}"
            for e in envelopes
        )

        debt_lines = "\n".join(
            f"  {d['name']}: {_fmt_money(d['amount'])} ({estimate_weeks_to_close(d['amount'], d['rate'], d['min_payment'])})"
            for d in debts
        )

        # Рекомендация от Grok
        recommendation = await generate_weekly_summary(
            income_total, expense_total, expenses_by_category, envelopes, debts,
        )

        text = (
            f"📊 Неделя {monday.strftime('%d.%m')}–{sunday.strftime('%d.%m')}:\n\n"
            f"Доход: {_fmt_money(income_total)}\n"
            f"Расходы: {_fmt_money(expense_total)}\n"
            f"{cat_lines}\n\n"
            f"Конверты:\n{env_lines}\n\n"
            f"Долги:\n{debt_lines}"
        )

        if recommendation:
            text += f"\n\n💡 {recommendation}"

        await bot.send_message(OWNER_TG_ID, text)
        logger.info("Еженедельная сводка отправлена")
    except Exception as e:
        logger.error(f"Ошибка еженедельной сводки: {e}")
