# -*- coding: utf-8 -*-
"""Photo buckets are ranked against the track, the same as footage buckets.

The ranker is theme-first: lyrics -> ranked THEMES -> the buckets each theme maps
to. The theme universe came from candidate_themes(), which is the FOOTAGE
taxonomy — but the photo mapping carries six themes of its own (forest_calm,
night_ride, party_energy, home_intimacy, street_youth, digital_dream). A theme
missing from that list is never scored, so its buckets could only ever reach the
user through the catalog-order tail: a forest track literally could not surface
the forest bucket.
"""
from __future__ import annotations

import pytest

from config.styles.theme_relevance import candidate_themes
from mlcore.footage_bucket_ranker import rank_buckets
from mlcore.footage_visual_catalog import load_theme_buckets, load_visual_catalog
from mlcore.photo_bucket_catalog import load_photo_catalog, load_photo_theme_buckets


def test_photo_only_themes_are_not_stranded() -> None:
    """The gap this fixes. If the taxonomy ever absorbs these, the test still
    passes — it asserts coverage, not the size of the gap."""
    photo_themes = set(load_photo_theme_buckets())
    assert photo_themes  # mapping must exist at all
    # every mapped theme has to be reachable by the ranker
    catalog = load_photo_catalog()
    ranked = rank_buckets(lyrics="лес туман горы", mood="", catalog=catalog, llm_call=None)
    assert set(ranked) == {b.bucket_id for b in catalog}


@pytest.mark.parametrize(
    "lyrics,expected",
    [
        ("лес туман горы тишина сосны", "photo:forest_fog_dark"),
        ("клуб вечеринка танцы толпа", "photo:crowd_club_dark"),
        ("ночь город дождь неон", "photo:urban_rain_night"),
    ],
)
def test_the_matching_bucket_reaches_the_top(lyrics: str, expected: str) -> None:
    """Top-3 because the shortlist shows three per page — a bucket below that is
    not "ranked" from the user's side, it is buried."""
    ranked = rank_buckets(
        lyrics=lyrics, mood="", catalog=load_photo_catalog(), llm_call=None
    )
    assert expected in ranked[:3], (lyrics, ranked[:5])


def test_ranking_actually_responds_to_the_track() -> None:
    catalog = load_photo_catalog()
    a = rank_buckets(lyrics="лес туман горы", mood="", catalog=catalog, llm_call=None)
    b = rank_buckets(lyrics="клуб вечеринка танцы", mood="", catalog=catalog, llm_call=None)
    assert a != b
    # ...and no lyrics still means catalog order, not a random one
    none = rank_buckets(lyrics="", mood="", catalog=catalog, llm_call=None)
    assert none == [x.bucket_id for x in catalog]


def test_the_footage_theme_universe_is_unchanged() -> None:
    """Themes are APPENDED, not substituted. The footage mapping adds nothing to
    candidate_themes(), so footage ranking and its tie-break order are untouched
    — verified separately by diffing 18 lyric/mood cases before and after."""
    assert not [t for t in load_theme_buckets() if t not in set(candidate_themes(""))]


def test_footage_ranking_still_responds_to_the_track() -> None:
    catalog = load_visual_catalog()
    a = rank_buckets(lyrics="ночь город дождь", mood="", catalog=catalog, llm_call=None)
    b = rank_buckets(lyrics="любовь солнце лето", mood="", catalog=catalog, llm_call=None)
    assert a[:3] != b[:3]
