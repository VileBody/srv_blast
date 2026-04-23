from __future__ import annotations

from typing import Any, Mapping


BACKPRESSURE_STATE_NORMAL = "normal"
BACKPRESSURE_STATE_DEGRADED = "degraded"
BACKPRESSURE_STATE_MANUAL_MAINTENANCE_RECOMMENDED = "manual_maintenance_recommended"


def _int_value(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def compute_capacity_policy(
    *,
    render_backlog: int,
    build_backlog: int,
    llm_saturation_by_worker_type: Mapping[str, Mapping[str, Any]] | None,
    render_backlog_degraded_threshold: int,
    render_backlog_scaleout_threshold: int,
    build_backlog_degraded_threshold: int,
    build_backlog_manual_maintenance_threshold: int,
) -> dict[str, Any]:
    render_backlog = max(0, _int_value(render_backlog))
    build_backlog = max(0, _int_value(build_backlog))

    eligible_worker_types: list[str] = []
    saturated_worker_types: list[str] = []
    if isinstance(llm_saturation_by_worker_type, Mapping):
        for worker_type, row_raw in sorted(llm_saturation_by_worker_type.items()):
            row = row_raw if isinstance(row_raw, Mapping) else {}
            enabled = bool(row.get("enabled"))
            weight = _int_value(row.get("weight"), 0)
            max_inflight = _int_value(row.get("max_inflight"), 0)
            saturated = bool(row.get("saturated"))
            if enabled and weight > 0 and max_inflight > 0:
                eligible_worker_types.append(str(worker_type))
                if saturated:
                    saturated_worker_types.append(str(worker_type))

    llm_all_workers_saturated = bool(eligible_worker_types) and (
        len(saturated_worker_types) >= len(eligible_worker_types)
    )
    llm_any_worker_saturated = bool(saturated_worker_types)

    degraded_reasons: list[str] = []
    severe_reasons: list[str] = []

    if render_backlog >= int(render_backlog_degraded_threshold):
        degraded_reasons.append("render_backlog_high")
    if render_backlog >= int(render_backlog_scaleout_threshold):
        severe_reasons.append("render_backlog_scaleout")

    if build_backlog >= int(build_backlog_degraded_threshold):
        degraded_reasons.append("build_backlog_high")
    if build_backlog >= int(build_backlog_manual_maintenance_threshold):
        severe_reasons.append("build_backlog_manual_maintenance")

    if llm_any_worker_saturated:
        degraded_reasons.append("llm_saturation")
    if llm_all_workers_saturated:
        severe_reasons.append("llm_all_workers_saturated")

    if severe_reasons:
        state = BACKPRESSURE_STATE_MANUAL_MAINTENANCE_RECOMMENDED
    elif degraded_reasons:
        state = BACKPRESSURE_STATE_DEGRADED
    else:
        state = BACKPRESSURE_STATE_NORMAL

    operator_actions: list[str] = []
    if "render_backlog_scaleout" in severe_reasons:
        operator_actions.append(
            f"Render backlog >= {int(render_backlog_scaleout_threshold)}: manually add the 3rd Windows node now."
        )
    elif "render_backlog_high" in degraded_reasons:
        operator_actions.append(
            f"Render backlog is elevated: watch queue and prepare to add the 3rd Windows node at {int(render_backlog_scaleout_threshold)}."
        )

    if "build_backlog_manual_maintenance" in severe_reasons:
        operator_actions.append(
            f"Build backlog >= {int(build_backlog_manual_maintenance_threshold)}: consider controlled maintenance if delay keeps growing."
        )
    elif "build_backlog_high" in degraded_reasons:
        operator_actions.append(
            f"Build backlog is elevated: keep intake open, but watch ETA and saturation above {int(build_backlog_degraded_threshold)}."
        )

    if "llm_all_workers_saturated" in severe_reasons:
        operator_actions.append(
            "All enabled LLM worker types are saturated: keep queue-first intake, but prepare controlled maintenance if this persists."
        )
    elif "llm_saturation" in degraded_reasons:
        operator_actions.append(
            "Some LLM worker types are saturated: queue-first intake stays on, but accepted jobs can wait longer before build starts."
        )

    operator_action = " ".join(operator_actions).strip() or "No operator action required."

    if state == BACKPRESSURE_STATE_MANUAL_MAINTENANCE_RECOMMENDED:
        user_message = (
            "Сейчас высокая нагрузка: заявку мы приняли в очередь, но старт и финальная выдача могут идти заметно дольше обычного."
        )
    elif state == BACKPRESSURE_STATE_DEGRADED:
        user_message = "Сейчас есть очередь, поэтому обработка может идти медленнее обычного, но заявка уже принята."
    else:
        user_message = ""

    return {
        "state": state,
        "reason_codes": severe_reasons or degraded_reasons,
        "operator_action": operator_action,
        "user_message": user_message,
        "render_backlog_degraded_threshold": int(render_backlog_degraded_threshold),
        "render_backlog_add_windows_node_threshold": int(render_backlog_scaleout_threshold),
        "build_backlog_degraded_threshold": int(build_backlog_degraded_threshold),
        "build_backlog_manual_maintenance_threshold": int(build_backlog_manual_maintenance_threshold),
        "render_node_action": "add_3rd_windows_node_manually",
        "backpressure_policy": "queue_first_accept_then_capacity_inside_worker",
        "accepted_job_mode": "queued_with_overload_notice",
        "llm_saturated_worker_types": saturated_worker_types,
        "llm_all_workers_saturated": llm_all_workers_saturated,
    }
