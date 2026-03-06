from __future__ import annotations

from mlcore.gemini_orchestrator import _looks_like_model_validation_error_text


def test_fragment_analytics_inconsistent_action_is_classified_for_stage_local_retry() -> None:
    msg = (
        "ValueError(\"fragment_analytics.chosen_action is inconsistent with relation_to_target "
        "(relation='inside_13_18' action='expand' expected='none')\")"
    )
    assert _looks_like_model_validation_error_text(msg) is True


def test_fragment_analytics_unsupported_relation_is_classified_for_stage_local_retry() -> None:
    msg = "ValueError(\"fragment_analytics.relation_to_target unsupported: 'unknown'\")"
    assert _looks_like_model_validation_error_text(msg) is True
