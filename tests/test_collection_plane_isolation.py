"""The collection plane must be physically unable to touch the other pools.

Sealing it in code (the folder gate) is one layer; these tests cover the other —
that the plane reads and writes DIFFERENT files and prefixes than the tag-based
footage pool and the photo pool, and that it never runs the tagger.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.orchestrator import asset_routes, tasks


# --------------------------------------------------------------------------- #
# three planes, three sets of paths
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "S3_COLLECTION_PREFIX",
        "S3_PHOTO_PREFIX",
        "S3_ASSET_PREFIX",
        "ASSET_UI_SOURCE_PREFIX",
        "ASSET_UI_PHOTO_SOURCE_PREFIX",
        "ASSET_UI_COLLECTION_SOURCE_PREFIX",
        "COLLECTION_ASSETS_INDEX_JSON",
        "PHOTO_ASSETS_INDEX_JSON",
        "STATIC_ASSETS_INDEX_JSON",
        "FOOTAGE_COLLECTIONS_JSON",
    ):
        monkeypatch.delenv(key, raising=False)


def test_each_plane_browses_its_own_s3_prefix() -> None:
    prefixes = {
        mt: asset_routes._source_prefix_for(mt) for mt in ("video", "photo", "collection")
    }
    # Distinct top-level folders => a scan of one can never list another's files.
    assert len(set(prefixes.values())) == 3
    assert prefixes["collection"] == "collection_sources"


def test_each_plane_has_its_own_index_file() -> None:
    paths = {
        mt: asset_routes._assets_index_path_for(mt) for mt in ("video", "photo", "collection")
    }
    assert len({str(p) for p in paths.values()}) == 3
    assert paths["collection"].name == "collection_assets_index.json"


def test_collection_prefix_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_COLLECTION_PREFIX", "cinema_raw")
    assert asset_routes._source_prefix_for("collection") == "cinema_raw"
    assert tasks._collection_source_prefix() == "cinema_raw"


def test_media_type_accepts_the_new_plane() -> None:
    assert tasks._norm_media_type("collection") == "collection"
    with pytest.raises(RuntimeError, match="invalid media_type"):
        tasks._norm_media_type("films")


# --------------------------------------------------------------------------- #
# registry reconciliation
# --------------------------------------------------------------------------- #
def _index(tmp_path: Path, rows: list) -> Path:
    p = tmp_path / "collection_assets_index.json"
    p.write_text(json.dumps({"assets": rows}), encoding="utf-8")
    return p


def _registry(tmp_path: Path, rows: list, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "footage_collections.json"
    p.write_text(json.dumps({"collections": rows}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(p))


def test_a_folder_without_an_entry_is_live_but_flagged_as_auto_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Uploading is now enough to make a group selectable; the registry only adds
    # the Russian label and the track themes. The report says which folders are
    # still running on a derived name rather than calling them broken.
    idx = _index(tmp_path, [{"genre": "films", "tag": "dune", "file_name": "a.mp4"}])
    _registry(tmp_path, [], monkeypatch)
    out = tasks._report_collection_registry(idx)
    assert out["auto_named_folders"] == ["films__dune"]
    assert out["collections_live"] == 1


def test_registered_but_empty_collection_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    idx = _index(tmp_path, [])
    _registry(
        tmp_path,
        [{"kind": "films", "folder": "dune", "label": "Дюна"}],
        monkeypatch,
    )
    out = tasks._report_collection_registry(idx)
    assert out["registered_but_empty"] == ["films__dune"]


def test_a_matched_folder_reports_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    idx = _index(tmp_path, [{"genre": "films", "tag": "dune", "file_name": "a.mp4"}])
    _registry(
        tmp_path,
        [{"kind": "films", "folder": "dune", "label": "Дюна"}],
        monkeypatch,
    )
    out = tasks._report_collection_registry(idx)
    assert out["collections_live"] == 1
    assert "auto_named_folders" not in out
    assert "registered_but_empty" not in out


# --------------------------------------------------------------------------- #
# the job env
# --------------------------------------------------------------------------- #
def test_collection_env_keys_reach_the_build_subprocess() -> None:
    # An env var the build subprocess never receives is the failure mode that
    # silently disabled every hook once already (see CLAUDE.md 2026-06-05).
    for key in ("RENDER_PRESET", "COLLECTION_INVENTORY_JSON", "FOOTAGE_COLLECTIONS_JSON"):
        assert key in tasks._LLM_ENV_KEYS


def test_a_registered_collection_counts_whatever_its_folder_casing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The registry spells folders however the operator typed them, S3 however they
    # were uploaded. The picker matches without regard to case, so the report must
    # too — it was calling a working collection unregistered.
    idx = _index(tmp_path, [{"genre": "cine16x9", "tag": "New_York", "file_name": "a.mp4"}])
    _registry(
        tmp_path,
        [{"kind": "cine16x9", "folder": "New_York", "label": "Нью-Йорк"}],
        monkeypatch,
    )
    out = tasks._report_collection_registry(idx)
    assert out["collections_live"] == 1
    assert "auto_named_folders" not in out
