"""Хендлеры Telegram-бота."""

import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.types import Message

from config import OWNER_TG_ID, ENVELOPE_NAMES
from db import (
    get_debts, add_debt, pay_debt, get_envelopes, distribute_income,
    add_expense, get_week_expenses, get_goals, add_goal, get_total_debt,
    get_spent_this_week, get_weekly_budget, set_setting, estimate_weeks_to_close,
)
from grok_client import parse_expenses

logger = logging.getLogger(__name__)
router = Router()


class OwnerFilter(BaseFilter):
    """Фильтр: пропускать только сообщения от владельца."""
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == OWNER_TG_ID


owner = OwnerFilter()


def _fmt_money(amount: int) -> str:
    """Форматирование суммы с разделителями."""
    if amount < 0:
        return f"-{abs(amount):,}₽".replace(",", " ")
    return f"{amount:,}₽".replace(",", " ")


# ── /start ──

@router.message(CommandStart(), owner)
async def cmd_start(message: Message):
    envelopes = await get_envelopes()
    total_debt = await get_total_debt()
    debts = await get_debts()

    env_lines = "\n".join(
        f"  {ENVELOPE_NAMES.get(e['name'], e['name'])}: {_fmt_money(e['balance'])}"
        for e in envelopes
    )

    nearest = ""
    for d in debts:
        if d["deadline_day"]:
            nearest = f"\n⏰ Ближайший дедлайн: {d['name']} — {d['deadline_day']}-го числа"
            break

    text = (
        f"Привет, Никит! 👋\n\n"
        f"📊 Конверты:\n{env_lines}\n\n"
        f"💳 Общий долг: {_fmt_money(total_debt)}{nearest}\n\n"
        f"Кидай траты текстом в любое время, я разберу.\n"
        f"Команды: /status /debts /goals /week /envelopes"
    )
    await message.answer(text)


# ── /status ──

@router.message(Command("status"), owner)
async def cmd_status(message: Message):
    envelopes = await get_envelopes()
    total_debt = await get_total_debt()
    goals = await get_goals()
    budget = await get_weekly_budget()
    spent = await get_spent_this_week()

    env_lines = "\n".join(
        f"  {ENVELOPE_NAMES.get(e['name'], e['name'])}: {_fmt_money(e['balance'])} ({e['percentage']}%)"
        for e in envelopes
    )

    goals_lines = ""
    if goals:
        goals_lines = "\n\n🎯 Цели:\n" + "\n".join(
            f"  {g['name']}: {_fmt_money(g['saved'])} / {_fmt_money(g['target_amount'])}"
            + (f" (до {g['deadline']})" if g.get("deadline") else "")
            for g in goals
        )

    text = (
        f"📊 Статус:\n\n"
        f"Конверты:\n{env_lines}\n\n"
        f"💳 Общий долг: {_fmt_money(total_debt)}\n"
        f"💰 Бюджет на неделю: {_fmt_money(spent)} / {_fmt_money(budget)}"
        f"{goals_lines}"
    )
    await message.answer(text)


# ── /debts ──

@router.message(Command("debts"), owner)
async def cmd_debts(message: Message):
    debts = await get_debts()
    if not debts:
        await message.answer("🎉 Долгов нет!")
        return

    lines = []
    for d in debts:
        prognosis = estimate_weeks_to_close(d["amount"], d["rate"], d["min_payment"])
        line = f"#{d['id']} {d['name']}: {_fmt_money(d['amount'])}"
        if d["rate"] > 0:
            line += f" ({d['rate']}% годовых)"
        if d["min_payment"] > 0:
            line += f"\n   Мин. платёж: {_fmt_money(d['min_payment'])}"
        if d["deadline_day"]:
            line += f" | дедлайн: {d['deadline_day']}-е"
        line += f"\n   Прогноз: {prognosis}"
        lines.append(line)

    text = "💳 Долги:\n\n" + "\n\n".join(lines)
    await message.answer(text)


# ── /adddebt ──

@router.message(Command("adddebt"), owner)
async def cmd_adddebt(message: Message):
    args = message.text.split(maxsplit=5)
    if len(args) < 6:
        await message.answer(
            "Формат: /adddebt <название> <сумма> <процент> <мин_платеж> <день_дедлайна>\n"
            "Пример: /adddebt Займ 50000 12 5000 15\n"
            "Если дедлайна нет — поставь 0"
        )
        return

    try:
        name = args[1]
        amount = int(args[2])
        rate = float(args[3])
        min_pay = int(args[4])
        deadline = int(args[5]) if args[5] != "0" else None
    except (ValueError, IndexError):
        await message.answer("❌ Ошибка в параметрах. Проверь формат.")
        return

    debt_id = await add_debt(name, amount, rate, min_pay, deadline)
    await message.answer(f"✅ Долг добавлен: #{debt_id} {name} — {_fmt_money(amount)}")


# ── /paydebt ──

@router.message(Command("paydebt"), owner)
async def cmd_paydebt(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Формат: /paydebt <id> <сумма>\nПример: /paydebt 1 10000")
        return

    try:
        debt_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ Ошибка в параметрах.")
        return

    result = await pay_debt(debt_id, amount)
    if not result:
        await message.answer(f"❌ Долг #{debt_id} не найден.")
        return

    await message.answer(
        f"✅ Платёж {_fmt_money(amount)} по долгу «{result['name']}»\n"
        f"Остаток: {_fmt_money(result['new_amount'])}"
    )


# ── /income ──

@router.message(Command("income"), owner)
async def cmd_income(message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("Формат: /income <сумма> [источник]\nПример: /income 50000 зарплата")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("❌ Сумма должна быть числом.")
        return

    source = args[2] if len(args) > 2 else "не указан"
    distribution = await distribute_income(amount, source)

    lines = "\n".join(
        f"  {ENVELOPE_NAMES.get(k, k)}: {_fmt_money(v)}"
        for k, v in distribution.items()
    )
    await message.answer(f"💰 Доход: {_fmt_money(amount)}\nРаспределение:\n{lines}")


# ── /goals ──

@router.message(Command("goals"), owner)
async def cmd_goals(message: Message):
    goals = await get_goals()
    if not goals:
        await message.answer("Целей пока нет. Добавь: /addgoal <название> <сумма> <дедлайн>")
        return

    lines = []
    for g in goals:
        pct = int(g["saved"] / g["target_amount"] * 100) if g["target_amount"] > 0 else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        line = f"#{g['id']} {g['name']}: {_fmt_money(g['saved'])} / {_fmt_money(g['target_amount'])} [{bar}] {pct}%"
        if g.get("deadline"):
            line += f"\n   Дедлайн: {g['deadline']}"
        lines.append(line)

    text = "🎯 Цели:\n\n" + "\n\n".join(lines)
    await message.answer(text)


# ── /addgoal ──

@router.message(Command("addgoal"), owner)
async def cmd_addgoal(message: Message):
    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        await message.answer(
            "Формат: /addgoal <название> <сумма> [дедлайн]\n"
            "Пример: /addgoal Ноутбук 80000 2026-06-01"
        )
        return

    try:
        name = args[1]
        target = int(args[2])
        deadline = args[3] if len(args) > 3 else None
    except ValueError:
        await message.answer("❌ Ошибка в параметрах.")
        return

    goal_id = await add_goal(name, target, deadline)
    await message.answer(f"✅ Цель добавлена: #{goal_id} {name} — {_fmt_money(target)}")


# ── /setbudget ──

@router.message(Command("setbudget"), owner)
async def cmd_setbudget(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Формат: /setbudget <сумма>\nПример: /setbudget 10000")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("❌ Сумма должна быть числом.")
        return

    await set_setting("weekly_budget", str(amount))
    await message.answer(f"✅ Недельный бюджет: {_fmt_money(amount)}")


# ── /week ──

@router.message(Command("week"), owner)
async def cmd_week(message: Message):
    expenses = await get_week_expenses()
    budget = await get_weekly_budget()
    spent = await get_spent_this_week()
    remaining = budget - spent

    if not expenses:
        await message.answer(
            f"📊 Неделя: трат пока нет.\n"
            f"Бюджет: {_fmt_money(budget)}"
        )
        return

    # Группировка по категориям
    by_cat: dict[str, int] = {}
    for e in expenses:
        cat = e.get("category", "другое")
        by_cat[cat] = by_cat.get(cat, 0) + e["amount"]

    cat_lines = "\n".join(f"  {k}: {_fmt_money(v)}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1]))

    total = sum(e["amount"] for e in expenses)
    text = (
        f"📊 Неделя:\n\n"
        f"Расходы: {_fmt_money(total)}\n{cat_lines}\n\n"
        f"Бюджет (личные): {_fmt_money(spent)} / {_fmt_money(budget)}\n"
        f"Остаток: {_fmt_money(remaining)}"
    )
    await message.answer(text)


# ── /envelopes ──

@router.message(Command("envelopes"), owner)
async def cmd_envelopes(message: Message):
    envelopes = await get_envelopes()
    lines = "\n".join(
        f"  {ENVELOPE_NAMES.get(e['name'], e['name'])}: {_fmt_money(e['balance'])} ({e['percentage']}% от дохода)"
        for e in envelopes
    )
    await message.answer(f"📦 Конверты:\n\n{lines}")


# ── Свободный текст → парсинг трат ──

@router.message(F.text & ~F.text.startswith("/"), owner)
async def handle_free_text(message: Message):
    """Любой текст без / — считаем вводом трат."""
    text = message.text.strip()
    if not text:
        return

    budget = await get_weekly_budget()
    spent = await get_spent_this_week()

    # Парсим через Grok
    expenses = await parse_expenses(text, budget, spent)

    if not expenses:
        await message.answer("Не нашёл трат в сообщении. Если хочешь записать — напиши сумму и категорию.")
        return

    # Записываем в БД
    total = 0
    lines = []
    for exp in expenses:
        await add_expense(exp["amount"], exp["category"], exp["note"])
        total += exp["amount"]
        lines.append(f"  {exp['category']}: {_fmt_money(exp['amount'])}" + (f" ({exp['note']})" if exp["note"] else ""))

    new_spent = spent + total
    remaining = budget - new_spent

    text_response = (
        f"✅ Записал:\n" + "\n".join(lines) + "\n\n"
        f"Итого за день: {_fmt_money(total)}\n"
        f"На неделю: {_fmt_money(new_spent)} / {_fmt_money(budget)}\n"
        f"Остаток: {_fmt_money(remaining)}"
    )
    await message.answer(text_response)
