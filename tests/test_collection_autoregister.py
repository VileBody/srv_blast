# -*- coding: utf-8 -*-
"""An uploaded folder is selectable without anyone editing JSON first.

Three uploads in a row went silently nowhere — films, then four cities, then
seven more — because the bot offered what the REGISTRY listed and a fresh folder
was not in it. Uploading is what makes a group real; the registry is where it
gets a Russian name and the track themes it suits.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore import footage_collection_catalog as cat


@pytest.fixture()
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _write(rows):
        p = tmp_path / "collection_assets_index.json"
        p.write_text(json.dumps({"assets": rows}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("COLLECTION_ASSETS_INDEX_JSON", str(p))
        return p
    return _write


@pytest.fixture()
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _write(rows):
        p = tmp_path / "footage_collections.json"
        p.write_text(json.dumps({"collections": rows}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(p))
        return p
    return _write


def _clip(kind, folder):
    return {"genre": kind, "tag": folder, "file_name": "a.mp4"}


def test_a_folder_nobody_registered_is_still_selectable(index, registry) -> None:
    index([_clip("cine16x9", "Boston"), _clip("cine16x9", "JDM")])
    registry([])
    slugs = {b.slug for b in cat.load_collection_catalog()}
    assert slugs == {"cine16x9__Boston", "cine16x9__JDM"}


def test_the_folder_name_becomes_the_button_text(index, registry) -> None:
    index([_clip("cine16x9", "New_York")])
    registry([])
    assert cat.load_collection_catalog()[0].label == "New York"


def test_the_registry_overrides_what_it_names(index, registry) -> None:
    index([_clip("cine16x9", "Boston"), _clip("cine16x9", "JDM")])
    registry([{
        "kind": "cine16x9", "folder": "Boston", "label": "Бостон",
        "themes": ["hustle_minor"], "description": "кирпич и осень",
    }])
    by_slug = {b.slug: b for b in cat.load_collection_catalog()}
    assert by_slug["cine16x9__Boston"].label == "Бостон"
    assert by_slug["cine16x9__Boston"].themes == ("hustle_minor",)
    # …and the one it does not name keeps its derived identity.
    assert by_slug["cine16x9__JDM"].label == "JDM"
    assert by_slug["cine16x9__JDM"].themes == ()


def test_shared_registry_folders_can_replace_the_node_local_index(index, registry) -> None:
    index([_clip("cine16x9", "Only_On_This_Node")])
    registry([{"kind": "cine16x9", "folder": "Boston", "label": "Бостон"}])

    by_slug = {
        b.slug: b
        for b in cat.load_collection_catalog(
            discovered_folders=[("cine16x9", "Boston"), ("cine16x9", "JDM")]
        )
    }

    assert set(by_slug) == {"cine16x9__Boston", "cine16x9__JDM"}
    assert by_slug["cine16x9__Boston"].label == "Бостон"
    assert by_slug["cine16x9__JDM"].label == "JDM"


def test_the_curated_ones_come_first(index, registry) -> None:
    # Registry order is the editorial order; discovered folders follow it.
    index([_clip("cine16x9", "Zurich"), _clip("cine16x9", "Boston")])
    registry([{"kind": "cine16x9", "folder": "Boston", "label": "Бостон"}])
    assert [b.slug for b in cat.load_collection_catalog()][0] == "cine16x9__Boston"


def test_geometry_follows_the_kind_not_a_guess(index, registry) -> None:
    index([_clip("cine16x9", "Boston"), _clip("films", "dune"), _clip("people", "x")])
    registry([])
    fmt = {b.slug: b.default_format for b in cat.load_collection_catalog()}
    assert fmt["cine16x9__Boston"] == "wide"
    assert fmt["films__dune"] == "vertical"
    assert fmt["people__x"] == "vertical"


def test_a_registered_collection_with_no_files_still_appears(index, registry) -> None:
    # Uploads can lag the decision; the entry is not silently dropped.
    index([])
    registry([{"kind": "films", "folder": "dune", "label": "Дюна"}])
    assert [b.slug for b in cat.load_collection_catalog()] == ["films__dune"]


def test_unknown_kinds_in_the_index_are_ignored(index, registry) -> None:
    # The plane owns three first-level folders; anything else under the prefix is
    # not a collection and must not become a button.
    index([_clip("junk", "whatever"), _clip("films", "dune")])
    registry([])
    assert [b.slug for b in cat.load_collection_catalog()] == ["films__dune"]


def test_a_missing_index_falls_back_to_the_registry(registry, monkeypatch) -> None:
    # The bots mount no data volume; they see only what was committed.
    monkeypatch.setenv("COLLECTION_ASSETS_INDEX_JSON", "/nonexistent/idx.json")
    registry([{"kind": "films", "folder": "dune", "label": "Дюна"}])
    assert [b.slug for b in cat.load_collection_catalog()] == ["films__dune"]


def test_an_unlisted_slug_resolves_from_its_own_name(monkeypatch) -> None:
    # What the bot does with a bucket the orchestrator auto-registered: failing
    # would strand a perfectly good selection.
    monkeypatch.setenv("COLLECTION_ASSETS_INDEX_JSON", "/nonexistent/idx.json")
    b = cat.find_collection("cine16x9__Boston")
    assert (b.kind, b.folder, b.default_format) == ("cine16x9", "Boston", "wide")


def test_a_slug_that_is_not_a_collection_still_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="not resolvable"):
        cat.find_collection("nonsense")
    with pytest.raises(RuntimeError, match="not resolvable"):
        cat.find_collection("junkkind__folder")


def test_a_malformed_registry_is_operator_error_not_an_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "footage_collections.json"
    p.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(p))
    with pytest.raises(RuntimeError, match="not valid JSON"):
        cat.load_collection_catalog()
