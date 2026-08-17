"""The inventory must keep one row per uploaded file, not per basename.

Regression test for the real films batch: 939 files across 12 folders, every
folder shipping clip_001.mp4 … clip_0NN.mp4. The inventory builder keyed its asset
map on the basename alone, so the folders overwrote each other and 939 collapsed
to 120 — eleven of twelve collections silently empty, no error anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

FOLDERS = [
    "бойцовский клуб", "брат", "бумер", "великий гэтсби",
    "волк с уолл-стрит", "до встречи с тобой", "дом gucci",
    "дьявол носит прада", "жмурки", "проект х",
    "реквием по мечте", "токийский дрифт",
]
PER_FOLDER = 8


def _index(tmp_path: Path, rows: List[Dict[str, Any]]) -> Path:
    p = tmp_path / "collection_assets_index.json"
    p.write_text(
        json.dumps({"assets_count": len(rows), "assets": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def _batch() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for folder in FOLDERS:
        for i in range(1, PER_FOLDER + 1):
            name = f"clip_{i:03d}.mp4"
            out.append({
                "file_name": name,
                "genre": "films",
                "tag": folder,
                "s3_key": f"collection_sources/films/{folder}/{name}",
                "src_w": 1920,
                "src_h": 1080,
                "duration_sec": 4.0,
            })
    return out


@pytest.fixture()
def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "test-bucket")
    monkeypatch.setenv("S3_COLLECTION_PREFIX", "collection_sources")
    # No S3 in tests; the locator is built from the index, not verified against it.
    monkeypatch.setenv("FOOTAGE_S3_PREFLIGHT_MODE", "off")

    def _run(rows: List[Dict[str, Any]], *, media_type: str = "collection") -> Dict[str, Any]:
        from footage_config import build_inventory_and_bundle

        idx = _index(tmp_path, rows)
        inv_out = tmp_path / "inv.json"
        build_inventory_and_bundle(
            repo_root=Path.cwd(),
            footage_dir=tmp_path / "footage",
            static_assets_index_path=idx,
            inventory_out_path=inv_out,
            bundle_out_path=tmp_path / "bundle.json",
            media_type=media_type,
        )
        return json.loads(inv_out.read_text(encoding="utf-8"))

    return _run


def test_every_uploaded_file_survives_the_inventory(build) -> None:
    inv = build(_batch())
    assert len(inv["assets"]) == len(FOLDERS) * PER_FOLDER


def test_each_folder_keeps_all_of_its_clips(build) -> None:
    inv = build(_batch())
    per_folder: Dict[str, int] = {}
    for a in inv["assets"]:
        per_folder[a["tag"]] = per_folder.get(a["tag"], 0) + 1
    assert per_folder == {f: PER_FOLDER for f in FOLDERS}


def test_identities_are_unique_and_ascii(build) -> None:
    inv = build(_batch())
    names = [a["file_name"] for a in inv["assets"]]
    assert len(set(names)) == len(names)
    for n in names:
        n.encode("ascii")  # AE fails on non-ASCII local paths


def test_locator_points_at_the_real_cyrillic_key(build) -> None:
    # The identity is not a path: the S3 locator must keep the original folder
    # and basename, or the node downloads a 404.
    inv = build(_batch())
    row = next(a for a in inv["assets"] if a["tag"] == "бойцовский клуб")
    assert "бойцовский клуб" in row["file_path"]
    assert row["file_path"].endswith(".mp4")
    assert "clip_00" in row["file_path"]


def test_media_name_is_ascii_and_unique(build) -> None:
    # Two folders' clip_003.mp4 must not land on one local media path.
    inv = build(_batch())
    media = [a["media_file_name"] for a in inv["assets"]]
    assert len(set(media)) == len(media)
    for m in media:
        m.encode("ascii")


def test_genuine_same_folder_duplicate_is_still_dropped(build) -> None:
    rows = _batch()
    rows.append(dict(rows[0]))
    inv = build(rows)
    assert len(inv["assets"]) == len(FOLDERS) * PER_FOLDER


def test_the_escape_hatch_restores_raw_basenames(
    build, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Qualification changes every clip's identity, which invalidates the cooldown
    # ledger; undoing it must not need a deploy.
    monkeypatch.setenv("FOOTAGE_COLLECTION_QUALIFY", "0")
    inv = build(_batch())
    assert len(inv["assets"]) == PER_FOLDER  # the old collapsing behaviour
    assert all("media_file_name" not in a for a in inv["assets"])


def test_the_tagged_pool_is_untouched(build) -> None:
    # media_type=video must behave exactly as before: no qualification, no
    # media_file_name, and the historical basename collapse preserved.
    inv = build(_batch(), media_type="video")
    assert len(inv["assets"]) == PER_FOLDER
    assert all("media_file_name" not in a for a in inv["assets"])
    assert all(a["file_name"].startswith("clip_") for a in inv["assets"])
