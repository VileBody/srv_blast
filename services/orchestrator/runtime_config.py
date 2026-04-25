from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .job_store import JobStore


@dataclass(frozen=True)
class RuntimeConfigSpec:
    key: str
    title: str
    kind: str
    default: Any
    category: str
    runtime_effect: str
    description: str
    min_value: float | None = None
    max_value: float | None = None
    max_length: int = 500


SPECS: tuple[RuntimeConfigSpec, ...] = (
    RuntimeConfigSpec(
        key="backpressure.build_backlog_degraded",
        title="Build backlog degraded threshold",
        kind="int",
        default=30,
        min_value=0,
        max_value=100000,
        category="backpressure",
        runtime_effect="hot",
        description="Build queue depth where operator UI should mark capacity as degraded.",
    ),
    RuntimeConfigSpec(
        key="backpressure.build_backlog_maintenance_recommended",
        title="Build backlog manual-maintenance threshold",
        kind="int",
        default=80,
        min_value=0,
        max_value=100000,
        category="backpressure",
        runtime_effect="hot",
        description="Build queue depth where manual maintenance or traffic pause should be considered.",
    ),
    RuntimeConfigSpec(
        key="backpressure.render_backlog_degraded",
        title="Render backlog degraded threshold",
        kind="int",
        default=100,
        min_value=0,
        max_value=100000,
        category="backpressure",
        runtime_effect="hot",
        description="Render/dispatch/poll stage count where render capacity is considered degraded.",
    ),
    RuntimeConfigSpec(
        key="backpressure.render_backlog_add_windows_node",
        title="Render backlog add-node threshold",
        kind="int",
        default=300,
        min_value=0,
        max_value=100000,
        category="backpressure",
        runtime_effect="hot",
        description="Render backlog where operator action is to add another Windows render node.",
    ),
    RuntimeConfigSpec(
        key="backpressure.llm_saturation_degraded_pct",
        title="LLM saturation degraded ratio",
        kind="float",
        default=0.85,
        min_value=0.0,
        max_value=1.0,
        category="backpressure",
        runtime_effect="hot",
        description="Highest worker-type inflight/max ratio that marks LLM capacity as degraded.",
    ),
    RuntimeConfigSpec(
        key="backpressure.llm_saturation_maintenance_pct",
        title="LLM saturation manual-maintenance ratio",
        kind="float",
        default=0.98,
        min_value=0.0,
        max_value=1.0,
        category="backpressure",
        runtime_effect="hot",
        description="Highest worker-type inflight/max ratio where manual maintenance should be considered.",
    ),
    RuntimeConfigSpec(
        key="backpressure.user_degraded_copy",
        title="Accepted-but-delayed user copy",
        kind="str",
        default="Задача принята, но сейчас очередь выше обычного. Мы начнем обработку автоматически, как только освободится слот.",
        category="backpressure",
        runtime_effect="hot",
        description="Operator-visible copy to reuse for accepted-but-delayed UX.",
        max_length=500,
    ),
    RuntimeConfigSpec(
        key="gemini.transport_retry_enabled",
        title="Gemini transport retry",
        kind="bool",
        default=True,
        category="gemini",
        runtime_effect="hot",
        description="Retry the same Celery build job on google.genai/httpx transport disconnects.",
    ),
    RuntimeConfigSpec(
        key="gemini.transport_retry_base_s",
        title="Gemini transport retry base seconds",
        kind="float",
        default=10.0,
        min_value=0.5,
        max_value=3600.0,
        category="gemini",
        runtime_effect="hot",
        description="Base backoff for Gemini transport disconnect retries.",
    ),
    RuntimeConfigSpec(
        key="gemini.transport_retry_cap_s",
        title="Gemini transport retry cap seconds",
        kind="float",
        default=300.0,
        min_value=1.0,
        max_value=7200.0,
        category="gemini",
        runtime_effect="hot",
        description="Maximum backoff for Gemini transport disconnect retries.",
    ),
    RuntimeConfigSpec(
        key="gemini.max_thinking_tokens",
        title="Gemini max thinking tokens",
        kind="int",
        default=2500,
        min_value=1,
        max_value=100000,
        category="gemini",
        runtime_effect="hot",
        description="Runtime override for GEMINI_MAX_THINKING_TOKENS applied before each build job.",
    ),
    RuntimeConfigSpec(
        key="worker.build_concurrency_per_node",
        title="Build worker concurrency per node",
        kind="int",
        default=4,
        min_value=1,
        max_value=128,
        category="workers",
        runtime_effect="requires_worker_recreate",
        description="Documented target for worker-build --concurrency; changing it still requires worker recreate.",
    ),
    RuntimeConfigSpec(
        key="worker.render_dispatch_concurrency_per_node",
        title="Render dispatch worker concurrency per node",
        kind="int",
        default=1,
        min_value=1,
        max_value=64,
        category="workers",
        runtime_effect="requires_worker_recreate",
        description="Documented target for worker-render dispatch concurrency.",
    ),
    RuntimeConfigSpec(
        key="worker.render_poll_concurrency_per_node",
        title="Render poll worker concurrency per node",
        kind="int",
        default=1,
        min_value=1,
        max_value=128,
        category="workers",
        runtime_effect="requires_worker_recreate",
        description="Documented target for worker-render-poll concurrency.",
    ),
    RuntimeConfigSpec(
        key="telegram.processing_max_concurrency",
        title="Telegram processing loop concurrency",
        kind="int",
        default=4,
        min_value=1,
        max_value=256,
        category="telegram",
        runtime_effect="requires_bot_recreate",
        description="Documented target for TG_PROCESSING_MAX_CONCURRENCY; bot must read/reload it before hot apply.",
    ),
    RuntimeConfigSpec(
        key="telegram.status_update_interval_s",
        title="Telegram status update interval seconds",
        kind="float",
        default=10.0,
        min_value=0.5,
        max_value=600.0,
        category="telegram",
        runtime_effect="requires_bot_recreate",
        description="Documented target for bot status update cadence.",
    ),
    RuntimeConfigSpec(
        key="outbox.dispatch_batch_size",
        title="Outbox dispatch batch size",
        kind="int",
        default=20,
        min_value=1,
        max_value=1000,
        category="outbox",
        runtime_effect="requires_bot_recreate",
        description="Documented target for delivery outbox dispatcher batch size.",
    ),
    RuntimeConfigSpec(
        key="windows.poll_interval_s",
        title="Windows render poll interval seconds",
        kind="float",
        default=10.0,
        min_value=1.0,
        max_value=600.0,
        category="windows",
        runtime_effect="requires_worker_recreate",
        description="Documented target for render poll cadence.",
    ),
    RuntimeConfigSpec(
        key="windows.poll_timeout_s",
        title="Windows render poll timeout seconds",
        kind="float",
        default=3600.0,
        min_value=60.0,
        max_value=86400.0,
        category="windows",
        runtime_effect="requires_worker_recreate",
        description="Documented target for maximum render wait time.",
    ),
)

SPEC_BY_KEY: dict[str, RuntimeConfigSpec] = {spec.key: spec for spec in SPECS}


def _config_key(prefix: str) -> str:
    return f"{prefix}:runtime_config:v1"


def _coerce_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    txt = str(raw).strip().lower()
    if txt in {"1", "true", "yes", "on"}:
        return True
    if txt in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected bool-like value, got {raw!r}")


def coerce_value(spec: RuntimeConfigSpec, raw: Any) -> Any:
    if spec.kind == "bool":
        return _coerce_bool(raw)
    if spec.kind == "int":
        value = int(str(raw).strip())
        if spec.min_value is not None and value < int(spec.min_value):
            raise ValueError(f"{spec.key} must be >= {int(spec.min_value)}")
        if spec.max_value is not None and value > int(spec.max_value):
            raise ValueError(f"{spec.key} must be <= {int(spec.max_value)}")
        return value
    if spec.kind == "float":
        value = float(str(raw).strip())
        if not math.isfinite(value):
            raise ValueError(f"{spec.key} must be finite")
        if spec.min_value is not None and value < float(spec.min_value):
            raise ValueError(f"{spec.key} must be >= {float(spec.min_value)}")
        if spec.max_value is not None and value > float(spec.max_value):
            raise ValueError(f"{spec.key} must be <= {float(spec.max_value)}")
        return value
    if spec.kind == "str":
        value = str(raw)
        if len(value) > int(spec.max_length):
            raise ValueError(f"{spec.key} length must be <= {int(spec.max_length)}")
        return value
    raise ValueError(f"unsupported runtime config kind={spec.kind!r}")


def _load_document(store: "JobStore") -> dict[str, Any]:
    raw = store._redis_call("runtime_config_get", lambda: store.r.get(_config_key(store.key_prefix)))
    if not raw:
        return {}
    try:
        obj = json.loads(str(raw))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _load_overrides(store: "JobStore") -> dict[str, Any]:
    doc = _load_document(store)
    raw_values = doc.get("values") if isinstance(doc.get("values"), dict) else doc
    out: dict[str, Any] = {}
    if not isinstance(raw_values, dict):
        return out
    for key, raw in raw_values.items():
        spec = SPEC_BY_KEY.get(str(key))
        if spec is None:
            continue
        try:
            out[spec.key] = coerce_value(spec, raw)
        except Exception:
            continue
    return out


def get_runtime_values(store: "JobStore") -> dict[str, Any]:
    overrides = _load_overrides(store)
    values: dict[str, Any] = {}
    for spec in SPECS:
        values[spec.key] = overrides.get(spec.key, spec.default)
    return values


def get_runtime_config(store: "JobStore") -> dict[str, Any]:
    doc = _load_document(store)
    overrides = _load_overrides(store)
    values = get_runtime_values(store)
    items: list[dict[str, Any]] = []
    for spec in SPECS:
        value = values[spec.key]
        items.append(
            {
                "key": spec.key,
                "title": spec.title,
                "kind": spec.kind,
                "value": value,
                "default": spec.default,
                "is_default": spec.key not in overrides,
                "category": spec.category,
                "runtime_effect": spec.runtime_effect,
                "description": spec.description,
                "min_value": spec.min_value,
                "max_value": spec.max_value,
                "max_length": spec.max_length,
            }
        )
    return {
        "version": 1,
        "updated_at": float(doc.get("updated_at") or 0.0) if isinstance(doc, dict) else 0.0,
        "values": values,
        "items": items,
    }


def set_runtime_config(store: "JobStore", values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError("values must be an object")
    overrides = _load_overrides(store)
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"unknown runtime config key: {key}")
        if raw_value is None:
            overrides.pop(key, None)
            continue
        value = coerce_value(spec, raw_value)
        if value == spec.default:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    doc = {"version": 1, "updated_at": time.time(), "values": overrides}
    store._redis_call(
        "runtime_config_set",
        lambda: store.r.set(_config_key(store.key_prefix), json.dumps(doc, ensure_ascii=False, sort_keys=True)),
    )
    return get_runtime_config(store)


def build_llm_saturation(llm_workers: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(llm_workers, Mapping):
        return out
    for worker_type, row_raw in sorted(llm_workers.items()):
        row = row_raw if isinstance(row_raw, Mapping) else {}
        try:
            inflight = int(row.get("inflight", 0) or 0)
        except Exception:
            inflight = 0
        try:
            max_inflight = int(row.get("max_inflight", 0) or 0)
        except Exception:
            max_inflight = 0
        ratio = float(inflight) / float(max_inflight) if max_inflight > 0 else 0.0
        out[str(worker_type)] = {
            "enabled": bool(row.get("enabled", False)),
            "inflight": inflight,
            "max_inflight": max_inflight,
            "ratio": ratio,
        }
    return out


def build_capacity_policy_snapshot(
    *,
    values: Mapping[str, Any],
    job_status_counts: Mapping[str, Any],
    job_stage_counts: Mapping[str, Any],
    llm_saturation_by_worker_type: Mapping[str, Any],
) -> dict[str, Any]:
    def _int_value(key: str) -> int:
        try:
            return int(values.get(key, SPEC_BY_KEY[key].default))
        except Exception:
            return int(SPEC_BY_KEY[key].default)

    def _float_value(key: str) -> float:
        try:
            return float(values.get(key, SPEC_BY_KEY[key].default))
        except Exception:
            return float(SPEC_BY_KEY[key].default)

    try:
        build_backlog = int(job_status_counts.get("QUEUED", 0) or 0)
    except Exception:
        build_backlog = 0

    render_stage_names = {"dispatch", "render", "poll"}
    render_backlog = 0
    for stage, raw_count in job_stage_counts.items():
        if str(stage or "").strip() in render_stage_names:
            try:
                render_backlog += int(raw_count or 0)
            except Exception:
                continue

    max_llm_ratio = 0.0
    hot_worker = ""
    for worker_type, row_raw in llm_saturation_by_worker_type.items():
        row = row_raw if isinstance(row_raw, Mapping) else {}
        try:
            ratio = float(row.get("ratio", 0.0) or 0.0)
        except Exception:
            ratio = 0.0
        if ratio >= max_llm_ratio:
            max_llm_ratio = ratio
            hot_worker = str(worker_type)

    degraded_reasons: list[str] = []
    manual_reasons: list[str] = []
    actions: list[str] = []

    build_degraded = _int_value("backpressure.build_backlog_degraded")
    build_manual = _int_value("backpressure.build_backlog_maintenance_recommended")
    render_degraded = _int_value("backpressure.render_backlog_degraded")
    render_manual = _int_value("backpressure.render_backlog_add_windows_node")
    llm_degraded = _float_value("backpressure.llm_saturation_degraded_pct")
    llm_manual = _float_value("backpressure.llm_saturation_maintenance_pct")

    if build_backlog >= build_manual > 0:
        manual_reasons.append(f"build_backlog={build_backlog}>=threshold={build_manual}")
        actions.append("consider pausing traffic or enabling manual maintenance if backlog keeps growing")
    elif build_backlog >= build_degraded > 0:
        degraded_reasons.append(f"build_backlog={build_backlog}>=threshold={build_degraded}")
        actions.append("watch build workers; add/recreate build capacity if queue keeps growing")

    if render_backlog >= render_manual > 0:
        manual_reasons.append(f"render_backlog={render_backlog}>=threshold={render_manual}")
        actions.append("add a Windows render node for the spike window")
    elif render_backlog >= render_degraded > 0:
        degraded_reasons.append(f"render_backlog={render_backlog}>=threshold={render_degraded}")
        actions.append("watch render dispatch/poll split and Windows node health")

    if max_llm_ratio >= llm_manual > 0:
        manual_reasons.append(f"llm_saturation[{hot_worker}]={max_llm_ratio:.2f}>={llm_manual:.2f}")
        actions.append("increase LLM max_inflight only if provider latency/error rate is stable")
    elif max_llm_ratio >= llm_degraded > 0:
        degraded_reasons.append(f"llm_saturation[{hot_worker}]={max_llm_ratio:.2f}>={llm_degraded:.2f}")
        actions.append("do not hard reject; let queue-first admission absorb temporary LLM saturation")

    state = "normal"
    reasons = degraded_reasons
    if manual_reasons:
        state = "manual-maintenance-recommended"
        reasons = manual_reasons + degraded_reasons
    elif degraded_reasons:
        state = "degraded"

    return {
        "state": state,
        "reasons": reasons,
        "operator_actions": list(dict.fromkeys(actions)),
        "user_degraded_copy": str(values.get("backpressure.user_degraded_copy") or ""),
        "signals": {
            "build_backlog": build_backlog,
            "render_backlog": render_backlog,
            "max_llm_saturation": max_llm_ratio,
            "hottest_llm_worker_type": hot_worker,
        },
        "thresholds": {
            "build_backlog_degraded": build_degraded,
            "build_backlog_maintenance_recommended": build_manual,
            "render_backlog_degraded": render_degraded,
            "render_backlog_add_windows_node": render_manual,
            "llm_saturation_degraded_pct": llm_degraded,
            "llm_saturation_maintenance_pct": llm_manual,
        },
    }
