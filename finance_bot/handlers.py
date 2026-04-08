"""Хендлеры Telegram-бота с FSM для интерактивных диалогов."""

import logging
from datetime import timedelta

from aiogram import Router, F
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from config import OWNER_TG_ID, ENVELOPE_RULES
from db import (
    get_debts, get_all_debts, get_debt, add_debt, pay_debt, remove_debt, update_debt_field,
    get_envelopes, get_envelope_rules, set_envelope_rules, distribute_income, get_personal_balance,
    add_expense, delete_expense, get_recent_expenses,
    get_week_expenses, get_week_income_by_source, get_week_expenses_by_category,
    get_goals, add_goal, get_total_debt, get_spent_this_week, get_weekly_budget,
    set_setting, estimate_weeks_to_close, get_month_totals, get_month_income_by_source,
    get_month_expenses_by_category, now_msk, get_full_financial_context,
)
from grok_client import parse_expenses, ask_question
from templates import (
    esc, money, tpl_start, tpl_status, tpl_balance, tpl_debts, tpl_week,
    tpl_income_distributed, tpl_income_personal, tpl_split_show, tpl_split_updated,
    tpl_expense_confirm, tpl_paydebt_confirm, tpl_paydebt_closed, tpl_adddebt_confirm,
    tpl_removedebt_confirm, tpl_envelopes, tpl_error_input, tpl_error_split_sum,
    tpl_error_grok, progress_bar, MONTH_NAMES,
)

logger = logging.getLogger(__name__)
router = Router()

# Маппинг источников дохода (рус → ключ)
SOURCE_MAP = {
    "blast": "blast", "бласт": "blast",
    "freelance": "freelance", "фриланс": "freelance",
    "personal": "personal", "личное": "personal", "личные": "personal",
    "other": "other", "другое": "other",
}

# Маппинг конвертов (рус → ключ)
ENVELOPE_MAP = {
    "долги": "debts", "debts": "debts",
    "операционка": "operations", "operations": "operations",
    "личные": "personal", "personal": "personal",
    "подушка": "savings", "savings": "savings",
}


# ── Фильтр владельца ──

class OwnerFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == OWNER_TG_ID


owner = OwnerFilter()


# ── FSM States ──

class AddDebtFSM(StatesGroup):
    name = State()
    amount = State()
    rate = State()
    min_payment = State()
    deadline_day = State()


class PayDebtFSM(StatesGroup):
    select = State()
    amount = State()


class RemoveDebtFSM(StatesGroup):
    select = State()
    confirm = State()


class EditDebtFSM(StatesGroup):
    select = State()
    field = State()
    value = State()


# ── Утилиты ──

async def _debts_numbered_list() -> tuple[list[dict], str]:
    """Возвращает (debts, текст списка с номерами)."""
    debts = await get_debts()
    if not debts:
        return [], esc("Нет активных долгов.")
    lines = []
    for i, d in enumerate(debts, 1):
        lines.append(f"{esc(f'{i}.')} {esc(d['name'])} — {money(d['amount'])}")
    return debts, "\n".join(lines)


async def _debts_with_prognosis() -> list[dict]:
    """Долги с прогнозом закрытия."""
    debts = await get_debts()
    for d in debts:
        d["prognosis"] = estimate_weeks_to_close(d["amount"], d.get("rate", 0), d.get("min_payment", 0))
    return debts


# ═══════════════════════════════════
# /cancel — отмена FSM
# ═══════════════════════════════════

@router.message(Command("cancel"), owner)
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    await state.clear()
    if current:
        await message.answer(esc("Отменено."))
    else:
        await message.answer(esc("Нечего отменять."))


# ═══════════════════════════════════
# /start
# ═══════════════════════════════════

@router.message(CommandStart(), owner)
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    total_debt = await get_total_debt()
    total_income, total_expense = await get_month_totals()
    balance = total_income - total_expense

    debts = await get_debts()
    nearest = None
    for d in debts:
        if d.get("deadline_day"):
            nearest = d
            break

    await message.answer(tpl_start(balance, total_debt, nearest))


# ═══════════════════════════════════
# /status
# ═══════════════════════════════════

@router.message(Command("status"), owner)
async def cmd_status(message: Message, state: FSMContext):
    await state.clear()
    envelopes = await get_envelopes()
    debts = await get_debts()
    total_debt = await get_total_debt()
    budget = await get_weekly_budget()
    spent = await get_spent_this_week()
    await message.answer(tpl_status(envelopes, debts, total_debt, budget, spent))


# ═══════════════════════════════════
# /balance
# ═══════════════════════════════════

@router.message(Command("balance"), owner)
async def cmd_balance(message: Message, state: FSMContext):
    await state.clear()
    today = now_msk().date()
    month_name = MONTH_NAMES.get(today.month, str(today.month))

    total_income, total_expense = await get_month_totals()
    income_by_src = await get_month_income_by_source()
    expenses_by_cat = await get_month_expenses_by_category()
    debts = await get_debts()

    await message.answer(tpl_balance(
        month_name=month_name,
        income_by_source=income_by_src,
        expenses_by_category=expenses_by_cat,
        total_income=total_income,
        total_expense=total_expense,
        net_balance=total_income - total_expense,
        debts=debts,
    ))


# ═══════════════════════════════════
# /debts
# ═══════════════════════════════════

@router.message(Command("debts"), owner)
async def cmd_debts(message: Message, state: FSMContext):
    await state.clear()
    debts = await _debts_with_prognosis()
    total_debt = await get_total_debt()
    await message.answer(tpl_debts(debts, total_debt))


# ═══════════════════════════════════
# /adddebt — инлайн или интерактивный
# ═══════════════════════════════════

@router.message(Command("adddebt"), owner)
async def cmd_adddebt(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split(maxsplit=5)

    if len(args) >= 6:
        # Инлайн-режим
        try:
            name = args[1]
            amount = int(args[2])
            rate = float(args[3])
            min_pay = int(args[4])
            deadline = int(args[5]) if args[5] != "0" else None
        except (ValueError, IndexError):
            await message.answer(tpl_error_input("/adddebt <название> <сумма> <процент> <мин_платеж> <дедлайн>"))
            return
        await _finish_adddebt(message, name, amount, rate, min_pay, deadline)
    else:
        # Интерактивный режим
        await state.set_state(AddDebtFSM.name)
        await message.answer(esc("Название долга?"))


@router.message(AddDebtFSM.name, owner)
async def adddebt_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddDebtFSM.amount)
    await message.answer(esc("Сумма?"))


@router.message(AddDebtFSM.amount, owner)
async def adddebt_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи число."))
        return
    await state.update_data(amount=amount)
    await state.set_state(AddDebtFSM.rate)
    await message.answer(esc("Процент годовых? (0 если без процентов)"))


@router.message(AddDebtFSM.rate, owner)
async def adddebt_rate(message: Message, state: FSMContext):
    try:
        rate = float(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи число."))
        return
    await state.update_data(rate=rate)
    await state.set_state(AddDebtFSM.min_payment)
    await message.answer(esc("Минимальный платёж в месяц? (0 если нет)"))


@router.message(AddDebtFSM.min_payment, owner)
async def adddebt_min_payment(message: Message, state: FSMContext):
    try:
        mp = int(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи число."))
        return
    await state.update_data(min_payment=mp)
    await state.set_state(AddDebtFSM.deadline_day)
    await message.answer(esc("Дедлайн — число месяца? (0 или 'нет' если нет)"))


@router.message(AddDebtFSM.deadline_day, owner)
async def adddebt_deadline(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    deadline = None
    if text not in ("0", "нет", "no", "-", ""):
        try:
            deadline = int(text)
        except ValueError:
            await message.answer(esc("Введи число или 'нет'."))
            return

    data = await state.get_data()
    await state.clear()
    await _finish_adddebt(message, data["name"], data["amount"], data["rate"], data["min_payment"], deadline)


async def _finish_adddebt(message: Message, name: str, amount: int, rate: float, min_payment: int, deadline_day: int | None):
    """Общая логика добавления долга."""
    await add_debt(name, amount, rate, min_payment, deadline_day)
    debts = await get_debts()
    total = await get_total_debt()
    await message.answer(tpl_adddebt_confirm(name, amount, rate, min_payment, deadline_day, len(debts), total))


# ═══════════════════════════════════
# /paydebt — инлайн или интерактивный
# ═══════════════════════════════════

@router.message(Command("paydebt"), owner)
async def cmd_paydebt(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()

    if len(args) >= 3:
        # Инлайн-режим
        try:
            debt_id = int(args[1])
            amount = int(args[2])
        except ValueError:
            await message.answer(tpl_error_input("/paydebt <id> <сумма>"))
            return
        await _finish_paydebt(message, debt_id, amount)
    else:
        # Интерактивный режим
        debts, text = await _debts_numbered_list()
        if not debts:
            await message.answer(text)
            return
        debt_map = {i: d["id"] for i, d in enumerate(debts, 1)}
        await state.update_data(debt_map=debt_map)
        await state.set_state(PayDebtFSM.select)
        await message.answer(text + "\n\n" + esc("Какой долг гасишь? (номер)"))


@router.message(PayDebtFSM.select, owner)
async def paydebt_select(message: Message, state: FSMContext):
    try:
        num = int(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи номер из списка."))
        return
    data = await state.get_data()
    debt_map = data["debt_map"]
    if num not in debt_map:
        await message.answer(esc("Нет такого номера."))
        return
    await state.update_data(debt_id=debt_map[num])
    await state.set_state(PayDebtFSM.amount)
    await message.answer(esc("Сколько вносишь?"))


@router.message(PayDebtFSM.amount, owner)
async def paydebt_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи число."))
        return
    data = await state.get_data()
    await state.clear()
    await _finish_paydebt(message, data["debt_id"], amount)


async def _finish_paydebt(message: Message, debt_id: int, amount: int):
    """Общая логика платежа по долгу."""
    result = await pay_debt(debt_id, amount)
    if not result:
        await message.answer(esc("Долг не найден."))
        return

    if result["new_amount"] <= 0:
        # Долг закрыт
        debts = await get_debts()
        total = await get_total_debt()
        await message.answer(tpl_paydebt_closed(
            result["name"], result["initial_amount"], len(debts), total,
        ))
    else:
        await message.answer(tpl_paydebt_confirm(
            amount, result["name"], result["old_amount"], result["new_amount"], result["initial_amount"],
        ))


# ═══════════════════════════════════
# /removedebt — интерактивный
# ═══════════════════════════════════

@router.message(Command("removedebt"), owner)
async def cmd_removedebt(message: Message, state: FSMContext):
    await state.clear()
    debts, text = await _debts_numbered_list()
    if not debts:
        await message.answer(text)
        return
    debt_map = {i: d["id"] for i, d in enumerate(debts, 1)}
    await state.update_data(debt_map=debt_map)
    await state.set_state(RemoveDebtFSM.select)
    await message.answer(text + "\n\n" + esc("Какой долг удалить? (номер)"))


@router.message(RemoveDebtFSM.select, owner)
async def removedebt_select(message: Message, state: FSMContext):
    try:
        num = int(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи номер из списка."))
        return
    data = await state.get_data()
    debt_map = data["debt_map"]
    if num not in debt_map:
        await message.answer(esc("Нет такого номера."))
        return

    debt = await get_debt(debt_map[num])
    if not debt:
        await state.clear()
        await message.answer(esc("Долг не найден."))
        return

    await state.update_data(debt_id=debt["id"], debt_name=debt["name"], debt_amount=debt["amount"])
    await state.set_state(RemoveDebtFSM.confirm)
    await message.answer(
        f"Удалить долг {esc(debt['name'])} ({money(debt['amount'])})? Да/Нет"
    )


@router.message(RemoveDebtFSM.confirm, owner)
async def removedebt_confirm(message: Message, state: FSMContext):
    answer = message.text.strip().lower()
    data = await state.get_data()
    await state.clear()

    if answer in ("да", "yes", "д", "y"):
        removed = await remove_debt(data["debt_id"])
        if removed:
            await message.answer(tpl_removedebt_confirm(removed["name"], removed["amount"]))
        else:
            await message.answer(esc("Долг не найден."))
    else:
        await message.answer(esc("Отменено."))


# ═══════════════════════════════════
# /editdebt — интерактивный
# ═══════════════════════════════════

EDIT_FIELDS = {
    "1": ("name", "название", str),
    "2": ("amount", "сумма", int),
    "3": ("rate", "процент", float),
    "4": ("min_payment", "мин. платёж", int),
    "5": ("deadline_day", "дедлайн", lambda x: int(x) if x.lower() not in ("0", "нет", "no") else None),
}


@router.message(Command("editdebt"), owner)
async def cmd_editdebt(message: Message, state: FSMContext):
    await state.clear()
    debts, text = await _debts_numbered_list()
    if not debts:
        await message.answer(text)
        return
    debt_map = {i: d["id"] for i, d in enumerate(debts, 1)}
    await state.update_data(debt_map=debt_map)
    await state.set_state(EditDebtFSM.select)
    await message.answer(text + "\n\n" + esc("Какой долг изменить? (номер)"))


@router.message(EditDebtFSM.select, owner)
async def editdebt_select(message: Message, state: FSMContext):
    try:
        num = int(message.text.strip())
    except ValueError:
        await message.answer(esc("Введи номер из списка."))
        return
    data = await state.get_data()
    debt_map = data["debt_map"]
    if num not in debt_map:
        await message.answer(esc("Нет такого номера."))
        return

    debt = await get_debt(debt_map[num])
    if not debt:
        await state.clear()
        await message.answer(esc("Долг не найден."))
        return

    await state.update_data(debt_id=debt["id"], debt_name=debt["name"])
    await state.set_state(EditDebtFSM.field)
    await message.answer("\n".join([
        esc(f"Что изменить в «{debt['name']}»?"),
        esc("1 — название"),
        esc("2 — сумма"),
        esc("3 — процент"),
        esc("4 — мин. платёж"),
        esc("5 — дедлайн"),
    ]))


@router.message(EditDebtFSM.field, owner)
async def editdebt_field(message: Message, state: FSMContext):
    choice = message.text.strip()
    if choice not in EDIT_FIELDS:
        await message.answer(esc("Введи число от 1 до 5."))
        return
    field_key, field_label, _ = EDIT_FIELDS[choice]
    await state.update_data(field_key=field_key, field_label=field_label, field_choice=choice)
    await state.set_state(EditDebtFSM.value)
    await message.answer(esc(f"Новое значение для «{field_label}»?"))


@router.message(EditDebtFSM.value, owner)
async def editdebt_value(message: Message, state: FSMContext):
    data = await state.get_data()
    _, _, converter = EDIT_FIELDS[data["field_choice"]]
    try:
        value = converter(message.text.strip())
    except (ValueError, TypeError):
        await message.answer(esc("Неверный формат. Попробуй ещё."))
        return

    await state.clear()
    success = await update_debt_field(data["debt_id"], data["field_key"], value)
    if success:
        display_val = value if value is not None else "нет"
        await message.answer(esc(f"Обновлено: {data['debt_name']} — {data['field_label']}: {display_val}"))
    else:
        await message.answer(esc("Не удалось обновить."))


# ═══════════════════════════════════
# /income
# ═══════════════════════════════════

@router.message(Command("income"), owner)
async def cmd_income(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer(tpl_error_input("/income <сумма> [источник]"))
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.answer(esc("Сумма должна быть числом."))
        return

    source_raw = args[2].strip().lower() if len(args) > 2 else "other"
    source = SOURCE_MAP.get(source_raw, "other")
    note = args[2] if len(args) > 2 else source

    distribution = await distribute_income(amount, source, note)

    if source == "personal":
        new_bal = await get_personal_balance()
        await message.answer(tpl_income_personal(amount, source, new_bal))
    else:
        await message.answer(tpl_income_distributed(amount, source, distribution))


# ═══════════════════════════════════
# /goals
# ═══════════════════════════════════

@router.message(Command("goals"), owner)
async def cmd_goals(message: Message, state: FSMContext):
    await state.clear()
    goals = await get_goals()
    if not goals:
        await message.answer(esc("Целей пока нет. Добавь: /addgoal <название> <сумма> <дедлайн>"))
        return

    lines = []
    for g in goals:
        pct = int(g["saved"] / g["target_amount"] * 100) if g["target_amount"] > 0 else 0
        bar = progress_bar(pct)
        gname = g["name"]
        gid = g["id"]
        line = f"*{esc('#' + str(gid) + ' ' + gname)}*: {money(g['saved'])} / {money(g['target_amount'])} {bar} {esc(str(pct) + '%')}"
        if g.get("deadline"):
            line += f"\nДедлайн: {esc(g['deadline'])}"
        lines.append(line)

    await message.answer(f"*{esc('Цели')}*\n\n" + "\n\n".join(lines))


# ═══════════════════════════════════
# /addgoal
# ═══════════════════════════════════

@router.message(Command("addgoal"), owner)
async def cmd_addgoal(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        await message.answer(tpl_error_input("/addgoal <название> <сумма> [дедлайн]"))
        return
    try:
        name = args[1]
        target = int(args[2])
        deadline = args[3] if len(args) > 3 else None
    except ValueError:
        await message.answer(esc("Сумма должна быть числом."))
        return

    goal_id = await add_goal(name, target, deadline)
    await message.answer(esc(f"Цель добавлена: #{goal_id} {name} — {target:,} руб.".replace(",", " ")))


# ═══════════════════════════════════
# /setbudget
# ═══════════════════════════════════

@router.message(Command("setbudget"), owner)
async def cmd_setbudget(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    if len(args) < 2:
        await message.answer(tpl_error_input("/setbudget <сумма>"))
        return
    try:
        amount = int(args[1])
    except ValueError:
        await message.answer(esc("Сумма должна быть числом."))
        return
    await set_setting("weekly_budget", str(amount))
    await message.answer(esc(f"Недельный бюджет: {amount:,} руб.".replace(",", " ")))


# ═══════════════════════════════════
# /week
# ═══════════════════════════════════

@router.message(Command("week"), owner)
async def cmd_week(message: Message, state: FSMContext):
    await state.clear()
    today = now_msk().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    income_by_src = await get_week_income_by_source()
    expenses_by_cat = await get_week_expenses_by_category()
    total_income = sum(income_by_src.values())
    total_expenses = sum(expenses_by_cat.values())
    budget = await get_weekly_budget()
    spent = await get_spent_this_week()

    await message.answer(tpl_week(
        monday, sunday, income_by_src, total_income,
        expenses_by_cat, total_expenses, budget, spent,
    ))


# ═══════════════════════════════════
# /envelopes
# ═══════════════════════════════════

@router.message(Command("envelopes"), owner)
async def cmd_envelopes(message: Message, state: FSMContext):
    await state.clear()
    envelopes = await get_envelopes()
    await message.answer(tpl_envelopes(envelopes))


# ═══════════════════════════════════
# /split — показ / изменение / сброс правил
# ═══════════════════════════════════

@router.message(Command("split"), owner)
async def cmd_split(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()

    if len(args) == 1:
        # Показать текущие правила
        rules = await get_envelope_rules()
        await message.answer(tpl_split_show(rules))
        return

    if args[1].lower() == "reset":
        # Сброс к дефолту
        old_rules = await get_envelope_rules()
        new_rules = dict(ENVELOPE_RULES)
        await set_envelope_rules(new_rules)
        await message.answer(tpl_split_updated(old_rules, new_rules))
        return

    # Парсинг: /split долги 40 операционка 25 личные 20 подушка 15
    tokens = args[1:]
    if len(tokens) % 2 != 0:
        await message.answer(esc("Формат: /split <конверт> <процент> <конверт> <процент> ..."))
        return

    new_rules = {}
    for i in range(0, len(tokens), 2):
        env_name = tokens[i].lower()
        env_key = ENVELOPE_MAP.get(env_name)
        if not env_key:
            await message.answer(esc(f"Неизвестный конверт: {tokens[i]}. Допустимые: долги, операционка, личные, подушка"))
            return
        try:
            pct = int(tokens[i + 1])
        except ValueError:
            await message.answer(esc(f"Процент должен быть числом: {tokens[i + 1]}"))
            return
        new_rules[env_key] = pct

    total_pct = sum(new_rules.values())
    if total_pct != 100:
        await message.answer(tpl_error_split_sum(total_pct))
        return

    # Проверяем что все 4 конверта указаны
    missing = set(ENVELOPE_RULES.keys()) - set(new_rules.keys())
    if missing:
        from templates import ENV_DISPLAY
        missing_names = ", ".join(ENV_DISPLAY.get(m, m) for m in missing)
        await message.answer(esc(f"Не указаны конверты: {missing_names}. Нужно указать все 4."))
        return

    old_rules = await get_envelope_rules()
    await set_envelope_rules(new_rules)
    await message.answer(tpl_split_updated(old_rules, new_rules))


# ═══════════════════════════════════
# /undo — отменить последнюю группу трат
# ═══════════════════════════════════

@router.message(Command("undo"), owner)
async def cmd_undo(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    last_ids = data.get("last_expense_ids", [])
    if not last_ids:
        await message.answer(esc("Нечего отменять. Используй /del для удаления конкретной траты."))
        return

    deleted = []
    for tx_id in last_ids:
        tx = await delete_expense(tx_id)
        if tx:
            deleted.append(tx)

    if not deleted:
        await message.answer(esc("Траты уже были удалены."))
        return

    total = sum(t["amount"] for t in deleted)
    budget = await get_weekly_budget()
    spent = await get_spent_this_week()
    lines = [f"*{esc('Отменено:')}*", ""]
    for t in deleted:
        lines.append(f"{esc(t.get('category', 'другое'))}: {money(t['amount'])}")
    lines += [
        "",
        f"Возвращено: {money(total)}",
        f"Бюджет на неделю: {money(budget - spent)} из {money(budget)}",
    ]
    await message.answer("\n".join(lines))


# ═══════════════════════════════════
# /del — удалить конкретную трату
# ═══════════════════════════════════

@router.message(Command("del"), owner)
async def cmd_del(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()

    if len(args) >= 2:
        # /del 7 или /del #7 #5 #4 или /del 7 5 4
        ids = []
        for arg in args[1:]:
            try:
                ids.append(int(arg.lstrip("#")))
            except ValueError:
                continue
        if not ids:
            await message.answer(tpl_error_input("/del 7 или /del 7 5 4"))
            return

        deleted = []
        not_found = []
        for tx_id in ids:
            tx = await delete_expense(tx_id)
            if tx:
                deleted.append(tx)
            else:
                not_found.append(tx_id)

        if not deleted:
            await message.answer(esc("Траты не найдены."))
            return

        budget = await get_weekly_budget()
        spent = await get_spent_this_week()
        total = sum(t["amount"] for t in deleted)
        lines = [f"*{esc('Удалено:')}*", ""]
        for t in deleted:
            lines.append(f"\\#{esc(t['id'])} {esc(t.get('category', 'другое'))}: {money(t['amount'])}")
        lines += ["", f"Возвращено: {money(total)}", f"Бюджет на неделю: {money(budget - spent)} из {money(budget)}"]
        await message.answer("\n".join(lines))
        return

    # Показать последние 10 трат для выбора
    recent = await get_recent_expenses(10)
    if not recent:
        await message.answer(esc("Нет записанных трат."))
        return

    lines = [f"*{esc('Последние траты')}*", ""]
    for t in recent:
        tid = t["id"]
        cat = t.get("category", "другое")
        note = t.get("note", "")
        date_str = t.get("date", "")
        line = f"\\#{esc(tid)} {esc(date_str)} {esc(cat)}: {money(t['amount'])}"
        if note:
            line += f" — {esc(note)}"
        lines.append(line)
    lines += ["", esc("Удалить: /del <id>")]
    await message.answer("\n".join(lines))


# ═══════════════════════════════════
# /ask — свободный вопрос к LLM
# ═══════════════════════════════════

@router.message(Command("ask"), owner)
async def cmd_ask(message: Message, state: FSMContext):
    await state.clear()
    question = message.text.partition(" ")[2].strip()
    if not question:
        await message.answer(esc("Формат: /ask <вопрос>\nПример: /ask на что я больше всего трачу?"))
        return

    try:
        context = await get_full_financial_context()
        answer = await ask_question(question, context)
        await message.answer(esc(answer))
    except Exception as e:
        logger.error(f"cmd_ask error: {e}", exc_info=True)
        await message.answer(f"Ошибка: {e}", parse_mode=None)


# ═══════════════════════════════════
# Свободный текст → парсинг трат (только вне FSM)
# ═══════════════════════════════════

@router.message(F.text, owner)
async def handle_free_text(message: Message, state: FSMContext):
    """Любой текст без / — считаем вводом трат (если нет активного FSM)."""
    logger.info(f"handle_free_text вызван: '{message.text[:50] if message.text else ''}'")

    # Пропускаем команды — их ловят Command-хендлеры выше
    if message.text and message.text.startswith("/"):
        return

    # Если пользователь в FSM-диалоге — не перехватываем
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"Пропуск: FSM активен ({current_state})")
        return

    text = message.text.strip()
    if not text:
        return

    try:
        budget = await get_weekly_budget()
        spent = await get_spent_this_week()

        logger.info(f"Free text: '{text[:80]}', budget={budget}, spent={spent}")

        expenses = await parse_expenses(text, budget, spent)

        if not expenses:
            logger.warning(f"Grok вернул пустой список для: {text[:80]}")
            try:
                await message.answer(tpl_error_grok())
            except Exception:
                await message.answer("Не удалось распознать траты. Попробуй формат: 1000 еда, 500 транспорт", parse_mode=None)
            return

        # Записываем в БД
        total = 0
        expense_ids = []
        for exp in expenses:
            tx_id = await add_expense(exp["amount"], exp["category"], exp["note"])
            expense_ids.append(tx_id)
            total += exp["amount"]

        # Сохраняем ID для /undo
        await state.update_data(last_expense_ids=expense_ids)

        new_spent = spent + total
        remaining = budget - new_spent
        date_str = now_msk().strftime("%d.%m")

        # Предупреждение если бюджет < 20%
        warn = remaining > 0 and budget > 0 and (remaining / budget) < 0.2
        today = now_msk().date()
        sunday = today + timedelta(days=6 - today.weekday())
        days_left = max(1, (sunday - today).days)

        logger.info(f"Записано {len(expenses)} трат на {total} руб.")

        try:
            await message.answer(tpl_expense_confirm(expenses, total, remaining, budget, date_str, warn, days_left))
        except Exception as e:
            logger.error(f"MarkdownV2 error: {e}")
            lines = [f"{exp['category']}: {exp['amount']} руб." for exp in expenses]
            fallback = f"Записал:\n" + "\n".join(lines) + f"\n\nИтого: {total} руб.\nОстаток на неделю: {remaining} из {budget} руб."
            await message.answer(fallback, parse_mode=None)

    except Exception as e:
        logger.error(f"handle_free_text error: {e}", exc_info=True)
        try:
            await message.answer(f"Ошибка обработки: {e}", parse_mode=None)
        except Exception:
            pass
