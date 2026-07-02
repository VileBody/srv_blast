"""Tests for the pure transforms of mlcore/footage_assets_db.py — the Postgres
pool registry. asyncpg I/O is not exercised here (no live DB)."""
from __future__ import annotations

from mlcore import footage_assets_db as adb


def _asset(fn="10000001.mp4", s3_key=None,
           genre="g", tag="t", w=1080, h=1080, dur=5.0, color="dark"):
    # default s3_key tracks the file_name so clip_id derives consistently
    if s3_key is None:
        s3_key = f"pinterest_collection/pins2/{genre}/{tag}/{fn}"
    return {
        "file_name": fn, "s3_key": s3_key, "genre": genre, "tag": tag,
        "src_w": w, "src_h": h, "duration_sec": dur, "dominant_color": color,
    }


def test_build_record_keys_on_clip_id_and_keeps_s3_key():
    rec = adb.build_asset_record(_asset())
    assert rec["clip_id"] == "10000001"
    assert rec["s3_key"] == "pinterest_collection/pins2/g/t/10000001.mp4"
    assert rec["genre"] == "g" and rec["tag"] == "t"
    assert rec["src_w"] == 1080 and rec["duration_sec"] == 5.0
    assert rec["source"] == "video"


def test_build_record_clip_id_from_filename_when_no_s3_key():
    rec = adb.build_asset_record(_asset(s3_key=""))
    assert rec["clip_id"] == "10000001"


def test_build_record_none_without_clip_id():
    assert adb.build_asset_record(_asset(fn="shortname.mp4", s3_key="")) is None
    assert adb.build_asset_record({}) is None
    assert adb.build_asset_record("nope") is None  # type: ignore[arg-type]


def test_records_from_index_dedups_by_clip_id_and_drops_unkeyed():
    assets = [
        _asset("10000001.mp4"),
        _asset("10000001.mp4", genre="other"),   # duplicate clip_id -> last wins
        _asset("10000002.mp4"),
        _asset("noid.mp4", s3_key=""),            # dropped
    ]
    recs = adb.records_from_index(assets, source="video")
    ids = sorted(r["clip_id"] for r in recs)
    assert ids == ["10000001", "10000002"]
    by_id = {r["clip_id"]: r for r in recs}
    assert by_id["10000001"]["genre"] == "other"   # last write wins


def test_index_row_roundtrip_shape():
    rec = adb.build_asset_record(_asset(color="warm"))
    row = adb.index_row_from_record(rec)
    assert row["file_name"] == "10000001.mp4"
    assert row["s3_key"].endswith("10000001.mp4")
    assert row["src_w"] == 1080
    assert row["dominant_color"] == "warm"


def test_clip_id_ignores_8digit_date_in_s3_prefix():
    # the prefix .../pins2_1to1_20260323/... has an 8-digit date; the real clip id
    # is in the basename and must win (else every asset collapses onto "20260323")
    rec = adb.build_asset_record({
        "file_name": "1002332460812809099.mp4",
        "s3_key": "pinterest_collection/pins2_1to1_20260323/hiphop/night/1002332460812809099.mp4",
        "src_w": 1080, "src_h": 1080, "duration_sec": 5.0,
    })
    assert rec["clip_id"] == "1002332460812809099"

    # even if file_name is missing, the s3 BASENAME (not the dated prefix) is used
    rec2 = adb.build_asset_record({
        "file_name": "",
        "s3_key": "pinterest_collection/pins2_1to1_20260323/x/y/1002332460812809099.mp4",
        "src_w": 1080, "src_h": 1080, "duration_sec": 5.0,
    })
    assert rec2["clip_id"] == "1002332460812809099"


def test_numeric_coercion_is_defensive():
    rec = adb.build_asset_record(_asset(w="1080", h=None, dur="5.5"))
    assert rec["src_w"] == 1080 and rec["src_h"] == 0 and rec["duration_sec"] == 5.5
