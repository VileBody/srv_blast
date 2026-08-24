# services/orchestrator/render_priority.py
"""
Interactive-first admission for Windows render slots.

Why this exists
---------------
Dispatch to the Windows pool is a backoff lottery: when every node is at
``max_inflight``, the task re-queues itself with an exponential countdown and
tries again later. That is fair enough when a handful of jobs compete, but it
starves live users during a large operator batch — a job coming from a
Telegram bot joins hundreds of batch jobs all waking up on the same interval,
so its expected wait grows with the size of the batch instead of with the
render time.

The gate here keeps the render pool work-conserving while giving live users
the next free slot:

  * an interactive job that finds the pool saturated registers itself in a
    Redis sorted set and retries on a short countdown;
  * a batch job refuses to reserve a slot while that set is non-empty, and
    re-queues itself instead.

Entries carry an absolute deadline as their score and are pruned on every
read, so an interactive job that dies mid-wait cannot starve the batch: its
registration expires and the batch resumes on the next attempt.

Env vars:
  RENDER_PRIORITY_ENABLED   "true"/"false"   default: true (kill switch)
  RENDER_PRIORITY_WAIT_TTL_S  int seconds    default: 300
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Sequence

CLASS_INTERACTIVE = "interactive"
CLASS_BATCH = "batch"

# Job ids created by the Telegram bots carry this idempotency-key prefix.
_INTERACTIVE_IDEMPOTENCY_PREFIXES: tuple[str, ...] = ("tg-",)

_DEFAULT_WAIT_TTL_S = 300


def priority_enabled() -> bool:
    raw = (os.environ.get("RENDER_PRIORITY_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def wait_ttl_s() -> int:
    try:
        value = int((os.environ.get("RENDER_PRIORITY_WAIT_TTL_S") or "").strip())
    except Exception:
        return _DEFAULT_WAIT_TTL_S
    return value if value > 0 else _DEFAULT_WAIT_TTL_S


def classify_job(req: Optional[Dict[str, Any]]) -> str:
    """
    Decide whether a job is live user traffic or part of an operator batch.

    An explicit ``job_priority`` on the request always wins. Without it we fall
    back to the idempotency key, so jobs already queued before this code shipped
    are classified correctly without being re-enqueued.

    Unknown shapes count as interactive: delaying a batch job costs throughput,
    delaying a user costs a user.
    """
    request = req or {}
    explicit = str(request.get("job_priority") or "").strip().lower()
    if explicit in {CLASS_INTERACTIVE, CLASS_BATCH}:
        return explicit

    idempotency_key = str(request.get("idempotency_key") or "").strip().lower()
    if not idempotency_key:
        return CLASS_INTERACTIVE
    if idempotency_key.startswith(_INTERACTIVE_IDEMPOTENCY_PREFIXES):
        return CLASS_INTERACTIVE
    return CLASS_BATCH


def waiting_key(key_prefix: str) -> str:
    prefix = str(key_prefix or "blast").strip() or "blast"
    return f"{prefix}:render:waiting_interactive:v1"


def mark_waiting(
    redis_client: Any,
    *,
    key_prefix: str,
    job_id: str,
    ttl_s: Optional[int] = None,
    now: Optional[float] = None,
) -> None:
    """Register (or refresh) an interactive job waiting for a render slot."""
    job = str(job_id or "").strip()
    if not job:
        return
    ttl = int(ttl_s if ttl_s is not None else wait_ttl_s())
    deadline = float(now if now is not None else time.time()) + max(1, ttl)
    key = waiting_key(key_prefix)
    try:
        redis_client.zadd(key, {job: deadline})
        redis_client.expire(key, max(1, ttl) * 2)
    except Exception:
        # Never let bookkeeping break a dispatch: a missed mark only means the
        # batch is not deferred this round.
        return


def clear_waiting(redis_client: Any, *, key_prefix: str, job_id: str) -> None:
    job = str(job_id or "").strip()
    if not job:
        return
    try:
        redis_client.zrem(waiting_key(key_prefix), job)
    except Exception:
        return


def interactive_waiting(
    redis_client: Any,
    *,
    key_prefix: str,
    now: Optional[float] = None,
) -> int:
    """
    How many interactive jobs are waiting for a slot right now.

    Expired registrations are pruned first, so a crashed waiter stops blocking
    the batch once its deadline passes.
    """
    key = waiting_key(key_prefix)
    cutoff = float(now if now is not None else time.time())
    try:
        redis_client.zremrangebyscore(key, "-inf", cutoff)
        return max(0, int(redis_client.zcard(key) or 0))
    except Exception:
        # Fail open: if the registry is unreadable the batch keeps running.
        return 0


def waiting_job_ids(
    redis_client: Any,
    *,
    key_prefix: str,
    limit: int = 20,
) -> Sequence[str]:
    """Waiting job ids, oldest deadline first. Diagnostics only."""
    try:
        raw = redis_client.zrange(waiting_key(key_prefix), 0, max(0, int(limit) - 1))
    except Exception:
        return ()
    out: list[str] = []
    for item in raw or ():
        out.append(item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item))
    return tuple(out)


def _float_env(key: str, default: float) -> float:
    try:
        value = float((os.environ.get(key) or "").strip())
    except Exception:
        return default
    return value if value > 0 else default


def interactive_backoff_s() -> tuple[float, float]:
    """Retry window for a live user waiting on a slot: short, so it wins the race."""
    return (
        _float_env("RENDER_PRIORITY_INTERACTIVE_BACKOFF_BASE_S", 3.0),
        _float_env("RENDER_PRIORITY_INTERACTIVE_BACKOFF_CAP_S", 15.0),
    )


def batch_defer_backoff_s() -> tuple[float, float]:
    """Retry window for a batch job yielding to a waiting user."""
    return (
        _float_env("RENDER_PRIORITY_BATCH_DEFER_BASE_S", 5.0),
        _float_env("RENDER_PRIORITY_BATCH_DEFER_CAP_S", 30.0),
    )
