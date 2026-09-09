# -*- coding: utf-8 -*-
"""From the button the user taps to the clips the picker returns.

Each link is covered on its own elsewhere; this pins the CHAIN, because that is
where the routing question actually lives: does tapping «Фильмы» and choosing
«Бойцовский клуб» really run the job on that folder and nothing else?

    bot button  ->  collection:films__бойцовский клуб
                ->  rotation_theme="collection", rotation_tags_group=<slug>
                ->  FOOTAGE_INVENTORY_JSON = the collection inventory
                ->  a pool holding exactly that folder's clips
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mlcore import footage_picker as fp
from mlcore.footage_batch_distribution import resolve_bucket_slot
from mlcore.footage_collection_catalog import load_collection_catalog
from mlcore.footage_style_resolver import resolve_style_rotation


def _clip(name: str, genre: str, tag: str, **extra: Any) -> Dict[str, Any]:
    row = {
        "file_name": name,
        "genre": genre,
        "tag": tag,
        "duration_sec": 3.0,
        "src_w": 1920,
        "src_h": 1080,
    }
    row.update(extra)
    return row


@pytest.fixture()
def catalog():
    cat = load_collection_catalog()
    assert cat, "the shipped registry must carry the film collections"
    return cat


def test_the_bot_id_routes_to_the_collection_plane(catalog) -> None:
    for bucket in catalog:
        theme, group = resolve_bucket_slot(bucket.bucket_id, catalog=[])
        assert (theme, group) == ("collection", bucket.slug)


def test_the_slot_resolves_to_that_folder_without_an_llm(catalog) -> None:
    bucket = catalog[0]
    theme, group = resolve_bucket_slot(bucket.bucket_id, catalog=[])
    rotation = resolve_style_rotation(theme, group)
    assert len(rotation.subgroups) == 1
    raw = rotation.subgroups[0]
    assert raw.theme == "collection"
    assert raw.tags_group == bucket.slug


def test_the_pool_is_exactly_the_chosen_film(catalog) -> None:
    chosen, other = catalog[0], catalog[1]
    assets: List[Dict[str, Any]] = (
        [_clip(f"a{i}.mp4", "films", chosen.folder) for i in range(5)]
        + [_clip(f"b{i}.mp4", "films", other.folder) for i in range(5)]
        # a tagged 9:16 clip that would win on any tag-based path
        + [_clip("vibe.mp4", "hiphop", "street", meta_theme_tags=["night", "city"],
                 meta_mood="minor", meta_color_tone="dark", meta_people_type="none")]
    )
    theme, group = resolve_bucket_slot(chosen.bucket_id, catalog=[])
    raw = resolve_style_rotation(theme, group).subgroups[0]
    pool = fp._build_raw_pool(raw, assets)
    names = {str(row["file_name"]) for row in pool}
    assert names == {f"a{i}.mp4" for i in range(5)}


def test_switching_the_button_switches_the_pool(catalog) -> None:
    # The guarantee the user asked about: picking a different film really picks
    # different footage, not a reshuffle of one shared pool.
    first, second = catalog[0], catalog[1]
    assets = (
        [_clip("a.mp4", "films", first.folder)]
        + [_clip("b.mp4", "films", second.folder)]
    )

    def _pool_for(bucket):
        theme, group = resolve_bucket_slot(bucket.bucket_id, catalog=[])
        raw = resolve_style_rotation(theme, group).subgroups[0]
        return {str(r["file_name"]) for r in fp._build_raw_pool(raw, assets)}

    assert _pool_for(first) == {"a.mp4"}
    assert _pool_for(second) == {"b.mp4"}


def test_a_film_job_never_reaches_the_tagged_pool(catalog) -> None:
    # Collections live in their own inventory, but even handed the tagged pool
    # the folder gate refuses everything that is not the chosen folder.
    tagged_only = [
        _clip("vibe1.mp4", "hiphop", "street", meta_theme_tags=["night"],
              meta_mood="minor", meta_color_tone="dark", meta_people_type="none"),
        _clip("vibe2.mp4", "pop", "sad", meta_theme_tags=["rain"],
              meta_mood="minor", meta_color_tone="dark", meta_people_type="none"),
    ]
    theme, group = resolve_bucket_slot(catalog[0].bucket_id, catalog=[])
    raw = resolve_style_rotation(theme, group).subgroups[0]
    assert fp._build_raw_pool(raw, tagged_only) == []


def test_cyrillic_folders_survive_the_whole_chain(catalog) -> None:
    # The id crosses a process boundary as an env var and comes back as a folder
    # name; a mangled round trip would silently yield an empty pool.
    cyrillic = [b for b in catalog if any(ch.isalpha() and ord(ch) > 127 for ch in b.folder)]
    assert cyrillic, "the film folders are Cyrillic — that is the case worth pinning"
    for bucket in cyrillic:
        theme, group = resolve_bucket_slot(bucket.bucket_id, catalog=[])
        raw = resolve_style_rotation(theme, group).subgroups[0]
        assets = [_clip("x.mp4", "films", bucket.folder)]
        assert len(fp._build_raw_pool(raw, assets)) == 1
