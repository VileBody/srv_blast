"""Whether a collection can serve a job is knowable at ingest — so it is reported there.

Two picker failures this predicts, both of which otherwise reach a paying user as
a failed render:

    no asset can cover interval for strict no-repeat policy: ... dur=3.412
    insufficient unique assets for strict no-repeat policy: need=38 have=12
"""
from __future__ import annotations

import pytest

from mlcore.footage_collection_readiness import (
    MAX_INTERVAL_SEC,
    MIN_GAP_SEC,
    clips_needed_for_window,
    evaluate_collection,
    evaluate_index,
)


def test_demand_is_derived_from_the_timing_profile_not_invented() -> None:
    from mlcore.switch_timing_deterministic import SwitchTimingParams

    p = SwitchTimingParams()
    assert MAX_INTERVAL_SEC == p.max_hold_sec
    assert MIN_GAP_SEC == p.default_gap_floor_sec


@pytest.mark.parametrize(
    "window, expected_at_least",
    [(15, 9), (30, 18), (60, 37)],
)
def test_longer_reels_need_more_unique_clips(window: int, expected_at_least: int) -> None:
    # Every interval takes its own clip — the no-repeat policy is strict.
    assert clips_needed_for_window(window) >= expected_at_least


def test_clips_shorter_than_the_longest_interval_make_a_collection_unusable() -> None:
    # The real risk for a pre-cut film batch: plenty of clips, all too short.
    r = evaluate_collection("films__x", [3.0] * 78)
    assert r.clips == 78
    assert r.status == "unusable"
    assert "3.5" in r.note


def test_a_batch_that_clears_the_longest_interval_is_ok() -> None:
    r = evaluate_collection("films__x", [4.0] * 78)
    assert r.status == "ok"
    assert r.clips_covering_longest_interval == 78
    assert r.note == ""


def test_a_mixed_collection_needs_only_some_long_clips() -> None:
    # Short clips still fill short intervals; only the longest ones are at risk.
    r = evaluate_collection("films__x", [2.0] * 70 + [5.0] * 8)
    assert r.can_fill_longest_interval
    assert r.clips_covering_longest_interval == 8


def test_too_few_clips_is_flagged_as_thin() -> None:
    r = evaluate_collection("films__x", [6.0] * 12)
    assert r.status == "thin"
    assert "12 clips" in r.note


def test_unusable_outranks_thin_because_it_fails_every_job() -> None:
    r = evaluate_collection("films__x", [1.0] * 3)
    assert r.status == "unusable"


def test_zero_and_junk_durations_are_ignored_not_counted() -> None:
    # An unindexed clip has duration 0 and cannot be picked at all.
    r = evaluate_collection("films__x", [0.0, None, "oops", 4.0, 4.0])
    assert r.clips == 2


def test_index_evaluation_splits_by_folder() -> None:
    assets = (
        [{"genre": "films", "tag": "брат", "duration_sec": 4.0}] * 40
        + [{"genre": "films", "tag": "бумер", "duration_sec": 2.0}] * 40
        + [{"genre": "films", "tag": "", "duration_sec": 4.0}]  # unfiled, skipped
    )
    rows = {r.slug: r for r in evaluate_index(assets)}
    assert set(rows) == {"films__брат", "films__бумер"}
    assert rows["films__брат"].status == "ok"
    assert rows["films__бумер"].status == "unusable"


def test_report_shape_is_json_serialisable() -> None:
    import json

    row = evaluate_collection("films__x", [4.0] * 50)
    json.dumps(row.as_dict())
