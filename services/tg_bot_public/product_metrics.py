"""Первый экран админки: метрики продукта по источнику (сайт / бот / вместе).

Считается в Python поверх сырых строк, а не в SQL, намеренно:
  * один и тот же код обслуживает сайт (`web_activity_log`), бота (`activity_log`)
    и объединение с дедупом по Telegram ID — три SQL-варианта расползлись бы;
  * логика метрик (retention, LTV, окна активности) проверяется юнит-тестами
    без Postgres и один в один показывается в локальном прототипе;
  * объём данных админский: тысячи пользователей, десятки тысяч событий.

Определения (они же — подписи в интерфейсе, чтобы цифры читались однозначно):
  активный   — есть хоть одно событие за последние 30 дней и после него не было
               блокировки бота (`bot_blocked`, статус kicked/left);
  отписался  — последнее событие пользователя = блокировка бота;
  retention  — «вернулся через N+ дней»: есть событие не раньше, чем через N дней
               после первого (unbounded retention — единственная честная форма
               для проектного продукта, куда приходят «сделать ролик и уйти»);
  LTV        — выручка на платящего за всё время (реализованная, без прогноза);
  CAC        — расходы на маркетинг за период / новые платящие за период;
               расходы вводятся вручную (`marketing_spend`), иначе тире.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ACTIVE_WINDOW_DAYS = 30
ACTIVE_WINDOW_CHOICES = (7, 30, 90)
COHORT_WEEKS = 8
COHORT_OFFSETS = 5  # недели 0..4 после первого визита

BLOCK_EVENT = "bot_blocked"

# События, которые пишет сам бот/админка/биллинг, а не человек. Они НЕ считаются
# активностью: иначе одна рассылка «оживляет» всю базу (на проде так и вышло —
# 1718 «активных» из 1722). Всё, чего здесь нет, считается действием пользователя,
# чтобы новые пользовательские события не выпадали молча.
SYSTEM_EVENTS = frozenset({
    BLOCK_EVENT, "reminder_sent", "sales_pitch", "keep_in_touch", "referral_sent",
    "initial_grant", "credits_reserved", "track_credits_granted",
    "subscription_charged", "subscription_charge_failed",
    "processing_timeout_recovered", "referral_timeout_recovered", "audio_recovered",
    "generation_done", "generation_failed", "generation_completed",
})
SYSTEM_EVENT_PREFIXES = ("admin_", "lifecycle_", "broadcast_", "tier_")

RETENTION_DAYS = (1, 3, 7, 14, 30)


def is_user_event(name: str) -> bool:
    return name not in SYSTEM_EVENTS and not name.startswith(SYSTEM_EVENT_PREFIXES)

# Событие → шаг воронки. Ключи воронки одинаковы для всех источников, чтобы
# «Вместе» складывалось из тех же ступеней.
FUNNEL_STEPS: Tuple[Tuple[str, str], ...] = (
    ("entered", "Вошли"),
    ("activated", "Активировались"),
    ("uploaded", "Загрузили трек"),
    ("started", "Запустили генерацию"),
    ("received", "Получили ролики"),
    ("paid", "Оплатили"),
)

_SITE_STEP_EVENTS: Dict[str, Tuple[str, ...]] = {
    "entered": ("app_entry", "signup_started", "signup_completed"),
    "activated": ("signup_completed",),
    "uploaded": ("track_uploaded",),
    "started": ("generation_started",),
    "received": ("generation_completed",),
    "paid": ("plan_purchased",),
}
_BOT_STEP_EVENTS: Dict[str, Tuple[str, ...]] = {
    "entered": ("start",),
    "activated": ("subscription_ok",),
    "uploaded": ("audio_uploaded",),
    "started": ("generation_started",),
    "received": ("generation_done",),
    "paid": ("payment_confirmed", "subscription_charged", "admin_activate"),
}

GENERATION_DONE_EVENTS = ("generation_done", "generation_completed")
GENERATION_FAILED_EVENTS = ("generation_failed",)
GENERATION_STARTED_EVENTS = ("generation_started",)


@dataclass
class Event:
    identity: str      # 'tg:<id>' или 'web:<user_id>'
    event: str
    at: datetime       # naive UTC
    channel: str       # 'bot' | 'site'


@dataclass
class Payment:
    identity: str      # 'tg:<id>'
    amount_rub: int
    at: datetime


@dataclass
class MetricsSource:
    events: List[Event]
    payments: List[Payment]
    # tg-пользователи из таблицы users (канонический список для бота).
    users: List[Tuple[str, datetime]] = field(default_factory=list)
    spend_rub: int = 0                 # расходы на маркетинг за выбранный период
    subscriptions: Dict[str, Any] = field(default_factory=dict)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _pct(num: float, den: float) -> Optional[float]:
    return (num / den * 100.0) if den else None


def _median_hours(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def compute(
    source: MetricsSource,
    *,
    channel: str,
    date_from: datetime,
    date_to: datetime,
    now: datetime,
    active_days: int = ACTIVE_WINDOW_DAYS,
) -> Dict[str, Any]:
    df, dt, now = _naive(date_from), _naive(date_to), _naive(now)
    step_events = _BOT_STEP_EVENTS if channel == "bot" else _SITE_STEP_EVENTS
    if channel == "all":
        step_events = {k: tuple(set(_BOT_STEP_EVENTS[k]) | set(_SITE_STEP_EVENTS[k])) for k in _BOT_STEP_EVENTS}

    by_identity: Dict[str, List[Event]] = defaultdict(list)
    for ev in source.events:
        by_identity[ev.identity].append(ev)
    for rows in by_identity.values():
        rows.sort(key=lambda e: e.at)

    # first_seen: таблица users точнее для бота (юзер мог появиться до activity_log).
    first_seen: Dict[str, datetime] = {}
    for identity, created in source.users:
        first_seen[identity] = _naive(created)
    for identity, rows in by_identity.items():
        first = rows[0].at
        if identity not in first_seen or first < first_seen[identity]:
            first_seen[identity] = first

    # ── Пользователи ────────────────────────────────────────────────────
    active_days = int(active_days) if int(active_days) in ACTIVE_WINDOW_CHOICES else ACTIVE_WINDOW_DAYS
    active_cut = now - timedelta(days=active_days)
    active: set[str] = set()
    blocked: set[str] = set()
    for identity, rows in by_identity.items():
        last_user = next((e for e in reversed(rows) if is_user_event(e.event)), None)
        last_block = next((e for e in reversed(rows) if e.event == BLOCK_EVENT), None)
        is_blocked = last_block is not None and (last_user is None or last_block.at >= last_user.at)
        if is_blocked:
            blocked.add(identity)
        elif last_user is not None and last_user.at >= active_cut:
            active.add(identity)

    registered_total = len(first_seen)
    live = {i for i in first_seen if i not in blocked}   # «все» = кто не заблокировал бота
    total_users = len(live)
    new_users = sum(1 for at in first_seen.values() if df <= at < dt)
    new_users_prev = sum(1 for at in first_seen.values() if df - (dt - df) <= at < df)

    # ── Продукт за период ───────────────────────────────────────────────
    period_events = [e for e in source.events if df <= e.at < dt]
    gen_started = sum(1 for e in period_events if e.event in GENERATION_STARTED_EVENTS)
    gen_done = sum(1 for e in period_events if e.event in GENERATION_DONE_EVENTS)
    gen_failed = sum(1 for e in period_events if e.event in GENERATION_FAILED_EVENTS)
    done_per_user: Dict[str, int] = defaultdict(int)
    for e in period_events:
        if e.event in GENERATION_DONE_EVENTS:
            done_per_user[e.identity] += 1
    creators = len(done_per_user)
    repeat_creators = sum(1 for n in done_per_user.values() if n >= 2)
    period_active = {e.identity for e in period_events if is_user_event(e.event)}

    # Время до первого ролика: от первого визита до первого generation_done, часы.
    ttv_hours: List[float] = []
    for identity, rows in by_identity.items():
        first_done = next((e for e in rows if e.event in GENERATION_DONE_EVENTS), None)
        if first_done is not None and df <= first_done.at < dt:
            ttv_hours.append(max(0.0, (first_done.at - first_seen[identity]).total_seconds() / 3600.0))

    # ── Деньги ──────────────────────────────────────────────────────────
    payments = sorted(source.payments, key=lambda p: p.at)
    first_payment_at: Dict[str, datetime] = {}
    revenue_by_payer: Dict[str, int] = defaultdict(int)
    for p in payments:
        first_payment_at.setdefault(p.identity, _naive(p.at))
        revenue_by_payer[p.identity] += int(p.amount_rub)
    period_payments = [p for p in payments if df <= _naive(p.at) < dt]
    revenue_period = sum(int(p.amount_rub) for p in period_payments)
    payers_period = {p.identity for p in period_payments}
    new_payers_period = {i for i, at in first_payment_at.items() if df <= at < dt}
    prev_df = df - (dt - df)
    revenue_prev = sum(int(p.amount_rub) for p in payments if prev_df <= _naive(p.at) < df)
    payers_all = len(revenue_by_payer)
    payers_live = sum(1 for i in revenue_by_payer if i in live)
    revenue_all = sum(revenue_by_payer.values())
    ltv = (revenue_all / payers_all) if payers_all else None
    arppu_period = (revenue_period / len(payers_period)) if payers_period else None
    cac = (source.spend_rub / len(new_payers_period)) if (source.spend_rub and new_payers_period) else None
    cost_per_user = (source.spend_rub / new_users) if (source.spend_rub and new_users) else None
    # Медианное время до первой оплаты среди тех, кто впервые заплатил в периоде.
    time_to_pay_days: List[float] = []
    for identity in new_payers_period:
        if identity in first_seen:
            time_to_pay_days.append(max(0.0, (first_payment_at[identity] - first_seen[identity]).total_seconds() / 86400.0))

    # ── Воронка (% от первого шага) ─────────────────────────────────────
    funnel: List[Dict[str, Any]] = []
    step_identities: Dict[str, set[str]] = {}
    for key, _label in FUNNEL_STEPS:
        names = set(step_events[key])
        step_identities[key] = {e.identity for e in period_events if e.event in names}
    # Оплата — из таблицы payments (источник правды), а не из событий: у бота и
    # сайта события покупки называются по-разному и не всегда пишутся.
    step_identities["paid"] |= payers_period
    base = len(step_identities["entered"])
    for key, label in FUNNEL_STEPS:
        count = len(step_identities[key])
        funnel.append({"key": key, "label": label, "count": count, "pct_of_first": _pct(count, base)})

    # ── Retention ───────────────────────────────────────────────────────
    retention = _retention(by_identity, first_seen, now, paid=set(revenue_by_payer))

    return {
        "channel": channel,
        "users": {
            "total": total_users,
            "registered_total": registered_total,
            "active_days": active_days,
            "active_30d": len(active),
            "active_pct": _pct(len(active), total_users),
            "blocked": len(blocked),
            "blocked_pct": _pct(len(blocked), total_users),
            "new_period": new_users,
            "new_prev": new_users_prev,
            "active_period": len(period_active),
        },
        "product": {
            "generation_started": gen_started,
            "generation_done": gen_done,
            "generation_failed": gen_failed,
            "success_pct": _pct(gen_done, gen_done + gen_failed),
            "creators": creators,
            "creators_pct_of_active": _pct(creators, len(period_active)),
            "videos_per_creator": (gen_done / creators) if creators else None,
            "repeat_creators": repeat_creators,
            "repeat_pct": _pct(repeat_creators, creators),
            "ttv_median_hours": _median_hours(ttv_hours),
        },
        "money": {
            "revenue_period": revenue_period,
            "revenue_prev": revenue_prev,
            "payers_period": len(payers_period),
            "new_payers_period": len(new_payers_period),
            "paid_conversion_pct": _pct(payers_live, total_users),
            "payers_live": payers_live,
            "arppu_period": arppu_period,
            "ltv": ltv,
            "payers_all": payers_all,
            "revenue_all": revenue_all,
            "spend_rub": int(source.spend_rub or 0),
            "cac": cac,
            "cost_per_user": cost_per_user,
            "ltv_to_cac": (ltv / cac) if (ltv and cac) else None,
            "time_to_pay_median_days": _median_hours(time_to_pay_days),
            "subscriptions": dict(source.subscriptions or {}),
        },
        "funnel": funnel,
        "retention": retention,
    }


def _retention(
    by_identity: Dict[str, List[Event]],
    first_seen: Dict[str, datetime],
    now: datetime,
    *,
    paid: set,
) -> Dict[str, Any]:
    """Day-N retention по сегментам + недельные когорты по первому визиту.

    D_N = доля пользователей (из «созревших», кому уже N+ дней), у которых было
    хоть одно действие ровно на N-й день после первого визита (bounded).
    Считаются только действия человека (is_user_event); рассылки и авто-события бота
    возвратом не являются.
    """
    def _user_days(identity: str) -> set:
        first = first_seen[identity]
        return {
            int((e.at - first).total_seconds() // 86400)
            for e in by_identity.get(identity, [])
            if is_user_event(e.event) and e.at >= first
        }

    days_cache: Dict[str, set] = {}
    segments: List[Dict[str, Any]] = []
    for key, label, members in (
        ("all", "Все", list(first_seen)),
        ("paid", "Платные", [i for i in first_seen if i in paid]),
        ("free", "Бесплатные", [i for i in first_seen if i not in paid]),
    ):
        cells: List[Dict[str, Any]] = []
        for n in RETENTION_DAYS:
            matured = [i for i in members if first_seen[i] <= now - timedelta(days=n)]
            returned = 0
            for i in matured:
                if i not in days_cache:
                    days_cache[i] = _user_days(i)
                if n in days_cache[i]:
                    returned += 1
            cells.append({"day": n, "matured": len(matured), "returned": returned, "pct": _pct(returned, len(matured))})
        segments.append({"key": key, "label": label, "size": len(members), "cells": cells})

    # Медиана дней до второго визита (среди вернувшихся хотя бы раз в другой день).
    gaps: List[float] = []
    for identity, rows in by_identity.items():
        first = first_seen[identity]
        later = next((e for e in rows if is_user_event(e.event) and (e.at - first) >= timedelta(days=1)), None)
        if later is not None:
            gaps.append((later.at - first).total_seconds() / 86400.0)

    # Недельные когорты: строка = неделя первого визита, столбцы = неделя 0..4.
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    cohorts: List[Dict[str, Any]] = []
    for back in range(COHORT_WEEKS - 1, -1, -1):
        c_start = week_start - timedelta(weeks=back)
        c_end = c_start + timedelta(weeks=1)
        members = [i for i, at in first_seen.items() if c_start <= at < c_end]
        cells: List[Optional[float]] = []
        for k in range(COHORT_OFFSETS):
            w_start = c_start + timedelta(weeks=k)
            if w_start >= now:
                cells.append(None)  # неделя ещё не наступила
                continue
            w_end = w_start + timedelta(weeks=1)
            if k == 0:
                cells.append(100.0 if members else None)
                continue
            came = sum(
                1 for i in members
                if any(w_start <= e.at < w_end and is_user_event(e.event) for e in by_identity.get(i, []))
            )
            cells.append(_pct(came, len(members)))
        cohorts.append({"week": c_start.strftime("%d.%m"), "size": len(members), "cells": cells})

    return {
        "segments": segments,
        "days": list(RETENTION_DAYS),
        "median_days_to_return": statistics.median(gaps) if gaps else None,
        "cohorts": cohorts,
        "cohort_offsets": COHORT_OFFSETS,
    }
