from __future__ import annotations

from services.tg_bot_public.admin_panel import _llm_workers_runtime_warnings


def test_llm_workers_warning_when_all_types_disabled() -> None:
    warnings = _llm_workers_runtime_warnings(
        {
            "sdk": {"enabled": False, "weight": 1, "max_inflight": 4},
            "openrouter": {"enabled": False, "weight": 1, "max_inflight": 4},
            "hybrid": {"enabled": False, "weight": 1, "max_inflight": 4},
        }
    )
    assert warnings and "no_enabled_types" in warnings[0]


def test_llm_workers_warning_when_zero_useful_capacity() -> None:
    warnings = _llm_workers_runtime_warnings(
        {
            "sdk": {"enabled": True, "weight": 0, "max_inflight": 4},
            "openrouter": {"enabled": True, "weight": 0, "max_inflight": 4},
            "hybrid": {"enabled": True, "weight": 0, "max_inflight": 4},
        }
    )
    assert warnings and "zero_useful_weight" in warnings[0]


def test_llm_workers_warning_empty_when_config_is_healthy() -> None:
    warnings = _llm_workers_runtime_warnings(
        {
            "sdk": {"enabled": True, "weight": 1, "max_inflight": 4},
            "openrouter": {"enabled": False, "weight": 1, "max_inflight": 4},
            "hybrid": {"enabled": False, "weight": 1, "max_inflight": 4},
        }
    )
    assert warnings == []

