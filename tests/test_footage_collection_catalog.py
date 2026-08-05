"""Collection plane: folder-scoped, untagged footage groups.

The point of these tests is not that the happy path works — it is that the plane
stays SEALED in both directions: a collection clip must never reach a tag-based
bucket, and a tag-based clip must never reach a collection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore.footage_collection_catalog import (
    CollectionBucket,
    collections_for_kind,
    evaluate,
    load_collection_catalog,
    load_collection_theme_buckets,
)


def _registry(tmp_path: Path, rows: list) -> Path:
    p = tmp_path / "footage_collections.json"
    p.write_text(json.dumps({"version": "t", "collections": rows}, ensure_ascii=False), encoding="utf-8")
    return p


_ROW = {
    "kind": "films",
    "folder": "interstellar",
    "label": "Интерстеллар",
    "themes": ["escapism_dreams_minor"],
    "formats": ["wide", "square"],
}


# --------------------------------------------------------------------------- #
# registry parsing
# --------------------------------------------------------------------------- #
def test_registry_row_becomes_bucket(tmp_path: Path) -> None:
    (b,) = load_collection_catalog(_registry(tmp_path, [_ROW]))
    assert b.bucket_id == "collection:films__interstellar"
    assert (b.theme, b.tags_group) == ("collection", "films__interstellar")
    assert b.mood == ""
    assert b.formats == ("wide", "square")
    assert b.default_format == "wide"


def test_missing_registry_is_an_empty_plane_not_an_error(tmp_path: Path) -> None:
    # Nothing uploaded yet is a legitimate state (mirrors the photo pool).
    assert load_collection_catalog(tmp_path / "nope.json") == []


def test_formats_default_to_wide_and_square(tmp_path: Path) -> None:
    row = {k: v for k, v in _ROW.items() if k != "formats"}
    (b,) = load_collection_catalog(_registry(tmp_path, [row]))
    assert b.formats == ("wide", "square")


@pytest.mark.parametrize(
    "patch, needle",
    [
        ({"kind": "movies"}, "kind"),
        ({"folder": "films/interstellar"}, "single path segment"),
        ({"label": ""}, "label"),
        ({"formats": ["portrait"]}, "format"),
    ],
)
def test_malformed_row_raises_rather_than_degrading(tmp_path: Path, patch: dict, needle: str) -> None:
    # No Fallback Policy: an operator typo must surface, not silently drop a group.
    with pytest.raises(RuntimeError, match=needle):
        load_collection_catalog(_registry(tmp_path, [{**_ROW, **patch}]))


def test_duplicate_collection_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="duplicate"):
        load_collection_catalog(_registry(tmp_path, [_ROW, dict(_ROW)]))


def test_collections_are_scoped_by_kind(tmp_path: Path) -> None:
    rows = [_ROW, {**_ROW, "kind": "people", "folder": "kubrick", "label": "Кубрик"}]
    cat = load_collection_catalog(_registry(tmp_path, rows))
    assert [b.folder for b in collections_for_kind("films", catalog=cat)] == ["interstellar"]
    assert [b.folder for b in collections_for_kind("people", catalog=cat)] == ["kubrick"]


def test_theme_mapping_is_inverted_from_the_registry(tmp_path: Path) -> None:
    cat = load_collection_catalog(_registry(tmp_path, [_ROW]))
    assert load_collection_theme_buckets(catalog=cat) == {
        "escapism_dreams_minor": ["collection:films__interstellar"]
    }


# --------------------------------------------------------------------------- #
# membership gate
# --------------------------------------------------------------------------- #
_BUCKET = CollectionBucket(
    slug="films__interstellar", label="Интерстеллар", kind="films", folder="interstellar"
)


def test_membership_is_folder_identity_and_ignores_every_semantic_field() -> None:
    # A clip whose tags/people/color would disqualify it from every semantic
    # bucket is still a member — the operator put it in the folder.
    asset = {
        "file_name": "a.mp4",
        "genre": "films",
        "tag": "interstellar",
        "meta_theme_tags": ["text", "watermark", "logo"],
        "meta_people_type": "crowd",
        "meta_color_tone": "warm",
    }
    assert evaluate(_BUCKET, asset) == (True, "eligible")


@pytest.mark.parametrize(
    "asset, stage",
    [
        ({"genre": "films", "tag": "dune"}, "folder"),
        ({"genre": "people", "tag": "interstellar"}, "kind"),
        ({"genre": "", "tag": ""}, "unfiled"),
    ],
)
def test_non_members_are_rejected(asset: dict, stage: str) -> None:
    ok, got = evaluate(_BUCKET, asset)
    assert (ok, got) == (False, stage)


# --------------------------------------------------------------------------- #
# shortlist ranking
# --------------------------------------------------------------------------- #
def test_ranking_keeps_every_collection_despite_having_no_mood(tmp_path: Path) -> None:
    # Collections carry mood="" — under the tag-plane rule a mood filter would
    # drop the whole catalog and strand the shortlist.
    from mlcore.footage_bucket_ranker import rank_buckets

    rows = [
        _ROW,
        {**_ROW, "folder": "drive", "label": "Драйв", "themes": ["night_ride_minor"]},
    ]
    cat = load_collection_catalog(_registry(tmp_path, rows))
    ranked = rank_buckets(lyrics="ночь дорога фары", mood="major", catalog=cat)
    assert sorted(ranked) == [
        "collection:films__drive",
        "collection:films__interstellar",
    ]


def test_collection_without_themes_still_appears(tmp_path: Path) -> None:
    from mlcore.footage_bucket_ranker import rank_buckets

    row = {k: v for k, v in _ROW.items() if k != "themes"}
    cat = load_collection_catalog(_registry(tmp_path, [row]))
    assert rank_buckets(lyrics="что угодно", mood="", catalog=cat) == [
        "collection:films__interstellar"
    ]
