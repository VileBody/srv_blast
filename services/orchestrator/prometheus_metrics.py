from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from .job_store import JobStore
from .observability_metrics import (
    GEMINI_LATENCY_BUCKETS,
    STAGE_DURATION_BUCKETS,
    get_counter_map,
    get_labeled_counter_samples,
    get_labeled_histogram_samples,
)


_LABELED_COUNTER_METRICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "job_lifecycle_total",
        "Total job lifecycle events grouped by resulting status",
        ("status",),
    ),
    (
        "stage_transition_total",
        "Total stage transitions",
        ("from_stage", "to_stage", "outcome"),
    ),
    (
        "dispatch_attempt_total",
        "Windows dispatch attempts/outcomes",
        ("node", "api_mode", "outcome"),
    ),
    (
        "dispatch_recovery_total",
        "Dispatch recovery outcomes",
        ("outcome",),
    ),
    (
        "render_poll_total",
        "Windows render poll outcomes",
        ("node", "outcome"),
    ),
    (
        "render_poll_timeout_total",
        "Windows render poll timeout outcomes",
        ("phase",),
    ),
    (
        "windows_node_state_change_total",
        "Windows node state changes (enabled/disabled)",
        ("node", "event", "reason"),
    ),
    (
        "gemini_call_total",
        "Gemini call outcomes by model and stage",
        ("model", "stage", "outcome", "code_class"),
    ),
    (
        "gemini_fallback_total",
        "Gemini fallback outcomes",
        ("primary_model", "fallback_model", "outcome", "reason_class"),
    ),
    (
        "gemini_warning_total",
        "Gemini warnings by warning type/model/stage",
        ("warning_type", "model", "stage"),
    ),
)

_LEGACY_COUNTER_MAPS: tuple[tuple[str, str, str, str], ...] = (
    (
        "payment_webhook_outcomes",
        "payment_webhook_outcomes_total",
        "Payment webhook outcomes",
        "outcome",
    ),
    (
        "payment_activate_outcomes",
        "payment_activate_outcomes_total",
        "Payment activation outcomes",
        "outcome",
    ),
    (
        "render_poll_timeout_outcomes",
        "render_poll_timeout_outcomes_total",
        "Legacy render poll timeout outcomes",
        "phase",
    ),
    (
        "dispatch_recovery_outcomes",
        "dispatch_recovery_outcomes_total",
        "Legacy dispatch recovery outcomes",
        "outcome",
    ),
)

_HISTOGRAM_METRICS: tuple[tuple[str, str, tuple[str, ...], tuple[float, ...]], ...] = (
    (
        "stage_duration_seconds",
        "Duration of orchestration stages in seconds",
        ("stage", "outcome"),
        STAGE_DURATION_BUCKETS,
    ),
    (
        "gemini_latency_seconds",
        "Latency of Gemini API calls in seconds",
        ("model", "stage", "outcome", "code_class"),
        GEMINI_LATENCY_BUCKETS,
    ),
)


def _label_values(labels: dict[str, str], names: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for name in names:
        v = str(labels.get(name) or "unknown").strip() or "unknown"
        out.append(v)
    return out


def _fmt_le(v: float) -> str:
    return f"{float(v):g}"


class _RedisBackedCollector:
    def __init__(self, store: JobStore):
        self._store = store

    def collect(self):
        counts: dict[str, int] = {
            "new": 0,
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
        }
        try:
            for job in self._store.list_jobs():
                status = str(getattr(job, "status", "") or "").strip().lower()
                if status in counts:
                    counts[status] += 1
        except Exception:
            pass

        status_g = GaugeMetricFamily(
            "job_status_count",
            "Current number of jobs by status",
            labels=["status"],
        )
        for status, value in counts.items():
            status_g.add_metric([status], float(value))
        yield status_g

        yield GaugeMetricFamily("queue_depth", "Current queued jobs", value=float(counts["queued"]))
        yield GaugeMetricFamily("inflight_jobs", "Current running jobs", value=float(counts["running"]))
        yield GaugeMetricFamily("failed_jobs", "Current failed jobs", value=float(counts["failed"]))

        try:
            from .llm_workers import get_inflight_counts

            llm_inflight = get_inflight_counts(self._store)
        except Exception:
            llm_inflight = {}
        llm_g = GaugeMetricFamily(
            "llm_inflight_by_worker_type",
            "Current in-flight LLM jobs by worker type",
            labels=["worker_type"],
        )
        for worker_type, count in sorted((llm_inflight or {}).items()):
            llm_g.add_metric([str(worker_type or "unknown")], float(int(count or 0)))
        yield llm_g

        for source_metric, target_metric, help_text, label_name in _LEGACY_COUNTER_MAPS:
            raw = get_counter_map(self._store, metric=source_metric)
            if not raw:
                continue
            fam = CounterMetricFamily(target_metric, help_text, labels=[label_name])
            for label, value in sorted(raw.items()):
                fam.add_metric([str(label)], float(int(value)))
            yield fam

        for metric_name, help_text, label_names in _LABELED_COUNTER_METRICS:
            samples = get_labeled_counter_samples(self._store, metric=metric_name)
            if not samples:
                continue
            fam = CounterMetricFamily(metric_name, help_text, labels=list(label_names))
            for labels, value in samples:
                fam.add_metric(_label_values(labels, label_names), float(int(value)))
            yield fam

        for metric_name, help_text, label_names, bounds in _HISTOGRAM_METRICS:
            samples = get_labeled_histogram_samples(self._store, metric=metric_name)
            if not samples:
                continue
            fam = HistogramMetricFamily(metric_name, help_text, labels=list(label_names))
            ordered_bounds = sorted(float(b) for b in bounds)
            for sample in samples:
                labels = sample.get("labels")
                if not isinstance(labels, dict):
                    labels = {}
                bucket_map_raw = sample.get("buckets")
                bucket_map = bucket_map_raw if isinstance(bucket_map_raw, dict) else {}
                count = int(sample.get("count", 0) or 0)
                sum_val = float(sample.get("sum", 0.0) or 0.0)

                bucket_samples: list[tuple[str, float]] = []
                for le in ordered_bounds:
                    bucket_samples.append((_fmt_le(le), float(int(bucket_map.get(le, 0) or 0))))
                bucket_samples.append(("+Inf", float(count)))
                fam.add_metric(_label_values(labels, label_names), buckets=bucket_samples, sum_value=sum_val)
            yield fam


def build_prometheus_metrics_payload(store: JobStore) -> tuple[bytes, str]:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_RedisBackedCollector(store))
    payload = generate_latest(registry)
    return payload, CONTENT_TYPE_LATEST
