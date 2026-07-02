from __future__ import annotations

from config.styles.theme_groups import THEME_GROUPS
from mlcore.footage_bucket_catalog import (
    Bucket,
    build_buckets,
    dedup_buckets,
    get_bucket_catalog,
    parse_themes_logic,
)
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "footage_v2.py").read_text(encoding="utf-8")


def test_parse_themes_logic_yields_all_themes() -> None:
    logic = parse_themes_logic(_SRC)
    assert len(logic) == 22
    assert "heartbreak_minor" in logic
    assert "tags_groups" in logic["heartbreak_minor"]


def test_build_buckets_have_tags_label_and_mood() -> None:
    buckets = build_buckets(_SRC)
    assert len(buckets) >= 55
    for b in buckets:
        assert b.priority_tags, f"{b.bucket_id} has no tags"
        assert b.label, f"{b.bucket_id} has no label"
        assert b.mood in {"major", "minor"}, f"{b.bucket_id} bad mood {b.mood}"
        assert b.bucket_id == f"{b.theme}:{b.tags_group}"


def test_dedup_collapses_visual_twins() -> None:
    raw = build_buckets(_SRC)
    deduped = get_bucket_catalog(_SRC)
    assert len(deduped) <= len(raw)
    # no two deduped buckets share the same (priority-tag set, mood)
    seen = set()
    for b in deduped:
        key = (b.tag_key, b.mood)
        assert key not in seen, f"duplicate visual bucket survived: {b.bucket_id}"
        seen.add(key)


def test_parse_matches_theme_groups_mirror() -> None:
    """CI gate: footage_v2 THEMES LOGIC and theme_groups.THEME_GROUPS must agree
    on which (theme, group) pairs exist — catch drift between the two files."""
    logic = parse_themes_logic(_SRC)
    parsed = {(t, g) for t, tv in logic.items() for g in (tv.get("tags_groups") or {})}
    mirror = {(t, g) for t, groups in THEME_GROUPS.items() for g in groups}
    only_prompt = parsed - mirror
    only_mirror = mirror - parsed
    assert not only_prompt, f"(theme,group) in footage_v2 but not THEME_GROUPS: {sorted(only_prompt)}"
    assert not only_mirror, f"(theme,group) in THEME_GROUPS but not footage_v2: {sorted(only_mirror)}"


def test_theme_level_exclude_people_parsed() -> None:
    """The theme "exclude" (people axis) must land on every bucket of that theme
    so the deterministic Stage2B resolver can copy it into filters.exclude."""
    buckets = build_buckets(_SRC)
    by_id = {b.bucket_id: b for b in buckets}
    b = by_id["romance_major:nature_sunset"]
    assert set(b.exclude) == {"crowd", "none", "driver"}
    # every bucket carries an exclude list (possibly empty), never None
    for bk in buckets:
        assert isinstance(bk.exclude, list)


def test_dedup_is_deterministic_keeps_first() -> None:
    raw = build_buckets(_SRC)
    deduped = dedup_buckets(raw)
    # first occurrence of each (tagset, mood) is the one kept
    first_ids = []
    seen = set()
    for b in raw:
        k = (b.tag_key, b.mood)
        if k not in seen:
            seen.add(k)
            first_ids.append(b.bucket_id)
    assert [b.bucket_id for b in deduped] == first_ids
