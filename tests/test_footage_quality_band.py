"""Wave 1 — quality band selection in footage_picker.

Score = overlap count + 0.5*color, so floor 2.0 == "matched >= 2 theme tags".
The head of an interval must be a near-best (in-band) clip, never a weak
overlap-1 clip; variety across seeds happens WITHIN the band; determinism holds.
"""
from __future__ import annotations

import pytest

from mlcore import footage_picker as fp


# --------------------------------------------------------------------------- #
# band membership
# --------------------------------------------------------------------------- #
def test_band_excludes_weak_overlap1_when_better_exists():
    scores = {"a": 3.0, "b": 2.0, "c": 1.0, "d": 1.5}  # c=overlap1, d=overlap1+color
    band = fp._quality_band(list(scores), scores)
    # best=3, floor=2 -> thresh=max(2, 3-1)=2 -> only a,b
    assert set(band) == {"a", "b"}


def test_band_floor_requires_two_tags_even_when_gap_small():
    scores = {"a": 2.5, "b": 2.0, "c": 1.5}  # c = overlap1+color
    band = fp._quality_band(list(scores), scores)
    # best=2.5 -> thresh=max(2.0, 1.5)=2.0 -> a,b (c=1.5 excluded)
    assert set(band) == {"a", "b"}


def test_band_relaxes_floor_for_thin_bucket():
    scores = {"a": 1.0, "b": 1.0, "c": 1.5}  # nothing reaches overlap 2
    band = fp._quality_band(list(scores), scores)
    # best=1.5 < floor -> thresh=1.5-1=0.5 -> keep everything (don't empty)
    assert set(band) == {"a", "b", "c"}


def test_band_single_standout_only():
    scores = {"star": 4.0, "x": 1.0, "y": 1.0}
    band = fp._quality_band(list(scores), scores)
    # best=4 -> thresh=max(2,3)=3 -> only the standout
    assert band == ["star"]


# --------------------------------------------------------------------------- #
# ordering: head always in band, variety within band, determinism
# --------------------------------------------------------------------------- #
def _order(names, scores, seed, band_on=True, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1" if band_on else "0")
    return fp._deterministic_file_name_order(
        file_names=names,
        seed_value=seed,
        interval_idx=0,
        interval_start=0.0,
        scores_by_name=scores,
    )


def test_head_never_weak_across_seeds(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["strong", "mid", "weak"]
    scores = {"strong": 3.0, "mid": 2.0, "weak": 1.0}
    heads = set()
    for seed in range(200):
        order = fp._deterministic_file_name_order(
            file_names=names, seed_value=seed, interval_idx=0, interval_start=0.0,
            scores_by_name=scores,
        )
        heads.add(order[0])
    # weak (overlap 1) must NEVER be the winner; both band members should appear
    assert "weak" not in heads
    assert heads == {"strong", "mid"}


def test_deterministic_same_seed_same_order(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["a", "b", "c", "d"]
    scores = {"a": 3.0, "b": 3.0, "c": 2.0, "d": 1.0}
    o1 = _order(names, scores, seed=42)
    o2 = _order(names, scores, seed=42)
    assert o1 == o2
    assert o1[0] in {"a", "b", "c"}          # never the overlap-1 "d"
    assert o1[-1] == "d"                       # out-of-band clip sinks to the tail


def test_cooldown_picks_least_recently_used_within_band(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["a", "b", "c", "weak"]
    scores = {"a": 3.0, "b": 3.0, "c": 3.0, "weak": 1.0}  # a,b,c in band; weak out
    # a served most recently (hottest), b older, c never -> c is coldest
    cooldown = {"a": 0.0, "b": 5.0, "c": 31.0}  # higher = colder = preferred
    order = fp._deterministic_file_name_order(
        file_names=names, seed_value=1, interval_idx=0, interval_start=0.0,
        scores_by_name=scores, cooldown_by_name=cooldown,
    )
    assert order[0] == "c"        # coldest in-band wins regardless of seed
    assert "weak" not in order[:3]  # out-of-band never near the head
    assert order[-1] == "weak"


def test_cooldown_head_is_deterministic_given_state(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["a", "b"]
    scores = {"a": 2.0, "b": 2.0}
    cooldown = {"a": 1.0, "b": 9.0}
    for seed in (1, 2, 3, 99):
        order = fp._deterministic_file_name_order(
            file_names=names, seed_value=seed, interval_idx=0, interval_start=0.0,
            scores_by_name=scores, cooldown_by_name=cooldown,
        )
        assert order[0] == "b"  # colder clip always first, independent of seed


def test_line_boost_wins_within_band(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["a", "b", "c"]
    scores = {"a": 3.0, "b": 3.0, "c": 3.0}   # all in band
    boost = {"a": 0.0, "b": 2.0, "c": 0.0}    # b matches the lyric line
    for seed in (1, 2, 3, 99):
        order = fp._deterministic_file_name_order(
            file_names=names, seed_value=seed, interval_idx=0, interval_start=0.0,
            scores_by_name=scores, boost_by_name=boost,
        )
        assert order[0] == "b"                # line match wins regardless of seed


def test_line_boost_beats_cooldown(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["a", "b"]
    scores = {"a": 3.0, "b": 3.0}
    boost = {"a": 1.0, "b": 0.0}              # a matches the line
    cooldown = {"a": 0.0, "b": 31.0}          # b is colder
    order = fp._deterministic_file_name_order(
        file_names=names, seed_value=1, interval_idx=0, interval_start=0.0,
        scores_by_name=scores, cooldown_by_name=cooldown, boost_by_name=boost,
    )
    assert order[0] == "a"                    # line > cooldown


def test_line_boost_never_leaves_the_band(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "1")
    names = ["strong", "weak"]
    scores = {"strong": 3.0, "weak": 1.0}     # weak is out of band (floor 2)
    boost = {"strong": 0.0, "weak": 5.0}      # weak matches the line but is weak vibe
    order = fp._deterministic_file_name_order(
        file_names=names, seed_value=1, interval_idx=0, interval_start=0.0,
        scores_by_name=scores, boost_by_name=boost,
    )
    assert order[0] == "strong"               # never leave the bucket for a weak clip


def test_legacy_path_still_works_when_disabled(monkeypatch):
    monkeypatch.setenv("FOOTAGE_QUALITY_BAND", "0")
    names = ["a", "b", "c"]
    scores = {"a": 3.0, "b": 2.0, "c": 1.0}
    order = fp._deterministic_file_name_order(
        file_names=names, seed_value=7, interval_idx=0, interval_start=0.0,
        scores_by_name=scores,
    )
    assert sorted(order) == sorted(names)      # returns a full permutation
