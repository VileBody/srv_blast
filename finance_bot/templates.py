"""Шаблоны сообщений для Telegram (MarkdownV2). Без эмодзи."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

_SPECIAL = frozenset('_*[]()~`>#+-=|{}.!')

MONTH_NAMES = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}

ENV_DISPLAY = {"debts": "Долги", "operations": "Операционка", "personal": "Личные", "savings": "Подушка"}
SRC_DISPLAY = {"blast": "Blast", "freelance": "Фриланс", "personal": "Личное", "other": "Другое"}


# ── Утилиты ──

def esc(text) -> str:
    """Экранирование спецсимволов MarkdownV2."""
    return "".join(("\\" + c if c in _SPECIAL else c) for c in str(text))


def money(amount: int) -> str:
    """Форматирование суммы без знака."""
    prefix = "-" if amount < 0 else ""
    s = f"{abs(amount):,}".replace(",", " ")
    return esc(f"{prefix}{s} руб.")


def money_signed(amount: int) -> str:
    """Форматирование суммы со знаком +/-."""
    sign = "+" if amount >= 0 else "-"
    s = f"{abs(amount):,}".replace(",", " ")
    return esc(f"{sign}{s} руб.")


def progress_bar(percent: int) -> str:
    """Прогресс-бар 10 символов: ▓▓▓▓░░░░░░."""
    percent = max(0, min(100, percent))
    filled = round(percent / 10)
    return "▓" * filled + "░" * (10 - filled)


def _today() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y")


def _date_fmt(d: date) -> str:
    return d.strftime("%d.%m")


def _debt_pct(initial: int, current: int) -> int:
    """Процент закрытия долга."""
    if initial <= 0:
        return 100 if current <= 0 else 0
    return max(0, min(100, int((initial - current) / initial * 100)))


def _src(source: str) -> str:
    return SRC_DISPLAY.get(source, source)


def _env(name: str) -> str:
    return ENV_DISPLAY.get(name, name)


def _nav(*cmds: str) -> str:
    """Блок навигации — команды через разделитель."""
    return "\n\n" + esc(" | ").join(cmds)


# ═══════════════════════════════════
# 1. /start
# ═══════════════════════════════════

def tpl_start(balance: int, total_debt: int, nearest: dict | None) -> str:
    lines = [
        f"*{esc('Финансовый трекер')}*",
        "",
        f"Текущий статус на {esc(_today())}:",
        "",
        f"*{esc('Баланс:')}* {money_signed(balance)}",
        f"*{esc('Долги:')}* {money(total_debt)}",
    ]
    if nearest:
        lines.append(
            f"*{esc('Ближайший дедлайн:')}* {esc(nearest['name'])}"
            f" — {esc(nearest['deadline_day'])}\\-го ({money(nearest.get('min_payment', 0))})"
        )
    lines += [
        "",
        esc("Команды:"),
        esc("/status — общий статус"),
        esc("/balance — баланс за месяц"),
        esc("/debts — долги и прогресс"),
        esc("/week — сводка за неделю"),
        esc("/income — добавить доход"),
        esc("/split — правила распределения"),
        esc("/ask — вопрос по финансам"),
    ]
    return "\n".join(lines)


# ═══════════════════════════════════
# 2. /status
# ═══════════════════════════════════

def tpl_status(envelopes: list[dict], debts: list[dict], total_debt: int, budget: int, spent: int) -> str:
    remaining = budget - spent
    lines = [
        f"*{esc('Статус на')} {esc(_today())}*",
        "",
        f"*{esc('Конверты')}*",
    ]
    for e in envelopes:
        lines.append(f"{esc(_env(e['name']))}: {money(e['balance'])}")

    lines += ["", f"*{esc('Долги — итого')} {money(total_debt)}*"]
    for d in debts:
        pct = _debt_pct(d.get("initial_amount", d["amount"]), d["amount"])
        lines.append(f"{esc(d['name'])}: {money(d['amount'])} ({esc(f'{pct}%')})")

    lines += [
        "",
        f"*{esc('Бюджет на неделю')}*",
        f"Лимит: {money(budget)}",
        f"Потрачено: {money(spent)}",
        f"Остаток: {money(remaining)}",
    ]
    return "\n".join(lines) + _nav("/week", "/balance", "/debts")


# ═══════════════════════════════════
# 3. /balance
# ═══════════════════════════════════

def tpl_balance(
    month_name: str,
    income_by_source: dict[str, int],
    expenses_by_category: dict[str, int],
    total_income: int,
    total_expense: int,
    net_balance: int,
    debts: list[dict],
) -> str:
    lines = [
        f"*{esc('Баланс за')} {esc(month_name)}*",
        "",
        f"*{esc('Доход')} — {money(total_income)}*",
    ]
    for src, amt in sorted(income_by_source.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"{esc(_src(src))}: {money(amt)}")

    lines += ["", f"*{esc('Расход')} — {money(total_expense)}*"]
    for cat, amt in sorted(expenses_by_category.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"{esc(cat)}: {money(amt)}")

    lines += ["", f"*{esc('Чистый баланс:')} {money_signed(net_balance)}*"]

    if debts:
        lines += ["", f"*{esc('Долги — прогресс')}*"]
        for d in debts:
            initial = d.get("initial_amount", d["amount"])
            current = d["amount"]
            paid = max(0, initial - current)
            pct = _debt_pct(initial, current)
            lines += [
                f"{esc(d['name'])}",
                f"{progress_bar(pct)} {esc(f'{pct}%')}",
                f"Было: {money(initial)} — Сейчас: {money(current)} — Выплачено: {money(paid)}",
                "",
            ]

    return "\n".join(lines) + _nav("/week", "/debts", "/envelopes")


# ═══════════════════════════════════
# 4. /debts
# ═══════════════════════════════════

def tpl_debts(debts: list[dict], total_debt: int) -> str:
    if not debts:
        return esc("Долгов нет.")

    lines = [f"*{esc('Долги')}*", ""]
    for i, d in enumerate(debts, 1):
        initial = d.get("initial_amount", d["amount"])
        current = d["amount"]
        pct = _debt_pct(initial, current)
        status = "закрыт" if current <= 0 else "активен"

        dname = d["name"]
        lines.append(f"*{esc(str(i) + '. ' + dname)}* — {esc(status)}")
        lines.append(f"Остаток: {money(current)} из {money(initial)}")
        if d.get("rate", 0) > 0:
            rate = d["rate"]
            lines.append(f"Ставка: {esc(str(rate) + '% годовых')}")
        if d.get("min_payment", 0) > 0:
            mp = f"Мин. платёж: {d['min_payment']:,} руб.".replace(",", " ")
            if d.get("deadline_day"):
                mp += f" до {d['deadline_day']}-го"
            lines.append(esc(mp))
        lines.append(f"Прогресс: {progress_bar(pct)} {esc(f'{pct}%')}")
        prognosis = d.get("prognosis", "")
        if prognosis:
            lines.append(f"Прогноз: {esc(prognosis)}")
        lines.append("")

    lines.append(f"*{esc('Итого:')} {money(total_debt)}*")
    return "\n".join(lines) + _nav("/paydebt", "/adddebt", "/editdebt", "/removedebt")


# ═══════════════════════════════════
# 5. /week
# ═══════════════════════════════════

def tpl_week(
    monday: date,
    sunday: date,
    income_by_source: dict[str, int],
    total_income: int,
    expenses_by_category: dict[str, int],
    total_expenses: int,
    budget: int,
    spent: int,
) -> str:
    remaining = budget - spent
    lines = [
        f"*{esc('Неделя')} {esc(_date_fmt(monday))} — {esc(_date_fmt(sunday))}*",
        "",
        f"*{esc('Доход:')} {money(total_income)}*",
    ]
    for src, amt in sorted(income_by_source.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"{esc(_src(src))}: {money(amt)}")

    lines += ["", f"*{esc('Расходы:')} {money(total_expenses)}*"]
    for cat, amt in sorted(expenses_by_category.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"{esc(cat)}: {money(amt)}")

    lines += [
        "",
        f"*{esc('Бюджет')}*",
        f"Лимит: {money(budget)}",
        f"Потрачено: {money(spent)}",
        f"Остаток: *{money(remaining)}*",
    ]
    return "\n".join(lines) + _nav("/balance", "/debts")


# ═══════════════════════════════════
# 6. Подтверждение дохода (с распределением)
# ═══════════════════════════════════

def tpl_income_distributed(amount: int, source: str, distribution: dict[str, int]) -> str:
    lines = [
        f"*{esc('Доход:')} {money(amount)}*",
        f"Источник: {esc(_src(source))}",
        "",
        esc("Распределение:"),
    ]
    for env_name, share in distribution.items():
        lines.append(f"{esc(_env(env_name))}: {money_signed(share)}")
    return "\n".join(lines) + _nav("/balance", "/envelopes")


# ═══════════════════════════════════
# 7. Подтверждение дохода (личное, без распределения)
# ═══════════════════════════════════

def tpl_income_personal(amount: int, source: str, new_balance: int) -> str:
    return "\n".join([
        f"*{esc('Доход:')} {money(amount)}*",
        f"Источник: {esc(_src(source))}",
        "",
        f"Зачислено в конверт *{esc('личные')}* без распределения\\.",
        f"Баланс личные: {money(new_balance)}",
    ]) + _nav("/balance", "/envelopes")


# ═══════════════════════════════════
# 8. /split (показ текущих правил)
# ═══════════════════════════════════

def tpl_split_show(rules: dict[str, int]) -> str:
    lines = [f"*{esc('Правила распределения')}*", ""]
    for env_name, pct in rules.items():
        lines.append(f"{esc(_env(env_name))}: *{esc(f'{pct}%')}*")
    lines += [
        "",
        esc("Изменить: /split долги 40 операционка 25 личные 20 подушка 15"),
        esc("Сбросить: /split reset"),
    ]
    return "\n".join(lines)


# ═══════════════════════════════════
# 9. /split (подтверждение изменения)
# ═══════════════════════════════════

def tpl_split_updated(old_rules: dict[str, int], new_rules: dict[str, int]) -> str:
    lines = [f"*{esc('Правила обновлены')}*", ""]
    for env_name in new_rules:
        old_pct = old_rules.get(env_name, 0)
        new_pct = new_rules[env_name]
        lines.append(f"{esc(_env(env_name))}: {esc(f'{old_pct}%')} — *{esc(f'{new_pct}%')}*")
    return "\n".join(lines)


# ═══════════════════════════════════
# 10. Ежедневный пинг (23:00)
# ═══════════════════════════════════

def tpl_daily_ping() -> str:
    return "\n".join([
        f"*{esc('Отчёт за день')}*",
        "",
        esc("На что потратился сегодня?"),
        esc("Напиши свободным текстом — разберу."),
    ])


# ═══════════════════════════════════
# 11. Повторный пинг (23:30)
# ═══════════════════════════════════

def tpl_daily_ping_retry() -> str:
    return "\n".join([
        esc("Жду траты за сегодня."),
        esc('Если не было — напиши "ноль".'),
    ])


# ═══════════════════════════════════
# 12. Подтверждение трат (после парсинга Grok)
# ═══════════════════════════════════

def tpl_expense_confirm(
    expenses: list[dict],
    total: int,
    remaining: int,
    budget: int,
    date_str: str,
    warn_budget: bool = False,
    days_left: int = 0,
) -> str:
    lines = [f"*{esc('Траты за')} {esc(date_str)}*", ""]
    for exp in expenses:
        line = f"{esc(exp['category'])}: {money(exp['amount'])}"
        if exp.get("note"):
            line += f" — {esc(exp['note'])}"
        lines.append(line)

    lines += [
        "",
        f"*{esc('Итого:')} {money(total)}*",
        f"Бюджет на неделю: {money(remaining)} из {money(budget)}",
    ]

    # Предупреждение если бюджет < 20%
    if warn_budget and budget > 0:
        pct_left = max(0, int(remaining / budget * 100))
        per_day = remaining // days_left if days_left > 0 else 0
        lines += [
            "",
            f"*{esc('Внимание:')}* осталось {esc(f'{pct_left}%')} недельного бюджета\\.",
            f"До конца недели {esc(days_left)} дней, доступно {money(per_day)}/день\\.",
        ]

    return "\n".join(lines) + _nav("/undo", "/week", "/balance")


# ═══════════════════════════════════
# 14. Еженедельная сводка (воскресенье)
# ═══════════════════════════════════

def tpl_weekly_summary(
    monday: date,
    sunday: date,
    income_by_source: dict[str, int],
    total_income: int,
    expenses_by_category: dict[str, int],
    total_expense: int,
    week_balance: int,
    debts: list[dict],
    recommendation: str,
) -> str:
    lines = [
        f"*{esc('Сводка:')} {esc(_date_fmt(monday))} — {esc(_date_fmt(sunday))}*",
        "",
        f"*{esc('Доход:')} {money(total_income)}*",
    ]
    for src, amt in sorted(income_by_source.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"{esc(_src(src))}: {money(amt)}")

    lines += ["", f"*{esc('Расход:')} {money(total_expense)}*"]
    for cat, amt in sorted(expenses_by_category.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"{esc(cat)}: {money(amt)}")

    lines += ["", f"*{esc('Баланс за неделю:')} {money_signed(week_balance)}*"]

    if debts:
        lines += ["", f"*{esc('Долги')}*"]
        for d in debts:
            prognosis = d.get("prognosis", "")
            line = f"{esc(d['name'])}: {money(d['amount'])}"
            if prognosis and d["amount"] > 0:
                line += f" — прогноз {esc(prognosis)}"
            lines.append(line)

    if recommendation:
        lines += ["", f"*{esc('Рекомендация:')}* {esc(recommendation)}"]

    return "\n".join(lines) + _nav("/balance", "/debts")


# ═══════════════════════════════════
# 15. /paydebt — подтверждение
# ═══════════════════════════════════

def tpl_paydebt_confirm(amount: int, name: str, old_amount: int, new_amount: int, initial_amount: int) -> str:
    pct = _debt_pct(initial_amount, new_amount)
    return "\n".join([
        f"*{esc('Платёж:')} {money(amount)}*",
        f"Долг: {esc(name)}",
        "",
        f"Было: {money(old_amount)}",
        f"Стало: *{money(new_amount)}*",
        f"Прогресс: {progress_bar(pct)} {esc(f'{pct}%')}",
    ]) + _nav("/debts", "/balance")


# ═══════════════════════════════════
# 16. /paydebt — долг закрыт
# ═══════════════════════════════════

def tpl_paydebt_closed(name: str, initial_amount: int, active_count: int, active_total: int) -> str:
    return "\n".join([
        f"*{esc('Долг закрыт:')} {esc(name)}*",
        "",
        f"Выплачено полностью: {money(initial_amount)}",
        f"Активных долгов: {esc(active_count)}, на сумму {money(active_total)}",
    ]) + _nav("/debts", "/balance")


# ═══════════════════════════════════
# 17. /adddebt — подтверждение
# ═══════════════════════════════════

def tpl_adddebt_confirm(
    name: str, amount: int, rate: float, min_payment: int, deadline_day: int | None,
    total_count: int, total_amount: int,
) -> str:
    lines = [
        f"*{esc('Добавлен долг:')} {esc(name)}*",
        "",
        f"Сумма: {money(amount)}",
        f"Ставка: {esc(f'{rate}% годовых')}",
        esc(f"Мин. платёж: {min_payment:,} руб./мес.".replace(",", " ")),
    ]
    if deadline_day:
        lines.append(f"Дедлайн: {esc(deadline_day)}\\-е каждого месяца")
    else:
        lines.append(esc("Дедлайн: нет"))
    lines += [
        "",
        f"Всего долгов: {esc(total_count)}, на сумму {money(total_amount)}",
    ]
    return "\n".join(lines) + _nav("/debts", "/balance")


# ═══════════════════════════════════
# 18. /removedebt — подтверждение удаления
# ═══════════════════════════════════

def tpl_removedebt_confirm(name: str, amount: int) -> str:
    return "\n".join([
        f"*{esc('Удалён долг:')} {esc(name)}*",
        f"Остаток на момент удаления: {money(amount)}",
    ]) + _nav("/debts", "/balance")


# ═══════════════════════════════════
# 19. Нет данных за день (00:00)
# ═══════════════════════════════════

def tpl_no_data(date_str: str) -> str:
    return f"Нет данных за {esc(date_str)}\\. Записано как пропуск\\."


# ═══════════════════════════════════
# 21. /envelopes
# ═══════════════════════════════════

def tpl_envelopes(envelopes: list[dict]) -> str:
    lines = [f"*{esc('Конверты')}*", ""]
    for e in envelopes:
        lines += [
            f"*{esc(_env(e['name']))}* — {esc(str(e['percentage']) + '% от дохода')}",
            f"Баланс: {money(e['balance'])}",
            "",
        ]
    return "\n".join(lines) + _nav("/split", "/balance")


# ═══════════════════════════════════
# 22. Ошибки
# ═══════════════════════════════════

def tpl_error_input(format_hint: str) -> str:
    return f"Не удалось разобрать\\. Формат: {esc(format_hint)}"


def tpl_error_split_sum(actual: int) -> str:
    return f"Сумма процентов: {esc(f'{actual}%')}\\. Должно быть 100%\\."


def tpl_error_grok() -> str:
    return esc("Не смог разобрать траты. Попробуй ещё раз или введи вручную.")
