from __future__ import annotations

from typing import Dict

from .job_store import JobStore


def _sanitize_part(raw: str, *, default: str) -> str:
    txt = str(raw or "").strip().lower()
    if not txt:
        txt = default
    out_chars: list[str] = []
    for ch in txt:
        if ch.isalnum() or ch in {"_", "-", "."}:
            out_chars.append(ch)
        else:
            out_chars.append("_")
    cleaned = "".join(out_chars).strip("_")
    return cleaned or default


def _counter_key(store: JobStore, metric: str) -> str:
    name = _sanitize_part(metric, default="unknown_metric")
    return f"{store.key_prefix}:metrics:{name}:v1"


def _counter_field(label: str) -> str:
    return _sanitize_part(label, default="total")


def increment_counter(
    store: JobStore,
    *,
    metric: str,
    label: str = "total",
    amount: int = 1,
) -> int:
    inc = int(amount)
    if inc == 0:
        return 0
    key = _counter_key(store, metric)
    field = _counter_field(label)
    rv = store._redis_call(
        "metrics_hincrby",
        lambda: store.r.hincrby(key, field, inc),
    )
    try:
        return int(rv)
    except Exception:
        return 0


def get_counter_map(store: JobStore, *, metric: str) -> Dict[str, int]:
    key = _counter_key(store, metric)
    raw = store._redis_call("metrics_hgetall", lambda: store.r.hgetall(key)) or {}
    out: Dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except Exception:
            continue
    return out
