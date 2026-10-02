"""Напоминания бота про сайт (docs/BOT_TO_WEB_FLOW.md, B3a и раздел 7).

Чистое планирование без I/O: по фактам о человеке решаем, какое напоминание
сейчас пора отправить. Отправку и выборку фактов делает цикл в app.py.

Поводы:
- `unopened`  — нажал «на сайте», но ссылку не открыл: 30 мин → 3 ч → ~20 ч;
- `no_gen`    — открыл сайт, но не запустил генерацию: 3 ч → 1 д → 3 д;
- `recharge`  — лимиты безлимитного трека обновились (один раз на перезарядку);
- `idle`      — давно не генерировал (только бесплатные): 3 → 7 → 14 д.

Общие правила: не больше одного напоминания в REMINDER_GAP, только днём
по Москве (QUIET_*), каждый шаг — один раз (ref в reminder_log).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

MSK = timezone(timedelta(hours=3))
DAY_START_HOUR = 10
DAY_END_HOUR = 22
REMINDER_GAP = timedelta(hours=20)

UNOPENED_STEPS = (timedelta(minutes=30), timedelta(hours=3), timedelta(hours=20))
NO_GEN_STEPS = (timedelta(hours=3), timedelta(days=1), timedelta(days=3))
IDLE_STEPS = (timedelta(days=3), timedelta(days=7), timedelta(days=14))


@dataclass(frozen=True)
class Due:
    kind: str
    ref: str
    step: int


def is_daytime(now: datetime) -> bool:
    hour = now.astimezone(MSK).hour
    return DAY_START_HOUR <= hour < DAY_END_HOUR


def due_step(age: timedelta, steps: Sequence[timedelta]) -> Optional[int]:
    """Номер самого позднего наступившего шага (пропущенные ранние не догоняем)."""
    reached = [i for i, s in enumerate(steps) if age >= s]
    return reached[-1] if reached else None


def can_send(now: datetime, last_sent: Optional[datetime]) -> bool:
    if not is_daytime(now):
        return False
    return last_sent is None or now - last_sent >= REMINDER_GAP
