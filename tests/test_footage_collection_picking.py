"""Collection buckets through the Stage2B resolver and the picker adapter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mlcore.footage_picker import _build_raw_pool, resolve_style_pick_from_raw_filters
from mlcore.footage_style_resolver import resolve_style_raw
from mlcore.models.footage_style import FootageStyleRawFilters, FootageStyleRawPayload


@pytest.fixture()
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "footage_collections.json"
    p.write_text(
        json.dumps(
            {
                "version": "t",
                "collections": [
                    {"kind": "films", "folder": "interstellar", "label": "Интерстеллар"},
                    {"kind": "films", "folder": "drive", "label": "Драйв"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(p))
    return p


def _asset(name: str, genre: str, tag: str, **meta: Any) -> Dict[str, Any]:
    return {"file_name": name, "genre": genre, "tag": tag, "duration_sec": 30.0, **meta}


# An untagged clip in a collection folder, and a fully tagged clip in the normal
# pool — the two populations the plane has to keep apart.
COLLECTION_CLIP = _asset("inter_01.mp4", "films", "interstellar")
OTHER_COLLECTION_CLIP = _asset("drive_01.mp4", "films", "drive")
TAGGED_CLIP = _asset(
    "pin_01.mp4",
    "pop",
    "sad",
    meta_theme_tags=["forest", "fog"],
    meta_people_type="none",
    meta_color_tone="dark",
    meta_mood="minor",
)
ALL = [COLLECTION_CLIP, OTHER_COLLECTION_CLIP, TAGGED_CLIP]

TAG_PAYLOAD = FootageStyleRawPayload(
    theme="loneliness_isolation_minor",
    mood="minor",
    tags_group="eerie_nature",
    filters=FootageStyleRawFilters(color_priority=["dark"], priority_theme_tags=["forest"]),
)


def _names(pool: List[Dict[str, Any]]) -> List[str]:
    return sorted(str(x["file_name"]) for x in pool)


def test_collection_bucket_survives_the_pydantic_carrier(registry: Path) -> None:
    # Regression: the raw payload demands a mood and >=1 priority tag, and a
    # collection has neither. It must be carried, not invented.
    raw = resolve_style_raw("collection", "films__interstellar")
    assert (raw.theme, raw.tags_group) == ("collection", "films__interstellar")
    assert raw.filters.priority_theme_tags == ["collection films interstellar"]


def test_pool_is_exactly_the_folder(registry: Path) -> None:
    raw = resolve_style_raw("collection", "films__interstellar")
    pool = _build_raw_pool(raw, ALL, media_type="video")
    # Not the sibling collection, not the tagged pool — only this folder.
    assert _names(pool) == ["inter_01.mp4"]


def test_untagged_collection_clips_never_enter_a_tag_bucket(registry: Path) -> None:
    pool = _build_raw_pool(TAG_PAYLOAD, ALL, media_type="video")
    assert _names(pool) == ["pin_01.mp4"]


def test_tagged_pool_clips_never_enter_a_collection(registry: Path) -> None:
    raw = resolve_style_raw("collection", "films__interstellar")
    # Even a clip carrying the collection's own sentinel tag stays out: membership
    # is the folder, and the sentinel is never compared against anything.
    impostor = _asset(
        "impostor.mp4", "pop", "sad", meta_theme_tags=["collection films interstellar"]
    )
    pool = _build_raw_pool(raw, [*ALL, impostor], media_type="video")
    assert _names(pool) == ["inter_01.mp4"]


def test_resolution_ignores_mood_for_collections(registry: Path) -> None:
    # The tag path filters on meta_mood first; collection clips have none, so
    # without the short-circuit every one of them would be dropped here.
    raw = resolve_style_raw("collection", "films__interstellar")
    pick, diag = resolve_style_pick_from_raw_filters(
        raw_pick=raw, mapped_assets=ALL, seed_key="job-1"
    )
    assert (pick.genre, pick.tag) == ("films", "interstellar")
    assert (diag.selected_group_assets_count, diag.mood_filtered_out) == (1, 0)


def test_empty_collection_folder_fails_loudly(registry: Path) -> None:
    raw = resolve_style_raw("collection", "films__drive")
    with pytest.raises(RuntimeError, match="collection_pool_empty"):
        resolve_style_pick_from_raw_filters(
            raw_pick=raw, mapped_assets=[COLLECTION_CLIP, TAGGED_CLIP], seed_key="job-1"
        )


def test_a_slug_from_an_auto_registered_folder_still_resolves(registry: Path) -> None:
    # Folders auto-register from the index, which lives with the orchestrator —
    # a process without it (the bots) must not choke on a slug it cannot look
    # up. The slug carries the kind and the folder, which is all identity needs.
    raw = resolve_style_raw("collection", "films__does_not_exist")
    assert raw.theme == "collection"
    assert raw.tags_group == "films__does_not_exist"


def test_a_slug_that_names_no_kind_fails_loudly(registry: Path) -> None:
    with pytest.raises(RuntimeError, match="not resolvable"):
        resolve_style_raw("collection", "nonsense")
