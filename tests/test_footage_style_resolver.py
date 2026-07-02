from __future__ import annotations

from pathlib import Path

import pytest

from mlcore.footage_bucket_catalog import build_buckets, get_bucket_catalog
from mlcore.footage_style_resolver import (
    bucket_to_style_raw,
    find_bucket,
    resolve_style_raw,
    resolve_style_rotation,
)
from mlcore.footage_picker import resolve_style_pick_from_raw_filters
from mlcore.models.footage_style import FootageStyleRawPayload, FootageStyleRotation

_SRC = (Path(__file__).resolve().parents[1] / "footage_v2.py").read_text(encoding="utf-8")


def test_every_bucket_resolves_to_valid_style_raw() -> None:
    """Determinism guard: EVERY bucket in the catalog must build a valid
    FootageStyleRawPayload — an unexpected footage_v2 color/people value would
    hard-fail here (in CI) rather than at runtime on a live job."""
    buckets = build_buckets(_SRC)
    assert buckets, "no buckets parsed"
    for b in buckets:
        raw = bucket_to_style_raw(b)
        assert isinstance(raw, FootageStyleRawPayload)
        assert raw.theme == b.theme
        assert raw.tags_group == b.tags_group
        assert raw.mood == b.mood
        assert raw.artist_id is None
        # priority_theme_tags = ALL group tags (broader than the LLM's 6-10 subset)
        assert set(raw.filters.priority_theme_tags) == set(b.priority_tags)
        assert raw.filters.color_priority  # never empty (validator would raise)


def test_theme_exclude_people_flows_into_filters() -> None:
    # romance_major theme excludes crowd/none/driver (people axis).
    raw = resolve_style_raw("romance_major", "nature_sunset")
    assert set(raw.filters.exclude) == {"crowd", "none", "driver"}


def test_resolve_rotation_is_single_subgroup_matching_slot() -> None:
    rot = resolve_style_rotation("romance_major", "nature_sunset")
    assert isinstance(rot, FootageStyleRotation)
    assert len(rot.subgroups) == 1
    sg = rot.subgroups[0]
    assert sg.theme == "romance_major"
    assert sg.tags_group == "nature_sunset"


def test_find_bucket_requires_both_theme_and_group() -> None:
    with pytest.raises(RuntimeError):
        find_bucket("romance_major", "")
    with pytest.raises(RuntimeError):
        find_bucket("", "nature_sunset")
    with pytest.raises(RuntimeError):
        find_bucket("no_such_theme", "no_such_group")


def test_deterministic_pick_flows_through_same_picker_adapter() -> None:
    """The resolved raw payload must produce a valid genre/tag via the SAME
    adapter the LLM rotation path uses — proving equivalence end-to-end."""
    raw = resolve_style_raw("romance_major", "nature_sunset")
    # Minimal mapped inventory: major mood, non-excluded people, overlapping tags.
    mapped = [
        {
            "genre": "Nature",
            "tag": "sunset",
            "meta_mood": "major",
            "meta_people_type": "couple",  # not in {crowd,none,driver}
            "meta_theme_tags": ["sunset", "beach", "ocean"],
            "meta_color_tone": "warm",
            "duration_sec": 3.0,
        },
        {
            "genre": "Urban",
            "tag": "night",
            "meta_mood": "major",
            "meta_people_type": "couple",
            "meta_theme_tags": ["street", "neon"],  # no overlap
            "meta_color_tone": "cold",
            "duration_sec": 3.0,
        },
    ]
    pick, diag = resolve_style_pick_from_raw_filters(
        raw_pick=raw,
        mapped_assets=mapped,
        seed_key="job-test:v1",
        total_assets=len(mapped),
    )
    # Overlap on sunset/beach/ocean wins over the zero-overlap urban clip.
    assert (pick.genre, pick.tag) == ("Nature", "sunset")


def test_resolver_covers_full_deduped_catalog() -> None:
    # sanity: the catalog the bot ranks over is a subset of resolvable buckets
    for b in get_bucket_catalog(_SRC):
        raw = resolve_style_raw(b.theme, b.tags_group)
        assert raw.tags_group == b.tags_group
