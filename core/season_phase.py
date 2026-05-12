"""Season phase store backed by Redis.

The phase is owned by the admin panel (in tg_bot_public) and read by the bot
during message rendering. Redis is shared across both services, which makes
it the cross-process source of truth for the season cycle.

Keys (prefix configurable via SEASON_REDIS_PREFIX, default `blast:season`):
  <prefix>:phase           — one of SeasonPhase values
  <prefix>:phase_started_at  — unix seconds when current phase began
  <prefix>:next_window_at    — unix seconds when WINDOW_OPEN begins
  <prefix>:season_theme      — human label, e.g. "Hooks"
  <prefix>:season_number     — int season counter
  <prefix>:week              — int 1..6 within current season
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from redis.asyncio import Redis

log = logging.getLogger("core.season_phase")


class SeasonPhase(str, Enum):
    DEV_EARLY = "DEV_EARLY"
    DEV_LATE = "DEV_LATE"
    PRE_LAUNCH = "PRE_LAUNCH"
    WINDOW_OPEN = "WINDOW_OPEN"
    WINDOW_CLOSING = "WINDOW_CLOSING"

    @classmethod
    def parse(cls, raw: str) -> "SeasonPhase":
        try:
            return cls(str(raw or "").strip().upper())
        except ValueError:
            return cls.DEV_EARLY


@dataclass(frozen=True)
class PhaseSnapshot:
    phase: SeasonPhase
    phase_started_at: float
    next_window_at: float
    season_theme: str
    season_number: int
    week: int

    @property
    def days_until_window(self) -> int:
        if self.next_window_at <= 0:
            return 0
        delta_s = self.next_window_at - time.time()
        if delta_s <= 0:
            return 0
        return max(1, int(delta_s // 86400))

    @property
    def hours_until_window(self) -> int:
        if self.next_window_at <= 0:
            return 0
        delta_s = self.next_window_at - time.time()
        if delta_s <= 0:
            return 0
        return max(1, int(delta_s // 3600))

    @property
    def phase_label(self) -> str:
        return {
            SeasonPhase.DEV_EARLY: "Ранняя разработка",
            SeasonPhase.DEV_LATE: "Финал разработки",
            SeasonPhase.PRE_LAUNCH: "Подготовка к окну",
            SeasonPhase.WINDOW_OPEN: "Окно открыто",
            SeasonPhase.WINDOW_CLOSING: "Окно закрывается",
        }[self.phase]


class PhaseStore:
    """Read/write the active season phase from Redis."""

    def __init__(self, redis: Redis, prefix: str = "blast:season") -> None:
        self._redis = redis
        self._prefix = prefix.rstrip(":")

    def _k(self, name: str) -> str:
        return f"{self._prefix}:{name}"

    async def snapshot(self) -> PhaseSnapshot:
        """Return the current phase snapshot.

        Missing keys fall back to sane defaults (DEV_EARLY, season 1 Hooks)
        so the bot can render menus even before the admin first configures
        the phase.
        """
        keys = [
            self._k("phase"),
            self._k("phase_started_at"),
            self._k("next_window_at"),
            self._k("season_theme"),
            self._k("season_number"),
            self._k("week"),
        ]
        values = await self._redis.mget(*keys)
        phase_raw, started, window, theme, season_no, week = values

        return PhaseSnapshot(
            phase=SeasonPhase.parse(phase_raw or SeasonPhase.DEV_EARLY.value),
            phase_started_at=_to_float(started),
            next_window_at=_to_float(window),
            season_theme=(theme or "Hooks").strip(),
            season_number=_to_int(season_no, default=1),
            week=_to_int(week, default=1),
        )

    async def set_phase(self, phase: SeasonPhase) -> None:
        pipe = self._redis.pipeline()
        pipe.set(self._k("phase"), phase.value)
        pipe.set(self._k("phase_started_at"), str(time.time()))
        await pipe.execute()
        log.info("season_phase_set phase=%s", phase.value)

    async def set_meta(
        self,
        *,
        next_window_at: Optional[float] = None,
        season_theme: Optional[str] = None,
        season_number: Optional[int] = None,
        week: Optional[int] = None,
    ) -> None:
        pipe = self._redis.pipeline()
        if next_window_at is not None:
            pipe.set(self._k("next_window_at"), str(float(next_window_at)))
        if season_theme is not None:
            pipe.set(self._k("season_theme"), season_theme)
        if season_number is not None:
            pipe.set(self._k("season_number"), str(int(season_number)))
        if week is not None:
            pipe.set(self._k("week"), str(int(week)))
        await pipe.execute()


def _to_float(raw) -> float:
    try:
        return float(raw) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_int(raw, *, default: int = 0) -> int:
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
