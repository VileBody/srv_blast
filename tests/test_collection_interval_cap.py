"""No interval may outlast the shortest clip the collection can offer.

Every interval is covered by exactly ONE clip and the no-repeat matcher cannot
stitch two short clips into a long one, so an interval longer than the shortest
clip fails the whole job:

    no asset can cover interval for strict no-repeat policy: idx=7 ... dur=3.412

With a batch pre-cut to 3s that happens whenever the music leaves a gap with no
usable onset — intermittently, on some tracks, which reads as a random failure.
"""
from __future__ import annotations

import pytest

from mlcore.switch_timing_deterministic import SwitchTimingParams, enforce_max_interval


def _intervals(points, start, end):
    bounds = [start] + list(points) + [end]
    return [round(b - a, 3) for a, b in zip(bounds, bounds[1:])]


def test_the_unbounded_tail_gets_split() -> None:
    # The last cut to the end of the clip is bounded by no rule at all.
    points = enforce_max_interval([2.0, 4.0], clip_start=0.0, clip_end=12.0, max_interval_sec=2.95)
    assert max(_intervals(points, 0.0, 12.0)) <= 2.95


def test_a_long_middle_gap_gets_split() -> None:
    # What the low_far fallback produces when there is no beat to land on.
    points = enforce_max_interval([2.0, 9.0], clip_start=0.0, clip_end=11.0, max_interval_sec=2.95)
    assert max(_intervals(points, 0.0, 11.0)) <= 2.95


def test_already_short_intervals_are_left_alone() -> None:
    original = [2.0, 4.0, 6.0]
    points = enforce_max_interval(original, clip_start=0.0, clip_end=8.0, max_interval_sec=2.95)
    assert points == original


def test_splits_are_even_rather_than_one_long_remainder() -> None:
    # A 9s gap at a 3s cap becomes 3+3+3, not 3+3+2.9+0.1.
    points = enforce_max_interval([], clip_start=0.0, clip_end=9.0, max_interval_sec=3.0)
    assert _intervals(points, 0.0, 9.0) == [3.0, 3.0, 3.0]


def test_output_stays_sorted_deduped_and_inside_the_window() -> None:
    points = enforce_max_interval([5.0, 5.0, 1.0], clip_start=0.0, clip_end=10.0, max_interval_sec=2.5)
    assert points == sorted(points)
    assert len(points) == len(set(points))
    assert all(0.0 < p < 10.0 for p in points)


def test_a_zero_cap_is_a_no_op_rather_than_an_infinite_split() -> None:
    assert enforce_max_interval([2.0], clip_start=0.0, clip_end=5.0, max_interval_sec=0.0) == [2.0]


@pytest.mark.parametrize("clip_len", [2.0, 3.0, 4.0])
def test_every_interval_fits_the_shortest_clip(clip_len: float) -> None:
    cap = max(0.5, clip_len - 0.05)
    points = enforce_max_interval(
        [1.0, 3.5, 12.0], clip_start=0.0, clip_end=30.0, max_interval_sec=cap
    )
    assert max(_intervals(points, 0.0, 30.0)) <= clip_len


def test_the_generator_accepts_a_lowered_hold() -> None:
    # The cap is applied to the generator first so its cuts stay on beats.
    p = SwitchTimingParams(max_hold_sec=2.95)
    assert p.max_hold_sec == 2.95
    assert SwitchTimingParams().max_hold_sec == 3.5  # default untouched
