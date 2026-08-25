from __future__ import annotations

import time
from typing import Any, Callable, Literal


WindowsDispatchPriority = Literal["live", "bulk"]

WINDOWS_DISPATCH_PRIORITY_LIVE: WindowsDispatchPriority = "live"
WINDOWS_DISPATCH_PRIORITY_BULK: WindowsDispatchPriority = "bulk"
_PRIORITIES: tuple[WindowsDispatchPriority, ...] = (
    WINDOWS_DISPATCH_PRIORITY_LIVE,
    WINDOWS_DISPATCH_PRIORITY_BULK,
)


def normalize_windows_dispatch_priority(
    raw: Any,
    *,
    default: WindowsDispatchPriority = WINDOWS_DISPATCH_PRIORITY_LIVE,
) -> WindowsDispatchPriority:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    if value not in _PRIORITIES:
        raise ValueError(
            f"unsupported windows dispatch priority: {value!r}; "
            f"expected one of {list(_PRIORITIES)!r}"
        )
    return value  # type: ignore[return-value]


class WindowsDispatchPriorityGate:
    """Strict FIFO admission with live jobs ahead of bulk backfills."""

    def __init__(self, *, redis_client: Any, key_prefix: str):
        self._r = redis_client
        self._prefix = str(key_prefix or "blast").strip()

    def _key(self, priority: WindowsDispatchPriority) -> str:
        return f"{self._prefix}:windows:dispatch_wait:{priority}:v1"

    @staticmethod
    def _job_id(raw: Any) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="strict")
        return str(raw or "").strip()

    def register(
        self,
        job_id: str,
        *,
        priority: WindowsDispatchPriority,
        enqueued_at: float | None = None,
    ) -> None:
        jid = str(job_id or "").strip()
        if not jid:
            raise ValueError("windows dispatch priority job_id is empty")
        selected = normalize_windows_dispatch_priority(priority)
        score = float(enqueued_at or time.time())
        if score <= 0:
            score = time.time()

        # A priority update must move the job, never leave it in both sets.
        other = (
            WINDOWS_DISPATCH_PRIORITY_BULK
            if selected == WINDOWS_DISPATCH_PRIORITY_LIVE
            else WINDOWS_DISPATCH_PRIORITY_LIVE
        )
        self._r.zrem(self._key(other), jid)
        # NX preserves the original FIFO position across Celery retries.
        self._r.zadd(self._key(selected), {jid: score}, nx=True)

    def remove(self, job_id: str) -> None:
        jid = str(job_id or "").strip()
        if not jid:
            return
        for priority in _PRIORITIES:
            self._r.zrem(self._key(priority), jid)

    def counts(self) -> dict[str, int]:
        return {
            priority: int(self._r.zcard(self._key(priority)) or 0)
            for priority in _PRIORITIES
        }

    def _first_active(
        self,
        priority: WindowsDispatchPriority,
        *,
        is_active: Callable[[str], bool],
    ) -> str:
        key = self._key(priority)
        while True:
            raw_rows = list(self._r.zrange(key, 0, 99) or [])
            if not raw_rows:
                return ""

            stale: list[str] = []
            for raw in raw_rows:
                jid = self._job_id(raw)
                if jid and is_active(jid):
                    if stale:
                        self._r.zrem(key, *stale)
                    return jid
                if jid:
                    stale.append(jid)

            if stale:
                self._r.zrem(key, *stale)
            if len(raw_rows) < 100:
                return ""

    def current_turn(self, *, is_active: Callable[[str], bool]) -> tuple[str, str]:
        for priority in _PRIORITIES:
            jid = self._first_active(priority, is_active=is_active)
            if jid:
                return jid, priority
        return "", ""

    def is_turn(
        self,
        job_id: str,
        *,
        is_active: Callable[[str], bool],
    ) -> tuple[bool, str, str]:
        turn_job_id, turn_priority = self.current_turn(is_active=is_active)
        return turn_job_id == str(job_id or "").strip(), turn_job_id, turn_priority
