"""Работа с SQLite базой данных."""

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

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


# ── Инициализация и миграции ──

async def init_db() -> bool:
    """Создать таблицы и начальные данные при первом запуске."""
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                initial_amount INTEGER NOT NULL DEFAULT 0,
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
                source TEXT DEFAULT 'other',
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

        # Миграции для существующих таблиц
        await _run_migrations(db)

        # Проверяем, есть ли уже данные
        cursor = await db.execute("SELECT COUNT(*) FROM envelopes")
        row = await cursor.fetchone()
        if row[0] == 0:
            logger.info("Первый запуск — инициализация данных")
            ts = now_msk().isoformat()

            for d in INITIAL_DEBTS:
                await db.execute(
                    "INSERT INTO debts (name, amount, initial_amount, rate, min_payment, deadline_day, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (d["name"], d["amount"], d["amount"], d["rate"], d["min_payment"], d["deadline_day"], ts),
                )

            for name, pct in ENVELOPE_RULES.items():
                await db.execute(
                    "INSERT INTO envelopes (name, balance, percentage) VALUES (?, 0, ?)",
                    (name, pct),
                )

            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                ("weekly_budget", "7000"),
            )

            await db.commit()
            logger.info("Начальные данные созданы")
            return True
        await db.commit()
        return False
    finally:
        await db.close()


async def _run_migrations(db: aiosqlite.Connection):
    """Миграции: добавление новых колонок к существующим таблицам."""
    # Проверяем колонки transactions
    cursor = await db.execute("PRAGMA table_info(transactions)")
    tx_cols = {row[1] for row in await cursor.fetchall()}

    if "source" not in tx_cols:
        await db.execute("ALTER TABLE transactions ADD COLUMN source TEXT DEFAULT 'other'")
        logger.info("Миграция: добавлена колонка source в transactions")

    # Проверяем колонки debts
    cursor = await db.execute("PRAGMA table_info(debts)")
    debt_cols = {row[1] for row in await cursor.fetchall()}

    if "initial_amount" not in debt_cols:
        await db.execute("ALTER TABLE debts ADD COLUMN initial_amount INTEGER DEFAULT 0")
        await db.execute("UPDATE debts SET initial_amount = amount WHERE initial_amount = 0")
        logger.info("Миграция: добавлена колонка initial_amount в debts")

    await db.commit()


# ── Долги ──

async def get_debts() -> list[dict]:
    """Активные долги (amount > 0)."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts WHERE amount > 0 ORDER BY rate DESC, amount DESC")
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_all_debts() -> list[dict]:
    """Все долги, включая закрытые."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts ORDER BY amount DESC, id ASC")
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_debt(debt_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_debt(name: str, amount: int, rate: float, min_payment: int, deadline_day: int | None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO debts (name, amount, initial_amount, rate, min_payment, deadline_day, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, amount, amount, rate, min_payment, deadline_day, now_msk().isoformat()),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def pay_debt(debt_id: int, amount: int) -> dict | None:
    """Внести платёж по долгу. Возвращает инфо или None если долг не найден."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,))
        debt = await cursor.fetchone()
        if not debt:
            return None

        d = dict(debt)
        old_amount = d["amount"]
        new_amount = max(0, old_amount - amount)
        await db.execute("UPDATE debts SET amount = ? WHERE id = ?", (new_amount, debt_id))

        await db.execute(
            "UPDATE envelopes SET balance = balance - ? WHERE name = 'debts'",
            (amount,),
        )

        ts = now_msk()
        await db.execute(
            "INSERT INTO transactions (type, amount, category, note, envelope, source, date, created_at) "
            "VALUES ('expense', ?, 'долг', ?, 'debts', 'other', ?, ?)",
            (amount, f"Платёж: {d['name']}", ts.strftime("%Y-%m-%d"), ts.isoformat()),
        )

        await db.commit()
        return {
            "name": d["name"],
            "initial_amount": d.get("initial_amount", old_amount),
            "old_amount": old_amount,
            "new_amount": new_amount,
            "paid": amount,
        }
    finally:
        await db.close()


async def remove_debt(debt_id: int) -> dict | None:
    """Удалить долг. Возвращает инфо удалённого долга или None."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,))
        debt = await cursor.fetchone()
        if not debt:
            return None
        d = dict(debt)
        await db.execute("DELETE FROM debts WHERE id = ?", (debt_id,))
        await db.commit()
        return d
    finally:
        await db.close()


async def update_debt_field(debt_id: int, field: str, value) -> bool:
    """Обновить одно поле долга. Возвращает True если обновлено."""
    allowed = {"name", "amount", "initial_amount", "rate", "min_payment", "deadline_day"}
    if field not in allowed:
        return False
    db = await get_db()
    try:
        await db.execute(f"UPDATE debts SET {field} = ? WHERE id = ?", (value, debt_id))
        await db.commit()
        return True
    finally:
        await db.close()


def estimate_weeks_to_close(amount: int, rate: float, min_payment: int) -> str:
    """Прогноз закрытия долга."""
    if amount <= 0:
        return "закрыт"
    if min_payment <= 0:
        return "без графика"

    monthly_rate = rate / 100 / 12
    remaining = amount
    months = 0

    while remaining > 0 and months < 600:
        interest = int(remaining * monthly_rate)
        principal = min_payment - interest
        if principal <= 0:
            return "не закроется (платёж не покрывает %)"
        remaining -= principal
        months += 1

    if months >= 600:
        return "очень долго"
    return f"~{months * 4} нед. (~{months} мес.)"


# ── Конверты ──

async def get_envelopes() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM envelopes ORDER BY percentage DESC")
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_envelope_rules() -> dict[str, int]:
    """Правила распределения: из settings или дефолт из config."""
    rules_json = await get_setting("envelope_rules", "")
    if rules_json:
        try:
            return json.loads(rules_json)
        except json.JSONDecodeError:
            pass
    return dict(ENVELOPE_RULES)


async def set_envelope_rules(rules: dict[str, int]):
    """Сохранить правила распределения в settings и синхронизировать с envelopes."""
    await set_setting("envelope_rules", json.dumps(rules))
    db = await get_db()
    try:
        for name, pct in rules.items():
            await db.execute("UPDATE envelopes SET percentage = ? WHERE name = ?", (pct, name))
        await db.commit()
    finally:
        await db.close()


async def distribute_income(amount: int, source: str, note: str) -> dict[str, int]:
    """Распределить доход по конвертам. Для source='personal' — всё в personal."""
    db = await get_db()
    try:
        if source == "personal":
            # Всё в конверт personal без распределения
            await db.execute(
                "UPDATE envelopes SET balance = balance + ? WHERE name = 'personal'",
                (amount,),
            )
            distribution = {"personal": amount}
        else:
            # Распределение по правилам
            rules = await get_envelope_rules()
            distribution = {}
            total_distributed = 0
            names = list(rules.keys())

            for i, env_name in enumerate(names):
                if i == len(names) - 1:
                    share = amount - total_distributed
                else:
                    share = int(amount * rules[env_name] / 100)
                distribution[env_name] = share
                total_distributed += share

                await db.execute(
                    "UPDATE envelopes SET balance = balance + ? WHERE name = ?",
                    (share, env_name),
                )

        # Записать транзакцию дохода
        ts = now_msk()
        await db.execute(
            "INSERT INTO transactions (type, amount, category, note, envelope, source, date, created_at) "
            "VALUES ('income', ?, 'доход', ?, NULL, ?, ?, ?)",
            (amount, note, source, ts.strftime("%Y-%m-%d"), ts.isoformat()),
        )

        await db.commit()
        return distribution
    finally:
        await db.close()


async def get_personal_balance() -> int:
    """Баланс конверта personal."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT balance FROM envelopes WHERE name = 'personal'")
        row = await cursor.fetchone()
        return row[0] if row else 0
    finally:
        await db.close()


# ── Транзакции ──

async def add_expense(amount: int, category: str, note: str, envelope: str = "personal") -> int:
    db = await get_db()
    try:
        ts = now_msk()
        cursor = await db.execute(
            "INSERT INTO transactions (type, amount, category, note, envelope, source, date, created_at) "
            "VALUES ('expense', ?, ?, ?, ?, 'other', ?, ?)",
            (amount, category, note, envelope, ts.strftime("%Y-%m-%d"), ts.isoformat()),
        )
        await db.execute(
            "UPDATE envelopes SET balance = balance - ? WHERE name = ?",
            (amount, envelope),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_week_expenses() -> list[dict]:
    """Расходы за текущую неделю (пн–вс)."""
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM transactions WHERE type = 'expense' AND date >= ? AND date <= ? ORDER BY date DESC",
            (monday.isoformat(), sunday.isoformat()),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_week_transactions() -> list[dict]:
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM transactions WHERE date >= ? AND date <= ? ORDER BY date DESC",
            (monday.isoformat(), sunday.isoformat()),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_week_income_by_source() -> dict[str, int]:
    """Доход за неделю по источникам."""
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(source, 'other') as src, SUM(amount) as total "
            "FROM transactions WHERE type = 'income' AND date >= ? AND date <= ? GROUP BY src",
            (monday.isoformat(), sunday.isoformat()),
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}
    finally:
        await db.close()


async def get_week_expenses_by_category() -> dict[str, int]:
    """Расходы за неделю по категориям."""
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(category, 'другое') as cat, SUM(amount) as total "
            "FROM transactions WHERE type = 'expense' AND date >= ? AND date <= ? GROUP BY cat",
            (monday.isoformat(), sunday.isoformat()),
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}
    finally:
        await db.close()


async def get_today_has_expenses() -> bool:
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


async def get_month_totals() -> tuple[int, int]:
    """(доход, расходы) за текущий месяц."""
    first_day = now_msk().date().replace(day=1).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT type, COALESCE(SUM(amount), 0) FROM transactions WHERE date >= ? GROUP BY type",
            (first_day,),
        )
        income = 0
        expense = 0
        for row in await cursor.fetchall():
            if row[0] == "income":
                income = row[1]
            elif row[0] == "expense":
                expense = row[1]
        return income, expense
    finally:
        await db.close()


async def get_month_income_by_source() -> dict[str, int]:
    first_day = now_msk().date().replace(day=1).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(source, 'other') as src, SUM(amount) as total "
            "FROM transactions WHERE type = 'income' AND date >= ? GROUP BY src",
            (first_day,),
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}
    finally:
        await db.close()


async def get_month_expenses_by_category() -> dict[str, int]:
    first_day = now_msk().date().replace(day=1).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(category, 'другое') as cat, SUM(amount) as total "
            "FROM transactions WHERE type = 'expense' AND date >= ? GROUP BY cat",
            (first_day,),
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}
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
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
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
        return [dict(r) for r in await cursor.fetchall()]
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


async def get_weekly_history(weeks: int = 4) -> list[dict]:
    """Расходы по категориям за последние N недель (включая текущую)."""
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    result = []
    db = await get_db()
    try:
        for i in range(weeks):
            w_start = monday - timedelta(weeks=i)
            w_end = w_start + timedelta(days=6)
            cursor = await db.execute(
                "SELECT COALESCE(category, 'другое') as cat, SUM(amount) as total "
                "FROM transactions WHERE type = 'expense' AND date >= ? AND date <= ? GROUP BY cat",
                (w_start.isoformat(), w_end.isoformat()),
            )
            by_cat = {row[0]: row[1] for row in await cursor.fetchall()}
            cursor2 = await db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'income' AND date >= ? AND date <= ?",
                (w_start.isoformat(), w_end.isoformat()),
            )
            income = (await cursor2.fetchone())[0]
            result.append({
                "week_start": w_start.isoformat(),
                "week_end": w_end.isoformat(),
                "income": income,
                "total_expense": sum(by_cat.values()) if by_cat else 0,
                "by_category": by_cat,
            })
    finally:
        await db.close()
    return result


async def get_full_financial_context() -> str:
    """Полный финансовый контекст для LLM."""
    from config import ENVELOPE_RULES

    today = now_msk().date()
    month_name_map = {
        1: "январь", 2: "февраль", 3: "март", 4: "апрель",
        5: "май", 6: "июнь", 7: "июль", 8: "август",
        9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
    }
    lines = [f"Дата: {today.isoformat()}, {month_name_map.get(today.month, '')}"]

    # Бюджет
    budget = await get_weekly_budget()
    spent = await get_spent_this_week()
    lines.append(f"\nБюджет на неделю: {budget}₽, потрачено: {spent}₽, остаток: {budget - spent}₽")

    # Конверты
    envelopes = await get_envelopes()
    lines.append("\nКонверты:")
    for e in envelopes:
        lines.append(f"  {e['name']}: {e['balance']}₽ ({e['percentage']}% от дохода)")

    # Долги
    debts = await get_debts()
    if debts:
        total_debt = await get_total_debt()
        lines.append(f"\nДолги (итого {total_debt}₽):")
        for d in debts:
            initial = d.get("initial_amount", d["amount"])
            paid_pct = max(0, int((initial - d["amount"]) / initial * 100)) if initial > 0 else 0
            line = f"  {d['name']}: {d['amount']}₽ из {initial}₽ (погашено {paid_pct}%)"
            if d.get("rate", 0) > 0:
                line += f", ставка {d['rate']}%"
            if d.get("min_payment", 0) > 0:
                line += f", мин.платёж {d['min_payment']}₽"
            if d.get("deadline_day"):
                line += f", дедлайн {d['deadline_day']}-го"
            lines.append(line)

    # Месяц
    total_income, total_expense = await get_month_totals()
    income_by_src = await get_month_income_by_source()
    expenses_by_cat = await get_month_expenses_by_category()
    lines.append(f"\nТекущий месяц ({month_name_map.get(today.month, '')}):")
    lines.append(f"  Доход: {total_income}₽")
    for src, amt in sorted(income_by_src.items(), key=lambda x: -x[1]):
        lines.append(f"    {src}: {amt}₽")
    lines.append(f"  Расход: {total_expense}₽")
    for cat, amt in sorted(expenses_by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"    {cat}: {amt}₽")
    lines.append(f"  Баланс: {total_income - total_expense}₽")

    # История за 4 недели
    history = await get_weekly_history(4)
    if history:
        lines.append("\nИстория расходов по неделям:")
        for w in history:
            cats = ", ".join(f"{k} {v}₽" for k, v in sorted(w["by_category"].items(), key=lambda x: -x[1]))
            lines.append(f"  {w['week_start']}..{w['week_end']}: доход {w['income']}₽, расход {w['total_expense']}₽")
            if cats:
                lines.append(f"    категории: {cats}")

    return "\n".join(lines)
