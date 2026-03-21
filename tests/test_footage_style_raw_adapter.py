from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore.footage_picker import (
    load_footage_style_metadata_rows,
    map_inventory_assets_with_style_metadata,
    merge_footage_style_metadata_rows,
    resolve_style_pick_from_raw_filters,
)
from mlcore.models.footage_style import FootageStyleRawPayload


def test_metadata_merge_and_inventory_mapping_by_clip_id(tmp_path: Path) -> None:
    db1 = [
        {
            "video_key": "Alternative__aesthetic_winter__12345678_foo",
            "video_path": "Alternative\\aesthetic_winter\\12345678_foo",
            "mood": "minor",
            "color_tone": "dark",
            "people_type": "guy",
            "theme_tags": ["night city", "shadows"],
        }
    ]
    db2 = [
        {
            "video_key": "Русский__12345678",
            "video_path": "Русский\\12345678",
            "mood": "minor",
            "color_tone": "dark",
            "people_type": "guys",
            "theme_tags": ["reflection", "shadows"],
        }
    ]
    p1 = tmp_path / "db1.json"
    p2 = tmp_path / "db2.json"
    p1.write_text(json.dumps(db1, ensure_ascii=False), encoding="utf-8")
    p2.write_text(json.dumps(db2, ensure_ascii=False), encoding="utf-8")

    rows = load_footage_style_metadata_rows(db_paths=[p1, p2])
    merged = merge_footage_style_metadata_rows(rows)
    assert "12345678" in merged
    assert merged["12345678"]["people_type"] == "guys"
    assert set(merged["12345678"]["theme_tags"]) == {"night city", "shadows", "reflection"}

    assets = [
        {
            "file_name": "12345678_clip.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 3.0,
            "src_w": 720,
            "src_h": 1280,
        },
        {
            "file_name": "99999999_clip.mp4",
            "genre": "Rock",
            "tag": "rain_aesthetic",
            "duration_sec": 3.0,
            "src_w": 720,
            "src_h": 1280,
        },
    ]
    mapped, unmapped = map_inventory_assets_with_style_metadata(assets=assets, metadata_index=merged)
    assert len(mapped) == 1
    assert mapped[0]["file_name"] == "12345678_clip.mp4"
    assert unmapped == ["99999999_clip.mp4"]


def test_raw_filters_adapter_resolves_genre_tag_deterministically() -> None:
    raw = FootageStyleRawPayload.model_validate(
        {
            "theme": "jealousy_minor",
            "mood": "minor",
            "filters": {
                "color_priority": ["dark", "cold"],
                "exclude": ["couple", "crowd", "girls"],
                "priority_theme_tags": ["night city", "shadows", "reflection"],
            },
        }
    )
    mapped_assets = [
        {
            "file_name": "12345678_a.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 3.0,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "guys",
            "meta_theme_tags": ["night city", "shadows", "reflection"],
        },
        {
            "file_name": "12345679_b.mp4",
            "genre": "Rock",
            "tag": "rain_aesthetic",
            "duration_sec": 3.0,
            "meta_mood": "minor",
            "meta_color_tone": "cold",
            "meta_people_type": "couple",
            "meta_theme_tags": ["night city"],
        },
        {
            "file_name": "12345680_c.mp4",
            "genre": "Pop",
            "tag": "dream_aesthetic",
            "duration_sec": 3.0,
            "meta_mood": "minor",
            "meta_color_tone": "warm",
            "meta_people_type": "none",
            "meta_theme_tags": ["flowers"],
        },
    ]

    pick, diag = resolve_style_pick_from_raw_filters(
        raw_pick=raw,
        mapped_assets=mapped_assets,
        seed_key="job-style-seed",
        total_assets=5,
        unmapped_assets=2,
        metadata_rows_merged=100,
    )
    assert pick.genre == "Rock"
    assert pick.tag == "dark_forest"
    assert diag.unmapped_assets == 2
    assert diag.exclude_filtered_out >= 1


def test_raw_filters_adapter_fails_on_empty_candidates() -> None:
    raw = FootageStyleRawPayload.model_validate(
        {
            "theme": "jealousy_minor",
            "mood": "minor",
            "filters": {
                "color_priority": ["dark"],
                "exclude": ["guys"],
                "priority_theme_tags": ["night city"],
            },
        }
    )
    mapped_assets = [
        {
            "file_name": "12345678_a.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 3.0,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "guys",
            "meta_theme_tags": ["night city"],
        }
    ]

    with pytest.raises(RuntimeError, match="No mapped assets remain after people exclusion"):
        resolve_style_pick_from_raw_filters(
            raw_pick=raw,
            mapped_assets=mapped_assets,
            seed_key="job-style-seed",
        )

