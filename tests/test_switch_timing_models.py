from __future__ import annotations

import pytest

from mlcore.models.switch_timing import SwitchTimingPayload, normalize_switch_points


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
