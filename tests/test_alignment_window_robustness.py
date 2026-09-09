"""Regression cover for the two failure modes behind
``ALIGNMENT_WINDOW_MISMATCH`` in production:

* a reference text that physically cannot fit the requested window, which used
  to burn a full separation + inference pass before failing opaquely;
* a boundary syllable spilling a few frames outside a hand-picked window, which
  used to reject every candidate and cost the user the render.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from mlcore.alignment.api import AlignmentMetrics, rejection_profile
from mlcore.alignment.contracts import (
    ERROR_TEXT_TOO_LONG_FOR_WINDOW,
    ERROR_WINDOW_MISMATCH,
)
from mlcore.alignment.core import (
    AlignedWord,
    AlignmentFailure,
    DynamicWindowConfig,
    EmissionTimeline,
    _build_stage1_asr,
    check_reference_fits_window,
    clamp_clip_end_to_decoded_audio,
    minimum_ctc_frames,
    required_alignment_seconds,
    select_dynamic_alignment_window,
)


def test_requested_end_is_clamped_to_decoded_audio_end(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        actual = clamp_clip_end_to_decoded_audio(
            clip_start_abs=0.0,
            clip_end_abs=18.0,
            audio_end_abs=17.319125,
        )

    assert actual == pytest.approx(17.319125)
    assert "requested_clip_end=18.000000 audio_end=17.319125" in caplog.text


def test_window_entirely_after_decoded_audio_still_fails() -> None:
    with pytest.raises(AlignmentFailure) as excinfo:
        clamp_clip_end_to_decoded_audio(
            clip_start_abs=18.0,
            clip_end_abs=20.0,
            audio_end_abs=17.319125,
        )

    assert excinfo.value.code == ERROR_WINDOW_MISMATCH


def _timeline() -> EmissionTimeline:
    return EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )


def _emissions() -> np.ndarray:
    """Eight tokens sung back to back over frames 20..27, i.e. 2.0s..2.8s.

    Two words of four tokens each, so the pair needs 0.8s of audio no matter
    where the search window is placed.
    """
    probabilities = np.full((61, 3), 0.03, dtype=np.float64)
    probabilities[:, 0] = 0.94
    for offset, frame in enumerate(range(20, 28)):
        token = 1 if offset % 2 == 0 else 2
        row = [0.05, 0.05, 0.05]
        row[token] = 0.90
        probabilities[frame] = row
    return np.log(probabilities)


TARGET_IDS = [1, 2, 1, 2, 1, 2, 1, 2]
TOKEN_WORD_INDEXES = [0, 0, 0, 0, 1, 1, 1, 1]


def _config(*, boundary_overflow_tolerance_sec: float) -> DynamicWindowConfig:
    return DynamicWindowConfig(
        max_adjust_sec=2.0,
        step_sec=0.5,
        min_edge_clearance_sec=0.15,
        stability_tolerance_sec=0.11,
        min_consensus_candidates=3,
        score_tolerance=0.12,
        min_boundary_duration_ratio=0.15,
        boundary_overflow_tolerance_sec=boundary_overflow_tolerance_sec,
    )


def _select(*, clip_start_abs: float, clip_end_abs: float, tolerance: float):
    return select_dynamic_alignment_window(
        log_probs=_emissions(),
        target_ids=TARGET_IDS,
        token_word_indexes=TOKEN_WORD_INDEXES,
        display_words=["разде", "двасе"],
        normalized_words=["разде", "двасе"],
        blank_id=0,
        timeline=_timeline(),
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        config=_config(boundary_overflow_tolerance_sec=tolerance),
        min_word_confidence=0.5,
    )


# --- text density preflight -------------------------------------------------


def test_minimum_ctc_frames_counts_a_blank_between_repeats() -> None:
    assert minimum_ctc_frames([1, 2, 3]) == 3
    assert minimum_ctc_frames([1, 1, 2]) == 4
    assert minimum_ctc_frames([1, 1, 1]) == 5


def test_required_alignment_seconds_scales_with_frame_rate() -> None:
    assert required_alignment_seconds([1, 2, 3], seconds_per_frame=0.02) == (
        pytest.approx(0.06)
    )


def test_reference_that_fits_the_window_passes_preflight() -> None:
    check_reference_fits_window(
        target_ids=list(range(1, 101)),
        clip_start_abs=10.0,
        clip_end_abs=25.0,
        seconds_per_frame=0.02,
        max_frame_budget_ratio=0.8,
        word_count=20,
    )


def test_reference_longer_than_the_window_fails_with_actionable_numbers() -> None:
    # 900 tokens * 20 ms = 18 s of speech crammed into a 15 s window.
    with pytest.raises(AlignmentFailure) as excinfo:
        check_reference_fits_window(
            target_ids=list(range(1, 901)),
            clip_start_abs=30.0,
            clip_end_abs=45.0,
            seconds_per_frame=0.02,
            max_frame_budget_ratio=0.8,
            word_count=150,
        )

    failure = excinfo.value
    assert failure.code == ERROR_TEXT_TOO_LONG_FOR_WINDOW
    assert failure.details["window_sec"] == pytest.approx(15.0)
    assert failure.details["required_sec"] == pytest.approx(18.0)
    assert failure.details["min_window_sec"] == pytest.approx(22.5)
    assert failure.details["word_count"] == 150


def test_zero_budget_ratio_disables_the_preflight() -> None:
    check_reference_fits_window(
        target_ids=list(range(1, 5001)),
        clip_start_abs=0.0,
        clip_end_abs=1.0,
        seconds_per_frame=0.02,
        max_frame_budget_ratio=0.0,
        word_count=900,
    )


# --- bounded boundary overflow ----------------------------------------------


def test_strict_window_still_wins_when_the_words_fit_inside() -> None:
    selection = _select(clip_start_abs=2.0, clip_end_abs=3.0, tolerance=0.2)

    assert selection.diagnostics["boundary_overflow_applied"] is False
    assert selection.diagnostics["selected_left_window_overflow_sec"] == 0.0
    assert selection.diagnostics["selected_right_window_overflow_sec"] == 0.0
    assert "boundary_window_overflow_tolerated" not in (
        selection.diagnostics["boundary_evidence_warnings"]
    )


def test_boundary_word_spilling_within_tolerance_is_accepted() -> None:
    # The user picked 2.0..2.6, two frames short of the 0.8s the two words
    # actually take. Nothing can sit strictly inside, but the honest placement
    # only hangs 0.2s past the edge.
    selection = _select(clip_start_abs=2.0, clip_end_abs=2.6, tolerance=0.2)

    assert selection.diagnostics["boundary_overflow_applied"] is True
    assert "boundary_window_overflow_tolerated" in (
        selection.diagnostics["boundary_evidence_warnings"]
    )
    assert selection.selected.window_overflow_sec == pytest.approx(0.2)
    assert selection.diagnostics["required_sec"] == pytest.approx(0.8)
    assert selection.diagnostics["window_sec"] == pytest.approx(0.6)


def test_zero_tolerance_reproduces_strict_containment() -> None:
    with pytest.raises(AlignmentFailure) as excinfo:
        _select(clip_start_abs=2.0, clip_end_abs=2.6, tolerance=0.0)

    failure = excinfo.value
    assert failure.code == ERROR_WINDOW_MISMATCH
    assert failure.details["rejection_counts"]["outside_user_window"] > 0
    assert failure.details["overflow_tolerant_candidate_count"] == 0


def test_overflow_beyond_tolerance_stays_a_hard_rejection() -> None:
    # Half the fragment lies outside a 0.2s window: a genuine mismatch, not a
    # boundary syllable.
    with pytest.raises(AlignmentFailure) as excinfo:
        _select(clip_start_abs=2.0, clip_end_abs=2.2, tolerance=0.2)

    assert excinfo.value.code == ERROR_WINDOW_MISMATCH
    assert excinfo.value.details["rejection_counts"]["outside_user_window"] > 0


# --- failure diagnostics ----------------------------------------------------


def test_failure_details_report_required_and_overflow_seconds() -> None:
    with pytest.raises(AlignmentFailure) as excinfo:
        _select(clip_start_abs=2.0, clip_end_abs=2.2, tolerance=0.2)

    details = excinfo.value.details
    assert details["window_sec"] == pytest.approx(0.2)
    assert details["required_sec"] == pytest.approx(0.8)
    assert details["required_to_window_ratio"] == pytest.approx(4.0)
    assert details["target_token_count"] == 8
    assert details["boundary_overflow_tolerance_sec"] == pytest.approx(0.2)
    assert details["min_window_overflow_sec"] > 0.2
    assert "window_sec=" in str(excinfo.value)
    assert "required_sec=" in str(excinfo.value)


def test_failed_candidates_carry_the_reason_they_could_not_be_built() -> None:
    with pytest.raises(AlignmentFailure) as excinfo:
        _select(clip_start_abs=2.0, clip_end_abs=2.2, tolerance=0.2)

    failed = excinfo.value.details["failed_candidates"]
    assert failed, "expected probes too short to hold the transcript"
    assert all("message" in item for item in failed)
    assert any("too short for transcript" in item["message"] for item in failed)


# --- downstream window propagation ------------------------------------------


def test_stage1_fragment_widens_to_contain_a_tolerated_boundary_word() -> None:
    words = [
        AlignedWord("раз", "раз", 2.1, 2.4, 0.0, 0.3, 0.9),
        AlignedWord("два", "два", 3.5, 3.9, 1.4, 1.8, 0.9),
    ]

    payload = _build_stage1_asr(
        words=words,
        target_fragment="раз два",
        clip_start_abs=2.2,
        clip_end_abs=3.8,
        pause_min_gap_sec=0.35,
    )

    fragment = payload.selected_fragment
    assert fragment is not None
    assert fragment.audio.clip_start_abs == pytest.approx(2.1)
    assert fragment.audio.clip_end_abs == pytest.approx(3.9)
    analytics = fragment.fragment_analytics
    assert analytics is not None
    assert analytics.working_start_abs == pytest.approx(2.1)
    assert analytics.working_end_abs == pytest.approx(3.9)


def test_stage1_fragment_keeps_the_user_window_when_words_fit() -> None:
    words = [
        AlignedWord("раз", "раз", 2.3, 2.5, 0.1, 0.3, 0.9),
        AlignedWord("два", "два", 3.4, 3.6, 1.2, 1.4, 0.9),
    ]

    payload = _build_stage1_asr(
        words=words,
        target_fragment="раз два",
        clip_start_abs=2.2,
        clip_end_abs=3.8,
        pause_min_gap_sec=0.35,
    )

    fragment = payload.selected_fragment
    assert fragment is not None
    assert fragment.audio.clip_start_abs == pytest.approx(2.2)
    assert fragment.audio.clip_end_abs == pytest.approx(3.8)


def _stage1_asr_with_clip(*, clip: tuple[float, float], words: list[AlignedWord]):
    return _build_stage1_asr(
        words=words,
        target_fragment="раз два",
        clip_start_abs=clip[0],
        clip_end_abs=clip[1],
        pause_min_gap_sec=0.35,
    )


def test_orchestrator_adopts_the_widened_window() -> None:
    from mlcore.gemini_orchestrator import _adopt_alignment_clip_window

    payload = _stage1_asr_with_clip(
        clip=(2.2, 3.8),
        words=[
            AlignedWord("раз", "раз", 2.1, 2.4, 0.0, 0.3, 0.9),
            AlignedWord("два", "два", 3.5, 3.9, 1.4, 1.8, 0.9),
        ],
    )

    adopted = _adopt_alignment_clip_window(
        stage1_asr=payload,
        user_clip_window=(2.2, 3.8),
        logger=logging.getLogger("test"),
    )

    assert adopted == pytest.approx((2.1, 3.9))


def test_orchestrator_never_narrows_the_user_window() -> None:
    from mlcore.gemini_orchestrator import _adopt_alignment_clip_window

    payload = _stage1_asr_with_clip(
        clip=(2.2, 3.8),
        words=[
            AlignedWord("раз", "раз", 2.3, 2.5, 0.1, 0.3, 0.9),
            AlignedWord("два", "два", 3.4, 3.6, 1.2, 1.4, 0.9),
        ],
    )

    adopted = _adopt_alignment_clip_window(
        stage1_asr=payload,
        user_clip_window=(2.2, 3.8),
        logger=logging.getLogger("test"),
    )

    assert adopted == pytest.approx((2.2, 3.8))


def test_orchestrator_keeps_the_window_without_a_fragment() -> None:
    from mlcore.gemini_orchestrator import _adopt_alignment_clip_window
    from mlcore.models.stage1_asr import Stage1AsrPayload

    payload = Stage1AsrPayload.model_validate(
        {
            "transcript_words": [{"text": "раз", "t_start": 2.3, "t_end": 2.5}],
            "pause_spans": [],
            "srt_items": [],
        }
    )
    assert payload.selected_fragment is None

    assert _adopt_alignment_clip_window(
        stage1_asr=payload,
        user_clip_window=(2.2, 3.8),
        logger=logging.getLogger("test"),
    ) == (2.2, 3.8)


# --- metrics ----------------------------------------------------------------


def test_rejection_profile_is_a_stable_sorted_reason_set() -> None:
    assert rejection_profile(None) == "none"
    assert rejection_profile({"rejection_counts": {}}) == "none"
    assert (
        rejection_profile(
            {
                "rejection_counts": {
                    "outside_user_window": 7,
                    "insufficient_edge_clearance": 7,
                }
            }
        )
        == "insufficient_edge_clearance|outside_user_window"
    )
    # Counts must not leak into the label: the same profile with different
    # counts has to aggregate into one series.
    assert rejection_profile({"rejection_counts": {"outside_user_window": 1}}) == (
        rejection_profile({"rejection_counts": {"outside_user_window": 99}})
    )


def test_metrics_render_prometheus_counters() -> None:
    metrics = AlignmentMetrics()
    metrics.record(outcome="success", code="", profile="clean")
    metrics.record(outcome="success", code="", profile="clean")
    metrics.record(
        outcome="failure",
        code=ERROR_WINDOW_MISMATCH,
        profile="outside_user_window",
    )

    body = metrics.render()
    assert "# TYPE blast_alignment_requests_total counter" in body
    assert (
        'blast_alignment_requests_total{outcome="success",error_code="",'
        'rejection_profile="clean"} 2' in body
    )
    assert (
        "blast_alignment_requests_total{outcome=\"failure\","
        f'error_code="{ERROR_WINDOW_MISMATCH}",'
        'rejection_profile="outside_user_window"} 1' in body
    )
