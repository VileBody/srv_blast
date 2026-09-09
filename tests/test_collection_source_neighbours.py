# -*- coding: utf-8 -*-
"""Consecutive cuts must not come from neighbouring moments of one shot.

A collection folder is cut sequentially out of one source video, so clip-041 and
clip-042 are consecutive seconds of the same take. Unique file names — which the
no-repeat policy guarantees — are therefore NOT enough to guarantee a visible
cut: the two read on screen as the same frame twice.

Reported after the first 16:9 renders ("некоторые кадры даблятся и одинаковые
идут прям друг за другом").
"""
from __future__ import annotations

import re

import pytest

from mlcore import footage_picker as fp


def _clip(i: int, genre: str = "cine16x9", tag: str = "Los_Angels") -> dict:
    name = f"{genre}__la-abc123__clip-{i:03d}.mp4"
    return {
        "file_name": name,
        "media_file_name": name,
        "genre": genre,
        "tag": tag,
        "duration_sec": 3.83,
        "src_w": 1920,
        "src_h": 1080,
        fp._SELECTION_RANK_SCORE_KEY: 1.0,
    }


def _ordinals(names):
    return [int(re.search(r"clip-(\d+)", n).group(1)) for n in names]


def _min_adjacent_gap(names):
    o = _ordinals(names)
    return min((abs(b - a) for a, b in zip(o, o[1:])), default=99)


def test_ordinal_is_read_off_the_trailing_number() -> None:
    assert fp._source_ordinal("cine16x9__la-abc__clip-041.mp4") == 41
    assert fp._source_ordinal("clip_003.mp4") == 3
    assert fp._source_ordinal("no-digits.mp4") is None


def test_neighbours_are_pulled_apart() -> None:
    names = [f"x__clip-{i:03d}.mp4" for i in (10, 11, 40, 41, 70)]
    pool = [n for n in names]
    out = fp._separate_source_neighbours(names, pool_names=pool)
    assert sorted(out) == sorted(names), "every clip must still be used exactly once"
    assert _min_adjacent_gap(out) >= fp._SOURCE_NEIGHBOUR_MIN_GAP


def test_an_already_spread_order_is_left_alone() -> None:
    names = [f"x__clip-{i:03d}.mp4" for i in (5, 40, 12, 77, 30)]
    assert fp._separate_source_neighbours(names, pool_names=names) == names


def test_a_pool_without_ordinals_is_untouched() -> None:
    names = ["alpha.mp4", "beta.mp4", "gamma.mp4"]
    assert fp._separate_source_neighbours(names, pool_names=names) == names


def test_the_matcher_applies_it_to_a_collection_pool() -> None:
    pool = [_clip(i) for i in range(1, 101)]
    intervals = [(t, t + 1.8) for t in [i * 1.8 for i in range(25)]]
    names = fp._assign_unique_file_names_for_intervals(
        intervals=intervals, pool=pool, seed_value=7,
        separate_source_neighbours=fp._is_collection_pool(pool),
    )
    assert len(set(names)) == len(names)
    assert _min_adjacent_gap(names) >= fp._SOURCE_NEIGHBOUR_MIN_GAP


def test_the_tagged_pool_is_not_subjected_to_it() -> None:
    # Pinterest ids are 18-digit numbers; a trailing-number "ordinal" there is
    # meaningless and separating on it would be noise.
    tagged = [
        {"file_name": f"10027552919976{i:04d}.mp4", "genre": "hiphop", "tag": "street",
         "duration_sec": 4.0, "src_w": 1080, "src_h": 1920}
        for i in range(40)
    ]
    assert fp._is_collection_pool(tagged) is False
    assert fp._is_collection_pool([_clip(1)]) is True


@pytest.mark.parametrize("kind", ["films", "people", "cine16x9"])
def test_every_collection_kind_counts(kind: str) -> None:
    assert fp._is_collection_pool([_clip(1, genre=kind)]) is True
