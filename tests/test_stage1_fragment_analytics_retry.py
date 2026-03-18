from __future__ import annotations

import logging

import pytest

from mlcore.gemini_orchestrator import _validate_fragment_analytics_for_target
from mlcore.gemini_orchestrator import _looks_like_model_validation_error_text
from mlcore.gemini_orchestrator import _is_fragment_target_exact_mismatch
from mlcore.gemini_orchestrator import _build_stage1b_fragment_exact_retry_hint
from mlcore.gemini_orchestrator import _build_stage1a_precision_rework_hint
from mlcore.gemini_orchestrator import _analyze_stage1a_timecode_precision
from mlcore.gemini_orchestrator import _should_retry_stage1a_suspicious_precision
from mlcore.gemini_orchestrator import _build_stage1_plan_from_selected_fragment
from mlcore.gemini_orchestrator import _warn_stage1_clip_over_max
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload
from mlcore.models.stage1_plan import FragmentAnalytics


def test_fragment_analytics_inconsistent_action_is_not_classified_for_stage_local_retry() -> None:
    msg = (
        "ValueError(\"fragment_analytics.chosen_action is inconsistent with relation_to_target "
        "(relation='inside_13_18' action='expand' expected='none')\")"
    )
    assert _looks_like_model_validation_error_text(msg) is False


def test_fragment_analytics_unsupported_relation_is_not_classified_for_stage_local_retry() -> None:
    msg = "ValueError(\"fragment_analytics.relation_to_target unsupported: 'unknown'\")"
    assert _looks_like_model_validation_error_text(msg) is False


def test_fragment_analytics_noncanonical_action_is_warning_not_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    analytics = FragmentAnalytics.model_validate(
        {
            "target_fragment": "SHE IS NOT MY LOVER",
            "working_fragment": "SHE IS NOT MY LOVER",
            "working_start_abs": 2.5,
            "working_end_abs": 16.8,
            "working_start_text": "SHE",
            "working_end_text": "LOVER",
            "relation_to_target": "inside_13_18",
            "chosen_action": "expand",
            "rationale": "kept selected segment as final result",
        }
    )

    with caplog.at_level(logging.WARNING):
        start, end = _validate_fragment_analytics_for_target(
            target_fragment="SHE IS NOT MY LOVER",
            audio_start_abs=2.5,
            audio_end_abs=16.8,
            analytics=analytics,
            logger=logging.getLogger("tests.fragment_analytics"),
        )

    assert abs(float(start) - 2.5) <= 1e-9
    assert abs(float(end) - 16.8) <= 1e-9
    assert "stage1b_fragment_analytics_noncanonical_action" in caplog.text


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


def test_stage1_clip_over_max_is_warning_only(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        _warn_stage1_clip_over_max(
            clip_start_abs=10.0,
            clip_end_abs=42.4,
            logger=logging.getLogger("tests.fragment_analytics"),
            source="stage1a_selected_fragment",
        )
    assert "stage1_clip_duration_over_max" in caplog.text


def test_fragment_analytics_too_short_window_uses_audio_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    analytics = FragmentAnalytics.model_validate(
        {
            "target_fragment": "WHY LOVE",
            "working_fragment": "WHY LOVE",
            "working_start_abs": 64.494,
            "working_end_abs": 76.654,  # 12.16s (<13s)
            "working_start_text": "Почему",
            "working_end_text": "любовь",
            "relation_to_target": "wider",
            "chosen_action": "expand",
            "rationale": "expand around target words",
        }
    )

    with caplog.at_level(logging.WARNING):
        start, end = _validate_fragment_analytics_for_target(
            target_fragment="WHY LOVE",
            audio_start_abs=64.044,
            audio_end_abs=77.104,  # 13.06s valid
            analytics=analytics,
            logger=logging.getLogger("tests.fragment_analytics"),
        )

    assert abs(float(start) - 64.044) <= 1e-9
    assert abs(float(end) - 77.104) <= 1e-9
    assert "stage1b_fragment_analytics_window_too_short" in caplog.text


def test_fragment_analytics_window_mismatch_uses_union(
    caplog: pytest.LogCaptureFixture,
) -> None:
    analytics = FragmentAnalytics.model_validate(
        {
            "target_fragment": "SHE IS NOT MY LOVER",
            "working_fragment": "SHE IS NOT MY LOVER",
            "working_start_abs": 1.0,
            "working_end_abs": 18.0,
            "working_start_text": "SHE",
            "working_end_text": "LOVER",
            "relation_to_target": "wider",
            "chosen_action": "expand",
            "rationale": "wider context",
        }
    )

    with caplog.at_level(logging.WARNING):
        start, end = _validate_fragment_analytics_for_target(
            target_fragment="SHE IS NOT MY LOVER",
            audio_start_abs=2.5,
            audio_end_abs=16.8,
            analytics=analytics,
            logger=logging.getLogger("tests.fragment_analytics"),
        )

    assert abs(float(start) - 1.0) <= 1e-9
    assert abs(float(end) - 18.0) <= 1e-9
    assert "stage1b_fragment_window_mismatch" in caplog.text


def test_stage1a_selected_fragment_clip_is_clamped_to_content_when_oversized() -> None:
    # Simulate mismatch seen in production:
    # selected clip says 42.881..111.081 (~68s) while selected words end near 71.081 (~28.2s content).
    selected_fragment = {
        "audio": {
            "clip_start_abs": 42.881,
            "clip_end_abs": 111.081,
            "moment_of_interest_sec": None,
        },
        "transcript_words": [
            {"text": "солдат", "t_start": 42.881, "t_end": 43.281},
            {"text": "груди", "t_start": 70.481, "t_end": 71.081},
        ],
        "pause_spans": [],
        "srt_items": [],
        "fragment_analytics": {
            "target_fragment": "солдат ... груди",
            "working_fragment": "солдат ... груди",
            "working_start_abs": 42.881,
            "working_end_abs": 111.081,
            "working_start_text": "солдат",
            "working_end_text": "груди",
            "relation_to_target": "inside_13_30",
            "chosen_action": "none",
            "rationale": "kept",
        },
    }
    stage1_asr = Stage1AsrPayload.model_validate(
        {
            "transcript_words": list(selected_fragment["transcript_words"]),
            "pause_spans": [],
            "srt_items": [],
            "selected_fragment": selected_fragment,
        }
    )
    selected = stage1_asr.selected_fragment
    assert selected is not None

    plan = _build_stage1_plan_from_selected_fragment(
        stage1_asr=stage1_asr,
        selected=selected,
        target_fragment="солдат ... груди",
        logger=logging.getLogger("tests.stage1_clip_clamp"),
    )

    # Clip is clamped to actual selected content range, not oversized analytics window.
    assert abs(float(plan.audio.clip_start_abs) - 42.881) <= 1e-9
    assert abs(float(plan.audio.clip_end_abs) - 71.081) <= 1e-9


def _fmt_mmss_mmm(total_ms: int) -> str:
    mins = int(total_ms // 60000)
    rem = int(total_ms % 60000)
    secs = int(rem // 1000)
    millis = int(rem % 1000)
    return f"{mins:02d}:{secs:02d}.{millis:03d}"


def test_stage1a_precision_detector_flags_coarse_grid() -> None:
    aligned_words = []
    start_ms = 10_000
    for i in range(60):
        end_ms = start_ms + 250
        aligned_words.append(
            {
                "text": f"w{i}",
                "t_start": _fmt_mmss_mmm(start_ms),
                "t_end": _fmt_mmss_mmm(end_ms),
            }
        )
        start_ms = end_ms + 50

    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": aligned_words,
            "pause_spans": [],
        }
    )
    diag = _analyze_stage1a_timecode_precision(payload=payload)
    assert diag["suspicious"] is True
    assert "coarse_50ms_grid" in list(diag["reasons"])
    assert _should_retry_stage1a_suspicious_precision(
        reference_words_count=60,
        precision_diag=diag,
    ) is True

    hint = _build_stage1a_precision_rework_hint(
        precision_diag=diag,
        transcribe_attempt_1=payload.model_dump(mode="json"),
        target_fragment="you and me",
    )
    assert "TIMECODE_PRECISION_REWORK=ON" in hint
    assert "НЕ ЛЕНИСЬ" in hint
    assert "TARGET_FRAGMENT_EXAMPLE" in hint


def test_stage1a_precision_detector_accepts_fine_grained_timings() -> None:
    aligned_words = []
    start_ms = 8_000
    dur_seq = [173, 221, 264, 187, 209]
    gap_seq = [63, 47, 89, 31, 77]
    for i in range(45):
        dur_ms = dur_seq[i % len(dur_seq)]
        gap_ms = gap_seq[i % len(gap_seq)]
        end_ms = start_ms + dur_ms
        aligned_words.append(
            {
                "text": f"w{i}",
                "t_start": _fmt_mmss_mmm(start_ms),
                "t_end": _fmt_mmss_mmm(end_ms),
            }
        )
        start_ms = end_ms + gap_ms

    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": aligned_words,
            "pause_spans": [],
        }
    )
    diag = _analyze_stage1a_timecode_precision(payload=payload)
    assert diag["suspicious"] is False
    assert _should_retry_stage1a_suspicious_precision(
        reference_words_count=45,
        precision_diag=diag,
    ) is False
