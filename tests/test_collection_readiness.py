"""Whether a collection can serve a job is knowable at ingest — so it is reported there.

Two picker failures this predicts, both of which otherwise reach a paying user as
a failed render:

    no asset can cover interval for strict no-repeat policy: ... dur=3.412
    insufficient unique assets for strict no-repeat policy: need=38 have=12
"""
from __future__ import annotations

import pytest

from mlcore.footage_collection_readiness import (
    HARD_FLOOR_SEC,
    MAX_INTERVAL_SEC,
    MIN_GAP_SEC,
    clips_needed_for_window,
    evaluate_collection,
    evaluate_index,
    interval_cap_for,
)


def test_demand_is_derived_from_the_timing_profile_not_invented() -> None:
    from mlcore.switch_timing_deterministic import SwitchTimingParams

    p = SwitchTimingParams()
    assert MAX_INTERVAL_SEC == p.max_hold_sec
    assert MIN_GAP_SEC == p.default_gap_floor_sec
    assert HARD_FLOOR_SEC == p.hard_floor_sec


@pytest.mark.parametrize(
    "window, expected_at_least",
    [(15, 9), (30, 18), (60, 37)],
)
def test_longer_reels_need_more_unique_clips(window: int, expected_at_least: int) -> None:
    # Every interval takes its own clip — the no-repeat policy is strict.
    assert clips_needed_for_window(window) >= expected_at_least


def test_the_delivered_three_second_batch_is_serviceable() -> None:
    # The real batch. Jobs lower max_hold to the pool's ceiling instead of asking
    # for a clip that does not exist, so 3s clips are fine — they just cut sooner.
    r = evaluate_collection("films__x", [3.0] * 78)
    assert r.status == "ok"
    assert r.interval_cap_sec == pytest.approx(2.95)
    assert r.note == ""


def test_a_cap_looser_than_the_gap_does_not_inflate_demand() -> None:
    # Cuts already land ~1.6s apart; a 2.95s ceiling only trims the long holds.
    assert clips_needed_for_window(60, interval_cap_sec=2.95) == clips_needed_for_window(60)


def test_a_cap_tighter_than_the_gap_raises_demand() -> None:
    # Sub-second clips force every interval to be short, so far more are needed.
    assert clips_needed_for_window(60, interval_cap_sec=0.95) > clips_needed_for_window(60)


def test_clips_too_short_to_cut_from_are_unusable() -> None:
    # Below the renderer's own floor between cuts, no clip count rescues it.
    r = evaluate_collection("films__x", [0.2] * 500)
    assert r.status == "unusable"
    assert "too short" in r.note


def test_the_cap_leaves_slack_so_a_clip_is_never_exactly_its_interval() -> None:
    assert interval_cap_for(3.0) < 3.0


def test_the_shortest_clip_sets_the_cap_for_the_whole_collection() -> None:
    # One short clip lowers the ceiling for everyone: the matcher may hand it any
    # interval, so the pool can only promise what its weakest member covers.
    r = evaluate_collection("films__x", [6.0] * 70 + [1.2] * 2)
    assert r.interval_cap_sec == pytest.approx(1.15)


def test_too_few_clips_is_flagged_as_thin() -> None:
    r = evaluate_collection("films__x", [6.0] * 12)
    assert r.status == "thin"
    assert "12 clips" in r.note


def test_zero_and_junk_durations_are_ignored_not_counted() -> None:
    # An unindexed clip has duration 0 and cannot be picked at all.
    r = evaluate_collection("films__x", [0.0, None, "oops", 4.0, 4.0])
    assert r.clips == 2


def test_index_evaluation_splits_by_folder() -> None:
    assets = (
        [{"genre": "films", "tag": "брат", "duration_sec": 4.0}] * 40
        + [{"genre": "films", "tag": "бумер", "duration_sec": 0.2}] * 40
        + [{"genre": "films", "tag": "", "duration_sec": 4.0}]  # unfiled, skipped
    )
    rows = {r.slug: r for r in evaluate_index(assets)}
    assert set(rows) == {"films__брат", "films__бумер"}
    assert rows["films__брат"].status == "ok"
    assert rows["films__бумер"].status == "unusable"
    # Each folder is judged on its own clips, not on the batch average.
    assert rows["films__брат"].interval_cap_sec != rows["films__бумер"].interval_cap_sec


def test_report_shape_is_json_serialisable() -> None:
    import json

    row = evaluate_collection("films__x", [4.0] * 50)
    json.dumps(row.as_dict())
