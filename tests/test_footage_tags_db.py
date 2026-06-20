from __future__ import annotations

from mlcore.footage_tags_db import (
    build_tag_record,
    extract_clip_id,
    merge_records_by_clip_id,
    snapshot_row_from_record,
)


def test_extract_clip_id() -> None:
    assert extract_clip_id("Rock__dark_forest__1001276929637034910_x") == "1001276929637034910"
    assert extract_clip_id("1005569423036085532_Serene.mp4") == "1005569423036085532"
    assert extract_clip_id("no-digits-here.mp4") is None


def test_build_record_normalizes_and_keys() -> None:
    rec = build_tag_record(
        {
            "video_key": "Rock__dark_forest__1001276929637034910_x",
            "video_path": "Rock\\dark_forest\\1001276929637034910_x",
            "mood": "MINOR",
            "color_tone": "Cold",
            "people_type": "guy",  # -> guys
            "theme_tags": ["Dark Forest", "fog", "fog", " night "],
        },
        tagger="migration",
    )
    assert rec is not None
    assert rec["clip_id"] == "1001276929637034910"
    assert rec["mood"] == "minor"
    assert rec["color_tone"] == "cold"
    assert rec["people_type"] == "guys"
    assert rec["theme_tags"] == ["dark forest", "fog", "night"]  # normalized + deduped
    assert rec["tagger"] == "migration"


def test_build_record_rejects_unkeyable_row() -> None:
    assert build_tag_record({"video_key": "no-id", "theme_tags": ["x"]}) is None


def test_build_record_drops_invalid_enum_values() -> None:
    rec = build_tag_record(
        {"video_key": "12345678_a", "mood": "happy", "color_tone": "rainbow", "people_type": "alien"}
    )
    assert rec is not None
    assert rec["mood"] == ""
    assert rec["color_tone"] == ""
    assert rec["people_type"] == "none"


def test_merge_dedups_by_clip_id_and_unions_tags() -> None:
    # Same clip in two genre folders; second source has extra tag + fills mood.
    a = build_tag_record({"video_key": "Pop__x__12345678_a", "color_tone": "cold", "theme_tags": ["snow"]})
    b = build_tag_record(
        {"video_key": "Rock__y__12345678_a", "mood": "minor", "color_tone": "cold", "theme_tags": ["snow", "winter"]}
    )
    merged = merge_records_by_clip_id([[a], [b]])
    assert len(merged) == 1
    rec = merged[0]
    assert rec["clip_id"] == "12345678"
    assert set(rec["theme_tags"]) == {"snow", "winter"}  # unioned
    assert rec["mood"] == "minor"  # taken from more complete record


def test_merge_freshest_last_breaks_ties() -> None:
    old = build_tag_record({"video_key": "12345678_a", "color_tone": "cold", "theme_tags": ["a"]})
    new = build_tag_record({"video_key": "12345678_a", "color_tone": "dark", "theme_tags": ["a"]})
    merged = merge_records_by_clip_id([[old], [new]])
    assert merged[0]["color_tone"] == "dark"  # equal completeness -> last wins


def test_snapshot_roundtrip_shape() -> None:
    rec = build_tag_record(
        {"video_key": "Rock__df__12345678_a", "mood": "minor", "color_tone": "cold",
         "people_type": "none", "theme_tags": ["fog", "night"]}
    )
    snap = snapshot_row_from_record(rec)
    # Must carry the fields footage_picker.load_footage_style_metadata_rows reads.
    assert snap["video_key"] == "Rock__df__12345678_a"
    assert snap["mood"] == "minor"
    assert snap["color_tone"] == "cold"
    assert snap["people_type"] == "none"
    assert snap["theme_tags"] == ["fog", "night"]
    assert extract_clip_id(snap["video_key"]) == "12345678"
