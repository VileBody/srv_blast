from __future__ import annotations

import json

from mlcore import footage_picker
from mlcore.footage_picker import (
    _build_raw_pool,
    _normalize_meta_tag,
    _normalize_theme_tag,
    _load_tag_aliases,
)
from mlcore.models.footage_style import FootageStyleRawPayload


def test_aliases_file_loads_and_is_canonical() -> None:
    aliases = _load_tag_aliases()
    assert aliases, "tag_aliases.json should load a non-empty map"
    # keys/values normalized lowercase, no self-maps
    for k, v in aliases.items():
        assert k == k.strip().lower()
        assert v == v.strip().lower()
        assert k != v


def test_known_freeform_tags_remap_to_taxonomy() -> None:
    # representative curated mappings
    assert _normalize_meta_tag("rainy") == "rain"
    assert _normalize_meta_tag("Mountains") == "mountain"
    assert _normalize_meta_tag("streetlight") == "street lights"
    assert _normalize_meta_tag("silhouettes") == "silhouette"
    assert _normalize_meta_tag("city view") == "cityscape"


def test_non_aliased_tag_passes_through_unchanged() -> None:
    assert _normalize_meta_tag("dark forest") == "dark forest"
    # canonical taxonomy tag is its own value (must not be remapped)
    assert _normalize_meta_tag("rain") == "rain"


def test_llm_side_normalizer_does_not_alias() -> None:
    # priority_theme_tags / exclude_tags use _normalize_theme_tag and stay literal
    assert _normalize_theme_tag("rainy") == "rainy"
    assert _normalize_theme_tag("mountains") == "mountains"


def test_freeform_clip_tag_matches_taxonomy_pick_via_alias() -> None:
    # Clip tagged only with free-form "rainy"/"mountains"; LLM picks canonical tags.
    assets = [
        {
            "file_name": "clip_rain.mp4",
            "genre": "Rock",
            "tag": "rain_aesthetic",
            "duration_sec": 3.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_theme_tags": ["rainy", "mountains"],
            "meta_people_type": "none",
            "meta_color_tone": "cold",
        }
    ]
    raw = FootageStyleRawPayload.model_validate(
        {
            "artist_id": "rock_grunge",
            "theme": "loneliness_isolation_minor",
            "mood": "minor",
            "tags_group": "vast_emptiness",
            "filters": {
                "color_priority": ["cold"],
                "exclude_people": [],
                "exclude_tags": [],
                "priority_theme_tags": ["rain", "mountain"],
            },
        }
    )
    pool = _build_raw_pool(raw, assets)
    assert len(pool) == 1, "clip should be reachable via alias remap"
    # overlap score = 2 (rain + mountain), color bonus 0.5
    assert pool[0][footage_picker._SELECTION_RANK_SCORE_KEY] >= 2.0


def test_missing_alias_file_degrades_gracefully(tmp_path, monkeypatch) -> None:
    # Simulate a parse-safe empty map; picker must still normalize literally.
    monkeypatch.setattr(footage_picker, "_TAG_ALIASES", {})
    assert _normalize_meta_tag("rainy") == "rainy"
