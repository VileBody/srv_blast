"""Сквозная аналитика: события продукта, воронка, удержание.

Модель простая и намеренно «плоская»: одно событие = одна строка (кто, что, когда, контекст).
Из этого потока считаются и воронка, и удержание, и деньги — без отдельных счётчиков,
которые пришлось бы синхронизировать руками.

Сейчас события живут в памяти (как и остальной мок). Переезд на БД — это замена
`EVENTS`-списка на таблицу: контракт `track()` и агрегатов при этом не меняется.
"""
from __future__ import annotations

import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

# Ключевые шаги продукта. Порядок = порядок воронки: от первого входа до опубликованного ролика.
FUNNEL: list[str] = [
    "signup_started",      # нажал «Зарегистрироваться»/«Войти» → ушёл в бота
    "signup_completed",    # подтвердил в Telegram, сессия установлена
    "project_created",     # завёл проект
    "track_uploaded",      # загрузил трек
    "generation_started",  # отправил батч на генерацию
    "generation_completed",  # батч отрендерился
    "video_posted",        # ролик ушёл в TikTok
]

# События, которые не входят в воронку, но нужны для метрик
EXTRA_EVENTS = {
    "generation_failed",
    "tiktok_connected",
    "plan_purchased",
    "payment_failed",
    "subscription_canceled",
    "limit_hit",
}

MAX_EVENTS = 50_000  # верхняя граница буфера в памяти, чтобы мок не съел RAM

EVENTS: list[dict[str, Any]] = []
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def track(name: str, user_id: str, props: dict[str, Any] | None = None) -> dict[str, Any]:
    """Записать событие. Неизвестные имена не отбрасываем — их видно в «прочих»."""
    event = {
        "id": f"ev_{uuid4().hex[:10]}",
        "name": name,
        "userId": user_id,
        "ts": _now().isoformat(),
        "props": props or {},
    }
    with _lock:
        EVENTS.append(event)
        if len(EVENTS) > MAX_EVENTS:
            del EVENTS[: len(EVENTS) - MAX_EVENTS]
    # Событие уходит в БД сразу: поток аналитики — лог, терять его при падении нельзя.
    # Импорт локальный, иначе цикл: persistence импортирует analytics.
    from . import persistence

    persistence.save_event(event)
    return event


def _within(days: int) -> list[dict[str, Any]]:
    if days <= 0:
        return list(EVENTS)
    cutoff = _now() - timedelta(days=days)
    return [e for e in EVENTS if datetime.fromisoformat(e["ts"]) >= cutoff]


def funnel(days: int = 30) -> list[dict[str, Any]]:
    """Воронка по УНИКАЛЬНЫМ юзерам: конверсия шага считается от предыдущего шага и от старта."""
    events = _within(days)
    reached: dict[str, set[str]] = {step: set() for step in FUNNEL}
    for event in events:
        if event["name"] in reached:
            reached[event["name"]].add(event["userId"])

    rows: list[dict[str, Any]] = []
    first = len(reached[FUNNEL[0]]) or 0
    previous = first
    for step in FUNNEL:
        count = len(reached[step])
        rows.append({
            "step": step,
            "users": count,
            # доля от предыдущего шага — где именно теряем
            "fromPrev": round(count / previous * 100, 1) if previous else 0.0,
            # доля от входа в воронку — сквозная конверсия
            "fromStart": round(count / first * 100, 1) if first else 0.0,
        })
        previous = count or previous
    return rows


def retention(weeks: int = 4) -> list[dict[str, Any]]:
    """Недельные когорты: сколько юзеров вернулись через N недель после первого события."""
    first_seen: dict[str, datetime] = {}
    by_user: dict[str, list[datetime]] = defaultdict(list)
    for event in EVENTS:
        ts = datetime.fromisoformat(event["ts"])
        uid = event["userId"]
        by_user[uid].append(ts)
        if uid not in first_seen or ts < first_seen[uid]:
            first_seen[uid] = ts

    cohorts: dict[str, list[str]] = defaultdict(list)
    for uid, start in first_seen.items():
        cohorts[start.strftime("%Y-W%V")].append(uid)

    rows: list[dict[str, Any]] = []
    for cohort, users in sorted(cohorts.items()):
        base = len(users)
        weekly: list[int] = []
        for week in range(weeks):
            active = 0
            for uid in users:
                start = first_seen[uid]
                lo = start + timedelta(weeks=week)
                hi = lo + timedelta(weeks=1)
                if any(lo <= ts < hi for ts in by_user[uid]):
                    active += 1
            weekly.append(active)
        rows.append({
            "cohort": cohort,
            "users": base,
            "weeks": weekly,
            "percent": [round(a / base * 100, 1) if base else 0.0 for a in weekly],
        })
    return rows


def summary(days: int = 30) -> dict[str, Any]:
    """Верхнеуровневые метрики за период."""
    events = _within(days)
    names = Counter(e["name"] for e in events)
    users = {e["userId"] for e in events}
    paying = {e["userId"] for e in events if e["name"] == "plan_purchased"}
    signups = {e["userId"] for e in events if e["name"] == "signup_completed"}
    generated = sum(int(e["props"].get("videos") or 0) for e in events if e["name"] == "generation_completed")

    started = names.get("generation_started", 0)
    failed = names.get("generation_failed", 0)
    return {
        "days": days,
        "activeUsers": len(users),
        "signups": len(signups),
        "payingUsers": len(paying),
        # доля новых юзеров, дошедших до оплаты
        "conversionToPaid": round(len(paying & signups) / len(signups) * 100, 1) if signups else 0.0,
        "videosGenerated": generated,
        "videosPosted": names.get("video_posted", 0),
        "generationFailRate": round(failed / (started + failed) * 100, 1) if (started + failed) else 0.0,
        "paymentFailures": names.get("payment_failed", 0),
        "cancellations": names.get("subscription_canceled", 0),
        "limitHits": names.get("limit_hit", 0),
        "events": len(events),
    }


def recent(limit: int = 50) -> list[dict[str, Any]]:
    return list(reversed(EVENTS[-limit:]))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def flow_metrics(days: int = 30) -> dict[str, Any]:
    """Метрики прохождения из ревью интерфейса: ожидание, «Пул» и возвраты назад.

    Три вопроса, на которые раньше ответа не было:
    - дожидаются ли генерации (и сколько сидят на экране ожидания те, кто ушёл);
    - сколько времени съедает этап «Пул» — самая тяжёлая раскладка в визарде;
    - как часто и с какого этапа люди возвращаются назад, то есть где переделывают.

    Медиана, а не среднее: одна вкладка, забытая на сутки, сдвинула бы среднее в мусор.
    """
    events = _within(days)
    waits = [e for e in events if e["name"] == "waiting_left"]
    abandoned = [e for e in waits if not e["props"].get("completed")]
    stage_times = [e for e in events if e["name"] == "wizard_stage_time"]
    pool_times = [float(e["props"].get("seconds") or 0) for e in stage_times if int(e["props"].get("stage") or 0) == 5]
    backs = [e for e in events if e["name"] == "wizard_back"]
    back_by_stage = Counter(str(e["props"].get("stage")) for e in backs)
    stage_seconds: dict[str, list[float]] = defaultdict(list)
    for event in stage_times:
        stage_seconds[str(event["props"].get("stage"))].append(float(event["props"].get("seconds") or 0))
    return {
        "days": days,
        # экран ожидания
        "waitSessions": len(waits),
        "waitAbandonRate": round(len(abandoned) / len(waits) * 100, 1) if waits else 0.0,
        "waitMedianSeconds": _median([float(e["props"].get("seconds") or 0) for e in waits]),
        "abandonMedianSeconds": _median([float(e["props"].get("seconds") or 0) for e in abandoned]),
        # этап «Пул» и остальные этапы визарда
        "poolMedianSeconds": _median(pool_times),
        "stageMedianSeconds": {stage: _median(values) for stage, values in sorted(stage_seconds.items())},
        # возвраты назад
        "backClicks": len(backs),
        "backByStage": dict(sorted(back_by_stage.items())),
    }


# --------------------------- Саммари по выкладке ---------------------------
# Задача этого блока — не «описать ролик», а понять ПУТЬ пользователя: как далеко он зашёл,
# сколько сгенерировал и сколько реально выложил, дошёл ли до тарифов и чем это кончилось.
# Отсюда видно, на каком шаге люди застревают и что коррелирует с вовлечением.

# Стадия = самый дальний достигнутый шаг воронки. Индекс в FUNNEL задаёт порядок.
STAGE_ORDER = FUNNEL


def _stage_of(names: set[str]) -> str:
    reached = [step for step in STAGE_ORDER if step in names]
    return reached[-1] if reached else "signup_started"


def user_journeys(days: int = 30) -> list[dict[str, Any]]:
    """Пер-юзер разрез: где остановился, сколько сделал, платит ли.

    `postRate` — доля выложенного от сгенерированного: главный индикатор того, что
    ролики реально доходят до площадки, а не оседают в кабинете.
    """
    events = _within(days)
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_user[event["userId"]].append(event)

    rows: list[dict[str, Any]] = []
    for user_id, items in by_user.items():
        names = {e["name"] for e in items}
        generated = sum(int(e["props"].get("videos") or 0) for e in items if e["name"] == "generation_completed")
        posted = sum(1 for e in items if e["name"] == "video_posted")
        purchases = [e for e in items if e["name"] == "plan_purchased"]
        timestamps = sorted(datetime.fromisoformat(e["ts"]) for e in items)
        rows.append({
            "userId": user_id,
            "stage": _stage_of(names),
            "stageIndex": STAGE_ORDER.index(_stage_of(names)),
            "projects": sum(1 for e in items if e["name"] == "project_created"),
            "tracks": sum(1 for e in items if e["name"] == "track_uploaded"),
            "batches": sum(1 for e in items if e["name"] == "generation_started"),
            "generated": generated,
            "posted": posted,
            # сгенерировал, но не выложил — самая дорогая потеря продукта
            "postRate": round(posted / generated * 100, 1) if generated else 0.0,
            "sawPricing": "pricing_viewed" in names,
            "hitLimit": "limit_hit" in names,
            "paid": bool(purchases),
            "tier": (purchases[-1]["props"].get("tier") if purchases else None),
            "failedGenerations": sum(1 for e in items if e["name"] == "generation_failed"),
            "firstSeen": timestamps[0].isoformat() if timestamps else None,
            "lastSeen": timestamps[-1].isoformat() if timestamps else None,
            "events": len(items),
        })
    # сверху — те, кто дальше всех и активнее: с ними понятнее, что работает
    rows.sort(key=lambda r: (r["stageIndex"], r["generated"]), reverse=True)
    return rows


def delivery_summary(days: int = 30) -> dict[str, Any]:
    """Агрегаты по выкладке и вовлечению — что происходит с контентом после генерации."""
    rows = user_journeys(days)
    if not rows:
        return {
            "days": days, "users": 0, "byStage": {}, "generated": 0, "posted": 0, "postRate": 0.0,
            "generatedNotPosted": 0, "stuckAfterGeneration": 0, "avgBatchesPerUser": 0.0,
            "sawPricing": 0, "pricingToPaid": 0.0, "hitLimit": 0, "limitToPaid": 0.0,
            "postRateByPaid": {"paid": 0.0, "free": 0.0},
        }

    generated = sum(r["generated"] for r in rows)
    posted = sum(r["posted"] for r in rows)
    saw_pricing = [r for r in rows if r["sawPricing"]]
    hit_limit = [r for r in rows if r["hitLimit"]]
    paid = [r for r in rows if r["paid"]]
    free = [r for r in rows if not r["paid"]]

    def _post_rate(bucket: list[dict[str, Any]]) -> float:
        gen = sum(r["generated"] for r in bucket)
        pos = sum(r["posted"] for r in bucket)
        return round(pos / gen * 100, 1) if gen else 0.0

    by_stage = Counter(r["stage"] for r in rows)
    return {
        "days": days,
        "users": len(rows),
        # сколько людей осело на каждом шаге — распределение, а не только переходы
        "byStage": {step: by_stage.get(step, 0) for step in STAGE_ORDER},
        "generated": generated,
        "posted": posted,
        "postRate": round(posted / generated * 100, 1) if generated else 0.0,
        "generatedNotPosted": max(0, generated - posted),
        # сгенерировал хотя бы один ролик и не выложил ни одного
        "stuckAfterGeneration": sum(1 for r in rows if r["generated"] > 0 and r["posted"] == 0),
        "avgBatchesPerUser": round(sum(r["batches"] for r in rows) / len(rows), 2),
        "sawPricing": len(saw_pricing),
        # дошёл до тарифов → купил
        "pricingToPaid": round(len([r for r in saw_pricing if r["paid"]]) / len(saw_pricing) * 100, 1) if saw_pricing else 0.0,
        "hitLimit": len(hit_limit),
        # упёрся в лимит → купил: работает ли лимит как аргумент апсейла
        "limitToPaid": round(len([r for r in hit_limit if r["paid"]]) / len(hit_limit) * 100, 1) if hit_limit else 0.0,
        # платящие обычно выкладывают активнее — если нет, продукт не удерживает
        "postRateByPaid": {"paid": _post_rate(paid), "free": _post_rate(free)},
    }
