from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .job_store import JobStore


# Buckets are explicit and deterministic to keep dashboard math stable.
STAGE_DURATION_BUCKETS: tuple[float, ...] = (
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1200.0,
    2400.0,
    3600.0,
)

GEMINI_LATENCY_BUCKETS: tuple[float, ...] = (
    0.25,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    300.0,
)


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


def _sanitize_label_name(raw: str, *, default: str) -> str:
    txt = str(raw or "").strip().lower()
    if not txt:
        return default
    out_chars: list[str] = []
    for ch in txt:
        if ch.isalnum() or ch == "_":
            out_chars.append(ch)
        else:
            out_chars.append("_")
    cleaned = "".join(out_chars).strip("_")
    if not cleaned:
        cleaned = default
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _normalize_labels(labels: Mapping[str, Any] | None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(labels, Mapping):
        return out
    for k, v in labels.items():
        nk = _sanitize_label_name(str(k or ""), default="label")
        nv = _sanitize_part(str(v or ""), default="unknown")
        if not nk:
            continue
        out[nk] = nv
    return out


def _labels_field(labels: Mapping[str, Any] | None) -> str:
    norm = _normalize_labels(labels)
    return json.dumps(norm, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_labels_field(raw: str) -> Dict[str, str]:
    text = str(raw or "{}")
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    return _normalize_labels({str(k): str(v) for k, v in obj.items()})


def _counter_key(store: JobStore, metric: str) -> str:
    name = _sanitize_part(metric, default="unknown_metric")
    return f"{store.key_prefix}:metrics:{name}:v1"


def _counter_field(label: str) -> str:
    return _sanitize_part(label, default="total")


def _labeled_counter_key(store: JobStore, metric: str) -> str:
    name = _sanitize_part(metric, default="unknown_metric")
    return f"{store.key_prefix}:metrics:labeled_counter:{name}:v1"


def _hist_count_key(store: JobStore, metric: str) -> str:
    name = _sanitize_part(metric, default="unknown_metric")
    return f"{store.key_prefix}:metrics:hist:{name}:count:v1"


def _hist_sum_key(store: JobStore, metric: str) -> str:
    name = _sanitize_part(metric, default="unknown_metric")
    return f"{store.key_prefix}:metrics:hist:{name}:sum:v1"


def _hist_bucket_key(store: JobStore, metric: str) -> str:
    name = _sanitize_part(metric, default="unknown_metric")
    return f"{store.key_prefix}:metrics:hist:{name}:bucket:v1"


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


def increment_labeled_counter(
    store: JobStore,
    *,
    metric: str,
    labels: Mapping[str, Any] | None = None,
    amount: int = 1,
) -> int:
    inc = int(amount)
    if inc == 0:
        return 0
    key = _labeled_counter_key(store, metric)
    field = _labels_field(labels)
    rv = store._redis_call(
        "metrics_labeled_hincrby",
        lambda: store.r.hincrby(key, field, inc),
    )
    try:
        return int(rv)
    except Exception:
        return 0


def get_labeled_counter_samples(
    store: JobStore,
    *,
    metric: str,
) -> list[tuple[Dict[str, str], int]]:
    key = _labeled_counter_key(store, metric)
    raw = store._redis_call("metrics_labeled_hgetall", lambda: store.r.hgetall(key)) or {}
    out: list[tuple[Dict[str, str], int]] = []
    if not isinstance(raw, dict):
        return out
    for field, value in raw.items():
        try:
            count = int(value)
        except Exception:
            continue
        labels = _parse_labels_field(str(field))
        out.append((labels, count))
    return out


def _normalize_hist_buckets(buckets: Iterable[float] | None) -> list[float]:
    vals: list[float] = []
    for raw in (buckets or []):
        try:
            v = float(raw)
        except Exception:
            continue
        if not math.isfinite(v):
            continue
        vals.append(v)
    vals = sorted({v for v in vals if v > 0.0})
    if not vals:
        vals = [1.0, 5.0, 10.0, 30.0, 60.0]
    return vals


def observe_labeled_histogram(
    store: JobStore,
    *,
    metric: str,
    value: float,
    buckets: Iterable[float],
    labels: Mapping[str, Any] | None = None,
) -> None:
    try:
        val = float(value)
    except Exception:
        return
    if not math.isfinite(val):
        return

    bounds = _normalize_hist_buckets(buckets)
    label_field = _labels_field(labels)
    count_key = _hist_count_key(store, metric)
    sum_key = _hist_sum_key(store, metric)
    bucket_key = _hist_bucket_key(store, metric)

    store._redis_call(
        "metrics_hist_hincrby_count",
        lambda: store.r.hincrby(count_key, label_field, 1),
    )
    store._redis_call(
        "metrics_hist_hincrbyfloat_sum",
        lambda: store.r.hincrbyfloat(sum_key, label_field, float(val)),
    )

    for le in bounds:
        if val <= le:
            bucket_field = f"{label_field}\tle={le:g}"
            store._redis_call(
                "metrics_hist_hincrby_bucket",
                lambda k=bucket_key, f=bucket_field: store.r.hincrby(k, f, 1),
            )


def get_labeled_histogram_samples(
    store: JobStore,
    *,
    metric: str,
) -> list[dict[str, Any]]:
    count_key = _hist_count_key(store, metric)
    sum_key = _hist_sum_key(store, metric)
    bucket_key = _hist_bucket_key(store, metric)

    raw_counts = store._redis_call("metrics_hist_hgetall_count", lambda: store.r.hgetall(count_key)) or {}
    raw_sums = store._redis_call("metrics_hist_hgetall_sum", lambda: store.r.hgetall(sum_key)) or {}
    raw_buckets = store._redis_call("metrics_hist_hgetall_bucket", lambda: store.r.hgetall(bucket_key)) or {}

    counts: dict[str, int] = {}
    sums: dict[str, float] = {}
    buckets_by_field: dict[str, dict[float, int]] = {}

    if isinstance(raw_counts, dict):
        for k, v in raw_counts.items():
            try:
                counts[str(k)] = int(v)
            except Exception:
                continue

    if isinstance(raw_sums, dict):
        for k, v in raw_sums.items():
            try:
                sums[str(k)] = float(v)
            except Exception:
                continue

    if isinstance(raw_buckets, dict):
        for k, v in raw_buckets.items():
            field_raw = str(k)
            labels_field, sep, le_tag = field_raw.partition("\tle=")
            if not sep:
                continue
            try:
                le = float(le_tag)
                cnt = int(v)
            except Exception:
                continue
            bucket_map = buckets_by_field.setdefault(labels_field, {})
            bucket_map[le] = int(cnt)

    fields = sorted(set(counts.keys()) | set(sums.keys()) | set(buckets_by_field.keys()))
    out: list[dict[str, Any]] = []
    for field in fields:
        out.append(
            {
                "labels": _parse_labels_field(field),
                "count": int(counts.get(field, 0)),
                "sum": float(sums.get(field, 0.0)),
                "buckets": dict(sorted((buckets_by_field.get(field) or {}).items())),
            }
        )
    return out


_ENV_STORE: JobStore | None = None


def _env_store() -> JobStore:
    global _ENV_STORE
    if _ENV_STORE is None:
        from .job_store import JobStore as _JobStore

        _ENV_STORE = _JobStore.from_env()
    return _ENV_STORE


def increment_labeled_counter_from_env(
    *,
    metric: str,
    labels: Mapping[str, Any] | None = None,
    amount: int = 1,
) -> None:
    try:
        increment_labeled_counter(_env_store(), metric=metric, labels=labels, amount=amount)
    except Exception:
        return


def observe_labeled_histogram_from_env(
    *,
    metric: str,
    value: float,
    buckets: Iterable[float],
    labels: Mapping[str, Any] | None = None,
) -> None:
    try:
        observe_labeled_histogram(
            _env_store(),
            metric=metric,
            value=value,
            buckets=buckets,
            labels=labels,
        )
    except Exception:
        return
