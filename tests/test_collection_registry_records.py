"""Collection rows must reach the durable registry, and survive the round trip.

Two failures this covers, both observed on the real activation run:

  activate: pool registry upsert failed (non-fatal): RuntimeError(
      "registry_empty_candidate_guard: refusing to prune source registry
       source='collection' current=0 candidate=0")

Every record was dropped because the shared clip_id extractor wants 8+ consecutive
digits and the delivered files are ``clip_003.mp4``.

The second is latent: a node that rebuilds its inventory from this registry needs
the scene cuts, or it places segment boundaries on the even grid while the
ingesting node placed them on edits — different boundaries, different clip
identities, different footage picked for one and the same job.
"""
from __future__ import annotations

from typing import Any, Dict, List

from mlcore.footage_assets_db import (
    build_asset_record,
    index_row_from_record,
    records_from_index,
    scene_cuts_from_csv,
)

FOLDERS = ["бойцовский клуб", "брат", "бумер"]


def _row(folder: str, name: str = "clip_003.mp4", **extra: Any) -> Dict[str, Any]:
    return {
        "file_name": name,
        "genre": "films",
        "tag": folder,
        "s3_key": f"collection_sources/films/{folder}/{name}",
        "src_w": 1920,
        "src_h": 1080,
        "duration_sec": 4.0,
        **extra,
    }


def test_records_survive_names_without_a_digit_run() -> None:
    recs = records_from_index([_row(f) for f in FOLDERS], source="collection")
    assert len(recs) == len(FOLDERS)


def test_records_stay_distinct_per_folder() -> None:
    recs = records_from_index([_row(f) for f in FOLDERS], source="collection")
    assert len({r["clip_id"] for r in recs}) == len(FOLDERS)


def test_the_video_pool_keeps_its_digit_rule() -> None:
    # Whatever this fixes for collections must not loosen the tagged pool's key.
    assert records_from_index([_row(f) for f in FOLDERS], source="video") == []
    numeric = _row("x", name="100275529199764783.mp4")
    assert len(records_from_index([numeric], source="video")) == 1


def test_scene_cuts_round_trip_through_the_registry() -> None:
    rec = build_asset_record(
        _row("бумер", duration_sec=180.0, scene_cuts=[12.5, 40.0, 91.25]),
        source="collection",
    )
    assert rec is not None
    assert rec["scene_cuts"] == "12.5,40,91.25"
    back = index_row_from_record(rec)
    assert back["scene_cuts"] == [12.5, 40.0, 91.25]


def test_short_clips_carry_no_cuts_and_no_key() -> None:
    rec = build_asset_record(_row("брат"), source="collection")
    assert rec is not None
    assert rec["scene_cuts"] == ""
    assert "scene_cuts" not in index_row_from_record(rec)


def test_csv_parser_tolerates_junk() -> None:
    assert scene_cuts_from_csv("") == []
    assert scene_cuts_from_csv(None) == []
    assert scene_cuts_from_csv("1.5,,oops,3") == [1.5, 3.0]


def test_index_round_trip_preserves_what_identity_depends_on() -> None:
    # The rehydrated index must re-derive the SAME qualified identity, so it has
    # to carry the raw basename plus genre/tag — not the qualified name.
    rows: List[Dict[str, Any]] = [_row(f) for f in FOLDERS]
    recs = records_from_index(rows, source="collection")
    back = [index_row_from_record(r) for r in recs]
    assert {b["file_name"] for b in back} == {"clip_003.mp4"}
    assert {b["tag"] for b in back} == set(FOLDERS)
    assert all(b["genre"] == "films" for b in back)
