from __future__ import annotations

import pytest

from mlcore.models.switch_timing import (
    RawTimingBuckets,
    Stage2TimingAnalysisPayload,
    Stage2TimingCutsPayload,
    SwitchTimingPayload,
    normalize_switch_points,
)


def test_switch_timing_payload_validates_internal_points() -> None:
    payload = SwitchTimingPayload.model_validate(
        {
            "clip_start_abs": 10.0,
            "clip_end_abs": 20.0,
            "fast_start_seconds": 6.0,
            "bpm": 120.0,
            "switch_points_abs": [11.0, 12.5, 15.0],
        }
    )
    assert payload.switch_points_abs == [11.0, 12.5, 15.0]


def test_switch_timing_payload_rejects_unsorted_points() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        SwitchTimingPayload.model_validate(
            {
                "clip_start_abs": 0.0,
                "clip_end_abs": 10.0,
                "fast_start_seconds": 6.0,
                "bpm": 120.0,
                "switch_points_abs": [2.0, 1.0],
            }
        )


def test_switch_timing_payload_allows_missing_bpm_for_gemini_only_mode() -> None:
    payload = SwitchTimingPayload.model_validate(
        {
            "clip_start_abs": 0.0,
            "clip_end_abs": 10.0,
            "fast_start_seconds": 6.0,
            "switch_points_abs": [1.0, 2.0, 3.0],
        }
    )
    assert payload.bpm is None


def test_stage2_timing_cuts_payload_rejects_empty_cuts() -> None:
    with pytest.raises(ValueError, match="must contain at least one cut point"):
        Stage2TimingCutsPayload.model_validate(
            {
                "applied_rule": "Dynamic Contrast",
                "final_cut_timings": [],
            }
        )


def test_raw_timing_buckets_sort_unsorted_input() -> None:
    # Raw detector buckets are order-free candidate sets; the LLM concatenates
    # them from multiple sources and routinely emits non-monotonic order. These
    # are the exact shapes that previously crashed stage2_timing_analysis (and
    # therefore the whole build) for entire batches.
    buckets = RawTimingBuckets.model_validate(
        {
            "kick_bass": [2.985, 4.1, 5.284, 8.372, 7.327],
            "semantic_peaks": [89.036, 75.568, 78.68],
        }
    )
    assert buckets.kick_bass == [2.985, 4.1, 5.284, 7.327, 8.372]
    assert buckets.semantic_peaks == [75.568, 78.68, 89.036]


def test_raw_timing_buckets_dedupe_near_duplicates() -> None:
    buckets = RawTimingBuckets.model_validate(
        {"snare_clap": [1.0, 1.0000001, 1.0000002, 2.0]}
    )
    assert buckets.snare_clap == [1.0, 2.0]


def test_raw_timing_buckets_still_reject_negative() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        RawTimingBuckets.model_validate({"vocal_phrases": [1.0, -0.5]})


def test_stage2_timing_analysis_payload_accepts_unsorted_raw_timings() -> None:
    payload = Stage2TimingAnalysisPayload.model_validate(
        {
            "selected_rule": "Dynamic Contrast",
            "reason": "drop-heavy track",
            "raw_timings": {
                "kick_bass": [67.464, 70.0, 89.036, 75.568, 78.68],
            },
        }
    )
    assert payload.raw_timings.kick_bass == [67.464, 70.0, 75.568, 78.68, 89.036]


def test_normalize_switch_points_merges_near_points() -> None:
    out = normalize_switch_points(
        raw_cut_timings=[0.5, 0.62, 2.0, 2.1, 4.0],
        clip_start_abs=0.0,
        clip_end_abs=6.0,
        merge_gap_sec=0.2,
        min_segment_sec=0.3,
    )
    assert out == [0.5, 2.0, 4.0]


def test_normalize_switch_points_rejects_too_short_segments() -> None:
    with pytest.raises(ValueError, match="min segment"):
        normalize_switch_points(
            raw_cut_timings=[0.1, 1.0, 2.0],
            clip_start_abs=0.0,
            clip_end_abs=3.0,
            merge_gap_sec=0.2,
            min_segment_sec=0.3,
        )


def test_normalize_switch_points_compacts_too_short_segments() -> None:
    out = normalize_switch_points(
        raw_cut_timings=[10.8, 11.086, 11.4, 12.0],
        clip_start_abs=10.8,
        clip_end_abs=14.0,
        merge_gap_sec=0.2,
        min_segment_sec=0.3,
        compact_short_segments=True,
    )
    assert out == [11.4, 12.0]


def test_normalize_switch_points_compaction_still_fails_if_all_points_dropped() -> None:
    with pytest.raises(ValueError, match="min segment"):
        normalize_switch_points(
            raw_cut_timings=[0.1],
            clip_start_abs=0.0,
            clip_end_abs=3.0,
            merge_gap_sec=0.2,
            min_segment_sec=0.3,
            compact_short_segments=True,
        )
