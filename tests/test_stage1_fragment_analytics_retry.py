from __future__ import annotations

import logging

import pytest

from mlcore.gemini_orchestrator import _validate_fragment_analytics_for_target
from mlcore.gemini_orchestrator import _looks_like_model_validation_error_text
from mlcore.gemini_orchestrator import _is_fragment_target_exact_mismatch
from mlcore.gemini_orchestrator import _build_stage1b_fragment_exact_retry_hint
from mlcore.models.stage1_plan import FragmentAnalytics


def test_fragment_analytics_inconsistent_action_is_classified_for_stage_local_retry() -> None:
    msg = (
        "ValueError(\"fragment_analytics.chosen_action is inconsistent with relation_to_target "
        "(relation='inside_13_18' action='expand' expected='none')\")"
    )
    assert _looks_like_model_validation_error_text(msg) is True


def test_fragment_analytics_unsupported_relation_is_classified_for_stage_local_retry() -> None:
    msg = "ValueError(\"fragment_analytics.relation_to_target unsupported: 'unknown'\")"
    assert _looks_like_model_validation_error_text(msg) is True


def test_fragment_analytics_target_exact_mismatch_is_warning_not_error(caplog: pytest.LogCaptureFixture) -> None:
    analytics = FragmentAnalytics.model_validate(
        {
            "target_fragment": "SHE IS NOT MY LOVER",
            "working_fragment": "SHE IS NOT MY LOVER",
            "working_start_abs": 2.5,
            "working_end_abs": 16.8,
            "working_start_text": "SHE",
            "working_end_text": "LOVER",
            "relation_to_target": "inside_13_18",
            "chosen_action": "none",
            "rationale": "window already fits",
        }
    )

    with caplog.at_level(logging.WARNING):
        start, end = _validate_fragment_analytics_for_target(
            target_fragment="SHE IS NOT MY LOVE",  # exact mismatch
            audio_start_abs=2.5,
            audio_end_abs=16.8,
            analytics=analytics,
            logger=logging.getLogger("tests.fragment_analytics"),
        )

    assert abs(float(start) - 2.5) <= 1e-9
    assert abs(float(end) - 16.8) <= 1e-9
    assert "stage1b_fragment_target_mismatch" in caplog.text


def test_fragment_target_exact_mismatch_helper_detects_difference() -> None:
    analytics = FragmentAnalytics.model_validate(
        {
            "target_fragment": "SHE IS NOT MY LOVER",
            "working_fragment": "SHE IS NOT MY LOVER",
            "working_start_abs": 2.5,
            "working_end_abs": 16.8,
            "working_start_text": "SHE",
            "working_end_text": "LOVER",
            "relation_to_target": "inside_13_18",
            "chosen_action": "none",
            "rationale": "window already fits",
        }
    )
    assert _is_fragment_target_exact_mismatch(
        target_fragment="SHE IS NOT MY LOVE",
        analytics=analytics,
    ) is True


def test_fragment_target_retry_hint_contains_expected_and_previous_values() -> None:
    hint = _build_stage1b_fragment_exact_retry_hint(
        target_fragment="SHE IS NOT MY LOVE",
        got_fragment="SHE IS NOT MY LOVER",
    )
    assert "TARGET_FRAGMENT_TEXT_CORRECTION=ON" in hint
    assert "EXPECTED_USER_TARGET_FRAGMENT" in hint
    assert "PREVIOUS_FRAGMENT_ANALYTICS_TARGET" in hint
    assert "SHE IS NOT MY LOVE" in hint
    assert "SHE IS NOT MY LOVER" in hint
