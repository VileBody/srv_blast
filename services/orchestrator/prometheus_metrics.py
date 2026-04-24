from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from .backpressure_policy import (
    BACKPRESSURE_STATE_DEGRADED,
    BACKPRESSURE_STATE_MANUAL_MAINTENANCE_RECOMMENDED,
    BACKPRESSURE_STATE_NORMAL,
    compute_capacity_policy,
)
from .config import SETTINGS
from .job_store import JobStore
from .llm_workers import get_runtime_status
from .observability_metrics import (
    GEMINI_LATENCY_BUCKETS,
    STAGE_DURATION_BUCKETS,
    get_counter_map,
    get_labeled_counter_samples,
    get_labeled_histogram_samples,
)
from .runtime_config import (
    build_capacity_policy_snapshot,
    build_llm_saturation,
    get_runtime_config,
    get_runtime_values,
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
        "gemini_token_total",
        "Gemini token usage from response usage_metadata",
        ("provider", "model", "stage", "token_type"),
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
        stage_counts: dict[str, int] = {}
        render_backlog = 0
        build_backlog = 0
        try:
            for job in self._store.list_jobs():
                status = str(getattr(job, "status", "") or "").strip().lower()
                if status in counts:
                    counts[status] += 1
                stage = str(getattr(job, "stage", "") or "none").strip().lower() or "none"
                stage_counts[stage] = int(stage_counts.get(stage, 0)) + 1
                if status in {"new", "queued", "running"}:
                    if "render" in stage or stage in {"dispatch", "poll", "render_dispatch", "render_poll"}:
                        render_backlog += 1
                    else:
                        build_backlog += 1
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
        yield GaugeMetricFamily("render_backlog", "Current render-side backlog", value=float(render_backlog))
        yield GaugeMetricFamily("build_backlog", "Current build-side backlog", value=float(build_backlog))

        stage_g = GaugeMetricFamily(
            "job_stage_count",
            "Current number of jobs by stage",
            labels=["stage"],
        )
        for stage, value in sorted(stage_counts.items()):
            stage_g.add_metric([str(stage or "none")], float(int(value)))
        yield stage_g

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

        llm_status = {}
        try:
            llm_status = get_runtime_status(self._store)
        except Exception:
            llm_status = {}

        llm_max_g = GaugeMetricFamily(
            "llm_worker_max_inflight",
            "Configured max inflight per LLM worker type",
            labels=["worker_type", "enabled"],
        )
        llm_available_g = GaugeMetricFamily(
            "llm_worker_available_slots",
            "Currently available slots per LLM worker type",
            labels=["worker_type", "enabled"],
        )
        llm_saturated_g = GaugeMetricFamily(
            "llm_worker_saturated",
            "Whether an LLM worker type is saturated (1=true, 0=false)",
            labels=["worker_type", "enabled"],
        )
        llm_enabled_g = GaugeMetricFamily(
            "llm_worker_enabled",
            "Whether an LLM worker type is enabled (1=true, 0=false)",
            labels=["worker_type"],
        )
        llm_runtime_payload: dict[str, dict[str, object]] = {}
        llm_saturation_payload: dict[str, dict[str, int | bool]] = {}
        for worker_type, row in sorted((llm_status or {}).items()):
            enabled = bool(getattr(row, "enabled", False))
            enabled_label = "true" if enabled else "false"
            max_inflight = int(getattr(row, "max_inflight", 0) or 0)
            available_slots = int(getattr(row, "available_slots", 0) or 0)
            inflight = int(getattr(row, "inflight", 0) or 0)
            weight = int(getattr(row, "weight", 0) or 0)
            saturated = bool(enabled and max_inflight > 0 and inflight >= max_inflight)
            llm_runtime_payload[str(worker_type)] = (
                row.model_dump(mode="json") if hasattr(row, "model_dump") else {}
            )
            llm_saturation_payload[str(worker_type)] = {
                "enabled": enabled,
                "weight": weight,
                "max_inflight": max_inflight,
                "inflight": inflight,
                "available_slots": available_slots,
                "saturated": saturated,
            }
            llm_max_g.add_metric([str(worker_type), enabled_label], float(max_inflight))
            llm_available_g.add_metric([str(worker_type), enabled_label], float(available_slots))
            llm_saturated_g.add_metric([str(worker_type), enabled_label], 1.0 if saturated else 0.0)
            llm_enabled_g.add_metric([str(worker_type)], 1.0 if enabled else 0.0)
        yield llm_max_g
        yield llm_available_g
        yield llm_saturated_g
        yield llm_enabled_g

        llm_saturation_ratio = build_llm_saturation(llm_runtime_payload)
        if llm_saturation_ratio:
            sat_g = GaugeMetricFamily(
                "llm_saturation_ratio",
                "Current LLM worker inflight/max_inflight ratio",
                labels=["worker_type"],
            )
            for worker_type, row in sorted(llm_saturation_ratio.items()):
                sat_g.add_metric([worker_type], float(row.get("ratio", 0.0) or 0.0))
            yield sat_g

        capacity_policy = compute_capacity_policy(
            render_backlog=render_backlog,
            build_backlog=build_backlog,
            llm_saturation_by_worker_type=llm_saturation_payload,
            render_backlog_degraded_threshold=int(SETTINGS.render_backlog_degraded_threshold),
            render_backlog_scaleout_threshold=int(SETTINGS.render_backlog_scaleout_threshold),
            build_backlog_degraded_threshold=int(SETTINGS.build_backlog_degraded_threshold),
            build_backlog_manual_maintenance_threshold=int(SETTINGS.build_backlog_manual_maintenance_threshold),
        )
        policy_state = str(capacity_policy.get("state") or BACKPRESSURE_STATE_NORMAL).strip().lower()
        policy_g = GaugeMetricFamily(
            "backpressure_policy_state",
            "Current backpressure policy state (1 for active state, 0 otherwise)",
            labels=["state"],
        )
        for state in (
            BACKPRESSURE_STATE_NORMAL,
            BACKPRESSURE_STATE_DEGRADED,
            BACKPRESSURE_STATE_MANUAL_MAINTENANCE_RECOMMENDED,
        ):
            policy_g.add_metric([state], 1.0 if policy_state == state else 0.0)
        yield policy_g

        yield GaugeMetricFamily(
            "render_poll_split_active",
            "Whether render dispatch and render poll use different queues (1=true, 0=false)",
            value=1.0
            if str(SETTINGS.celery_queue_render_poll or "").strip()
            and str(SETTINGS.celery_queue_render_poll or "").strip() != str(SETTINGS.celery_queue_render or "").strip()
            else 0.0,
        )

        try:
            runtime_cfg = get_runtime_config(self._store)
            runtime_values = get_runtime_values(self._store)
            policy = build_capacity_policy_snapshot(
                values=runtime_values,
                job_status_counts={k.upper(): v for k, v in counts.items()},
                job_stage_counts=stage_counts,
                llm_saturation_by_worker_type=llm_saturation_ratio,
            )
            state = str(policy.get("state") or "unknown")
            policy_state_g = GaugeMetricFamily(
                "capacity_policy_state",
                "Current backpressure policy state as one-hot gauge",
                labels=["state"],
            )
            for candidate in ("normal", "degraded", "manual-maintenance-recommended", "unknown"):
                policy_state_g.add_metric([candidate], 1.0 if candidate == state else 0.0)
            yield policy_state_g

            signals = policy.get("signals") if isinstance(policy.get("signals"), dict) else {}
            if signals:
                signal_g = GaugeMetricFamily(
                    "capacity_policy_signal",
                    "Backpressure policy input signals",
                    labels=["signal"],
                )
                for signal, raw_value in sorted(signals.items()):
                    if isinstance(raw_value, (int, float)):
                        signal_g.add_metric([str(signal)], float(raw_value))
                yield signal_g

            items = runtime_cfg.get("items") if isinstance(runtime_cfg.get("items"), list) else []
            cfg_g = GaugeMetricFamily(
                "runtime_config_numeric_value",
                "Current numeric/bool runtime configuration values",
                labels=["key", "category", "runtime_effect"],
            )
            emitted = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if isinstance(value, bool):
                    numeric = 1.0 if value else 0.0
                elif isinstance(value, (int, float)):
                    numeric = float(value)
                else:
                    continue
                cfg_g.add_metric(
                    [
                        str(item.get("key") or ""),
                        str(item.get("category") or ""),
                        str(item.get("runtime_effect") or ""),
                    ],
                    numeric,
                )
                emitted = True
            if emitted:
                yield cfg_g
        except Exception:
            pass

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
