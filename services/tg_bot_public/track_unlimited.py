"""Правила бесплатного «безлимита на трек» (docs/BOT_TO_WEB_FLOW.md, раздел 5).

Чистая логика без I/O: на вход — что уже нагенерировано по треку, на выход —
можно ли запустить батч, сколько роликов и когда станет можно. Её зовут и сайт
(перед генерацией и для экрана перезарядки), и бот (напоминание «лимиты
обновились»), поэтому правила живут в одном месте.

Правила:
- безлимит открывается на ОДИН трек и навсегда остаётся на нём;
- батч — не больше FREE_BATCH_CAP роликов;
- первые сутки: FIRST_DAY_BATCHES батча вместе со стартовым (первые 5 роликов за
  кредиты — первый из двух), между батчами по треку перезарядка COOLDOWN;
- дальше: не больше DAILY_VIDEOS роликов за скользящие сутки;
- трипваер (399 ₽) снимает всё: до TRIPWIRE_BATCH_CAP за раз, без перезарядки.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

FREE_BATCH_CAP = 5
TRIPWIRE_BATCH_CAP = 25
COOLDOWN = timedelta(hours=4)
FIRST_DAY = timedelta(hours=24)
FIRST_DAY_BATCHES = 2
DAILY_VIDEOS = 5
DAILY_WINDOW = timedelta(hours=24)

TRIPWIRE_PRICE_RUB = 399
# Предложение трипваера живёт сутки с первого упора в перезарядку; бот его догоняет.
TRIPWIRE_OFFER_WINDOW = timedelta(hours=24)
# Код пакета в payments/normalize_package_code — по нему confirm_payment_once
# узнаёт трипваер и не начисляет за него кредиты.
TRIPWIRE_PACKAGE = "track399"

REASON_OK = "ok"
REASON_LOCKED = "locked"
REASON_COOLDOWN = "cooldown"
REASON_DAILY_LIMIT = "daily_limit"


@dataclass(frozen=True)
class TrackBatch:
    created_at: datetime
    videos: int
    # "free" — по безлимиту, "credits" — за кредиты (до открытия или у платящих).
    mode: str


@dataclass(frozen=True)
class TrackQuota:
    allowed: bool
    reason: str
    max_videos: int
    batch_cap: int
    available_at: Optional[datetime]
    tripwire: bool


def _rolling_available(items: list[TrackBatch], at: datetime) -> tuple[int, Optional[datetime]]:
    """Скользящие сутки на момент `at`: сколько бесплатных роликов свободно и, если ноль,
    когда освободится хотя бы один (выпадет самый старый батч окна)."""
    window = [b for b in items if b.mode == "free" and b.created_at > at - DAILY_WINDOW]
    used = sum(b.videos for b in window)
    if used < DAILY_VIDEOS:
        return DAILY_VIDEOS - used, None
    freed = 0
    for batch in window:
        freed += batch.videos
        if used - freed < DAILY_VIDEOS:
            return 0, batch.created_at + DAILY_WINDOW
    raise AssertionError("unreachable: window exhausts the quota but frees nothing")


def evaluate(
    *,
    now: datetime,
    unlocked_at: Optional[datetime],
    tripwire: bool,
    batches: Iterable[TrackBatch],
) -> TrackQuota:
    """Квота трека на момент `now`. Все datetime — в одной таймзоне (UTC).

    Первые сутки начинаются со стартового батча (первые бесплатные ролики за кредиты),
    если он был в последние сутки до открытия безлимита: он и есть первый из двух."""
    if unlocked_at is None:
        return TrackQuota(False, REASON_LOCKED, 0, FREE_BATCH_CAP, None, False)
    if tripwire:
        return TrackQuota(True, REASON_OK, TRIPWIRE_BATCH_CAP, TRIPWIRE_BATCH_CAP, None, True)

    items = sorted((b for b in batches if b.videos > 0), key=lambda b: b.created_at)
    opening = [b for b in items if unlocked_at - FIRST_DAY < b.created_at <= unlocked_at]
    day_start = opening[0].created_at if opening else unlocked_at
    first_day_end = day_start + FIRST_DAY
    if now < first_day_end:
        if items:
            ready_at = items[-1].created_at + COOLDOWN
            if now < ready_at:
                return TrackQuota(False, REASON_COOLDOWN, 0, FREE_BATCH_CAP, ready_at, False)
        day_batches = [b for b in items if b.created_at >= day_start]
        if len(day_batches) >= FIRST_DAY_BATCHES:
            # После первых суток действует скользящее окно: если оно ещё держит батчи
            # первого дня, «можно» наступит не ровно в конце суток, а когда освободится ролик.
            left, freed_at = _rolling_available(items, first_day_end)
            return TrackQuota(False, REASON_DAILY_LIMIT, 0, FREE_BATCH_CAP, freed_at if left == 0 else first_day_end, False)
        return TrackQuota(True, REASON_OK, FREE_BATCH_CAP, FREE_BATCH_CAP, None, False)

    left, freed_at = _rolling_available(items, now)
    if left > 0:
        return TrackQuota(True, REASON_OK, min(FREE_BATCH_CAP, left), FREE_BATCH_CAP, None, False)
    return TrackQuota(False, REASON_DAILY_LIMIT, 0, FREE_BATCH_CAP, freed_at, False)
