"""Работа с SQLite базой данных."""

import aiosqlite
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import DB_PATH, INITIAL_DEBTS, ENVELOPE_RULES, TIMEZONE

logger = logging.getLogger(__name__)
MSK = ZoneInfo(TIMEZONE)


def now_msk() -> datetime:
    return datetime.now(MSK)


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    """Создать таблицы и начальные данные при первом запуске."""
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                rate REAL NOT NULL DEFAULT 0,
                min_payment INTEGER NOT NULL DEFAULT 0,
                deadline_day INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS envelopes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                balance INTEGER NOT NULL DEFAULT 0,
                percentage INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                amount INTEGER NOT NULL,
                category TEXT,
                note TEXT,
                envelope TEXT,
                date TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_amount INTEGER NOT NULL,
                saved INTEGER NOT NULL DEFAULT 0,
                deadline TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Проверяем, есть ли уже данные
        cursor = await db.execute("SELECT COUNT(*) FROM envelopes")
        row = await cursor.fetchone()
        if row[0] == 0:
            logger.info("Первый запуск — инициализация данных")
            ts = now_msk().isoformat()

            # Начальные долги
            for d in INITIAL_DEBTS:
                await db.execute(
                    "INSERT INTO debts (name, amount, rate, min_payment, deadline_day, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (d["name"], d["amount"], d["rate"], d["min_payment"], d["deadline_day"], ts),
                )

            # Конверты
            for name, pct in ENVELOPE_RULES.items():
                await db.execute(
                    "INSERT INTO envelopes (name, balance, percentage) VALUES (?, 0, ?)",
                    (name, pct),
                )

            # Дефолтные настройки
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                ("weekly_budget", "7000"),
            )

            await db.commit()
            logger.info("Начальные данные созданы")
            return True  # первый запуск
        await db.commit()
        return False
    finally:
        await db.close()


# ── Долги ──

async def get_debts() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts WHERE amount > 0 ORDER BY rate DESC, amount DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def add_debt(name: str, amount: int, rate: float, min_payment: int, deadline_day: int | None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO debts (name, amount, rate, min_payment, deadline_day, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, amount, rate, min_payment, deadline_day, now_msk().isoformat()),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def pay_debt(debt_id: int, amount: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,))
        debt = await cursor.fetchone()
        if not debt:
            return None

        new_amount = max(0, dict(debt)["amount"] - amount)
        await db.execute("UPDATE debts SET amount = ? WHERE id = ?", (new_amount, debt_id))

        # Списать из конверта "debts"
        await db.execute(
            "UPDATE envelopes SET balance = balance - ? WHERE name = 'debts'",
            (amount,),
        )

        # Записать транзакцию
        await db.execute(
            "INSERT INTO transactions (type, amount, category, note, envelope, date, created_at) "
            "VALUES ('expense', ?, 'долг', ?, 'debts', ?, ?)",
            (amount, f"Платёж по: {dict(debt)['name']}", now_msk().strftime("%Y-%m-%d"), now_msk().isoformat()),
        )

        await db.commit()
        result = dict(debt)
        result["new_amount"] = new_amount
        result["paid"] = amount
        return result
    finally:
        await db.close()


def estimate_weeks_to_close(amount: int, rate: float, min_payment: int) -> str:
    """Прогноз закрытия долга в неделях."""
    if amount <= 0:
        return "закрыт"
    if min_payment <= 0 and rate <= 0:
        return "без графика"

    monthly_rate = rate / 100 / 12
    remaining = amount
    months = 0
    max_months = 600  # 50 лет максимум

    if min_payment <= 0:
        return "без графика"

    while remaining > 0 and months < max_months:
        interest = int(remaining * monthly_rate)
        principal = min_payment - interest
        if principal <= 0:
            return "не закроется (платёж не покрывает %)"
        remaining -= principal
        months += 1

    if months >= max_months:
        return "очень долго"

    weeks = months * 4
    return f"~{weeks} нед. (~{months} мес.)"


# ── Конверты ──

async def get_envelopes() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM envelopes ORDER BY percentage DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def distribute_income(amount: int, source: str) -> dict:
    """Распределить доход по конвертам и записать транзакции."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT name, percentage FROM envelopes")
        envelopes = await cursor.fetchall()

        distribution = {}
        total_distributed = 0

        envelope_list = [dict(e) for e in envelopes]
        for i, env in enumerate(envelope_list):
            if i == len(envelope_list) - 1:
                # Последний конверт забирает остаток (избегаем ошибок округления)
                share = amount - total_distributed
            else:
                share = int(amount * env["percentage"] / 100)
            distribution[env["name"]] = share
            total_distributed += share

            await db.execute(
                "UPDATE envelopes SET balance = balance + ? WHERE name = ?",
                (share, env["name"]),
            )

        # Записать транзакцию дохода
        ts = now_msk()
        await db.execute(
            "INSERT INTO transactions (type, amount, category, note, envelope, date, created_at) "
            "VALUES ('income', ?, 'доход', ?, NULL, ?, ?)",
            (amount, source, ts.strftime("%Y-%m-%d"), ts.isoformat()),
        )

        await db.commit()
        return distribution
    finally:
        await db.close()


# ── Транзакции ──

async def add_expense(amount: int, category: str, note: str, envelope: str = "personal") -> int:
    """Добавить расход."""
    db = await get_db()
    try:
        ts = now_msk()
        cursor = await db.execute(
            "INSERT INTO transactions (type, amount, category, note, envelope, date, created_at) "
            "VALUES ('expense', ?, ?, ?, ?, ?, ?)",
            (amount, category, note, envelope, ts.strftime("%Y-%m-%d"), ts.isoformat()),
        )
        # Списать из конверта personal
        await db.execute(
            "UPDATE envelopes SET balance = balance - ? WHERE name = ?",
            (amount, envelope),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_week_expenses() -> list[dict]:
    """Расходы за текущую неделю (пн-вс)."""
    today = now_msk().date()
    # Начало недели — понедельник
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM transactions WHERE type = 'expense' AND date >= ? AND date <= ? "
            "ORDER BY date DESC",
            (monday.isoformat(), sunday.isoformat()),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_week_transactions() -> list[dict]:
    """Все транзакции за текущую неделю."""
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM transactions WHERE date >= ? AND date <= ? ORDER BY date DESC",
            (monday.isoformat(), sunday.isoformat()),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_today_has_expenses() -> bool:
    """Есть ли расходы за сегодня."""
    today = now_msk().strftime("%Y-%m-%d")
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM transactions WHERE type = 'expense' AND date = ?",
            (today,),
        )
        row = await cursor.fetchone()
        return row[0] > 0
    finally:
        await db.close()


# ── Настройки ──

async def get_setting(key: str, default: str = "0") -> str:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default
    finally:
        await db.close()


async def set_setting(key: str, value: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        await db.commit()
    finally:
        await db.close()


# ── Цели ──

async def get_goals() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM goals ORDER BY deadline ASC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def add_goal(name: str, target_amount: int, deadline: str | None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO goals (name, target_amount, saved, deadline, created_at) VALUES (?, ?, 0, ?, ?)",
            (name, target_amount, deadline, now_msk().isoformat()),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


# ── Статистика ──

async def get_total_debt() -> int:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COALESCE(SUM(amount), 0) FROM debts WHERE amount > 0")
        row = await cursor.fetchone()
        return row[0]
    finally:
        await db.close()


async def get_spent_this_week() -> int:
    """Сумма расходов за неделю из конверта personal."""
    expenses = await get_week_expenses()
    return sum(e["amount"] for e in expenses if e.get("envelope") == "personal")


async def get_weekly_budget() -> int:
    return int(await get_setting("weekly_budget", "7000"))
