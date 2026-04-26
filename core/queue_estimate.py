from __future__ import annotations

import math
from typing import Any, Iterable


ACTIVE_JOB_STATUSES = {"NEW", "QUEUED", "RUNNING"}
DEFAULT_QUEUE_ESTIMATE_WINDOW = 50


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _float_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _status_of(job: Any) -> str:
    return str(_get(job, "status", "") or "").upper()


def _job_id_of(job: Any) -> str:
    return str(_get(job, "job_id", "") or "").strip()


def normalize_queue_estimate_window(window_size: int | None) -> int:
    try:
        raw = int(window_size or DEFAULT_QUEUE_ESTIMATE_WINDOW)
    except (TypeError, ValueError):
        raw = DEFAULT_QUEUE_ESTIMATE_WINDOW
    if raw <= 0:
        raw = DEFAULT_QUEUE_ESTIMATE_WINDOW
    return max(1, min(raw, 500))


def build_queue_estimate(
    jobs: Iterable[Any],
    *,
    job_id: str,
    window_size: int | None = DEFAULT_QUEUE_ESTIMATE_WINDOW,
    now: float | None = None,
) -> dict[str, Any] | None:
    target_id = str(job_id or "").strip()
    if not target_id:
        return None

    now_s = _float_or_none(now)
    if now_s is None:
        import time

        now_s = time.time()

    all_jobs = list(jobs or [])
    target = None
    for job in all_jobs:
        if _job_id_of(job) == target_id:
            target = job
            break
    if target is None:
        return None

    active_jobs = [job for job in all_jobs if _status_of(job) in ACTIVE_JOB_STATUSES]
    active_jobs.sort(
        key=lambda job: (
            _float_or_none(_get(job, "created_at")) or 0.0,
            _job_id_of(job),
        )
    )

    queue_position = 0
    active_slice: list[Any] = []
    for idx, job in enumerate(active_jobs, start=1):
        active_slice.append(job)
        if _job_id_of(job) == target_id:
            queue_position = idx
            break

    normalized_window = normalize_queue_estimate_window(window_size)
    successful_durations: list[tuple[float, float]] = []
    for job in all_jobs:
        if _status_of(job) != "SUCCEEDED":
            continue
        created_at = _float_or_none(_get(job, "created_at"))
        finished_at = _float_or_none(_get(job, "finished_at"))
        if created_at is None or finished_at is None:
            continue
        duration_s = finished_at - created_at
        if duration_s <= 0:
            continue
        successful_durations.append((finished_at, duration_s))
    successful_durations.sort(key=lambda item: item[0], reverse=True)
    duration_samples = [duration for _, duration in successful_durations[:normalized_window]]

    avg_duration_s: float | None = None
    if duration_samples:
        avg_duration_s = sum(duration_samples) / float(len(duration_samples))

    eta_s: float | None
    if queue_position <= 0:
        eta_s = 0.0
    elif avg_duration_s is None:
        eta_s = None
    else:
        eta_s = 0.0
        for job in active_slice:
            status = _status_of(job)
            started_at = _float_or_none(_get(job, "started_at"))
            if status == "RUNNING" and started_at is not None:
                eta_s += max(0.0, avg_duration_s - max(0.0, now_s - started_at))
            else:
                eta_s += avg_duration_s

    return {
        "job_id": target_id,
        "status": _status_of(target),
        "active": queue_position > 0,
        "queue_position": int(queue_position),
        "active_jobs_total": int(len(active_jobs)),
        "window_size": int(normalized_window),
        "sample_size": int(len(duration_samples)),
        "avg_duration_seconds": avg_duration_s,
        "eta_seconds": eta_s,
    }


def pick_queue_estimate_job_id(rows: Iterable[dict[str, Any]]) -> str:
    for row in rows or []:
        status = str(row.get("status") or "").upper()
        if status not in {"SUCCEEDED", "FAILED"}:
            jid = str(row.get("job_id") or "").strip()
            if jid:
                return jid
    return ""


def format_wait_seconds_ru(seconds: Any) -> str:
    value = _float_or_none(seconds)
    if value is None:
        return ""
    if value <= 0:
        return "меньше минуты"
    minutes = max(1, int(math.ceil(value / 60.0)))
    if minutes < 60:
        return f"~{minutes} мин"
    hours = minutes // 60
    rem = minutes % 60
    if rem <= 0:
        return f"~{hours} ч"
    return f"~{hours} ч {rem} мин"


def format_queue_estimate_lines(estimate: dict[str, Any] | None) -> list[str]:
    if not isinstance(estimate, dict) or not bool(estimate.get("active")):
        return []

    try:
        position = int(estimate.get("queue_position") or 0)
        total = int(estimate.get("active_jobs_total") or 0)
    except (TypeError, ValueError):
        return []
    if position <= 0 or total <= 0:
        return []

    lines = [f"Очередь: #{position} из {total}"]
    wait_text = format_wait_seconds_ru(estimate.get("eta_seconds"))
    if wait_text:
        lines.append(f"Ожидание: примерно {wait_text}")
    else:
        sample_size = int(estimate.get("sample_size") or 0)
        if sample_size <= 0:
            lines.append("Ожидание: статистика ещё копится")
    return lines
