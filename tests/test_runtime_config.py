from __future__ import annotations

from services.orchestrator.runtime_config import (
    build_capacity_policy_snapshot,
    build_llm_saturation,
    get_runtime_config,
    get_runtime_values,
    set_runtime_config,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True


class _FakeStore:
    def __init__(self) -> None:
        self.key_prefix = "blast_test"
        self.r = _FakeRedis()

    def _redis_call(self, _op: str, fn):
        return fn()


def test_runtime_config_defaults_and_overrides() -> None:
    store = _FakeStore()
    cfg = get_runtime_config(store)
    assert cfg["values"]["gemini.transport_retry_enabled"] is True
    assert cfg["values"]["backpressure.render_backlog_add_windows_node"] == 300

    updated = set_runtime_config(
        store,
        {
            "gemini.transport_retry_enabled": "0",
            "backpressure.render_backlog_add_windows_node": "42",
        },
    )
    assert updated["values"]["gemini.transport_retry_enabled"] is False
    assert updated["values"]["backpressure.render_backlog_add_windows_node"] == 42
    assert get_runtime_values(store)["backpressure.render_backlog_add_windows_node"] == 42


def test_runtime_config_rejects_unknown_key() -> None:
    store = _FakeStore()
    try:
        set_runtime_config(store, {"SECRET_TOKEN": "oops"})
    except ValueError as exc:
        assert "unknown runtime config key" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_capacity_policy_marks_render_manual_action() -> None:
    values = get_runtime_values(_FakeStore())
    saturation = build_llm_saturation(
        {
            "vertex_sdk_mix": {
                "enabled": True,
                "inflight": 10,
                "max_inflight": 100,
            }
        }
    )
    policy = build_capacity_policy_snapshot(
        values=values,
        job_status_counts={"QUEUED": 0},
        job_stage_counts={"render": 301, "poll": 2},
        llm_saturation_by_worker_type=saturation,
    )
    assert policy["state"] == "manual-maintenance-recommended"
    assert any("Windows render node" in action for action in policy["operator_actions"])
