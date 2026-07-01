"""Tests for the pure funnel of scripts/footage_pool_reconcile.py — the footage
pool "sources of truth" reconciliation. No S3/DB I/O.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "footage_pool_reconcile", _ROOT / "scripts" / "footage_pool_reconcile.py"
)
rec = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rec)  # type: ignore[union-attr]


def _asset(fn, *, w=1080, h=1080, dur=5.0):
    return {"file_name": fn, "genre": "g", "tag": "t", "src_w": w, "src_h": h, "duration_sec": dur}


def test_funnel_drops_at_each_stage():
    index = [
        _asset("10000001.mp4"),                    # ok -> pickable
        _asset("10000002.mp4"),                    # ok but untagged
        _asset("10000003.mp4", w=0),               # invalid dims
        _asset("shortname.mp4"),                   # no 8+ digit clip_id
        _asset("10000001.mp4"),                    # duplicate file_name
    ]
    tagged = {"10000001"}  # only clip 1 is tagged
    report = rec.reconcile(index_assets=index, tagged_clip_ids=tagged)

    t = report["totals"]
    assert t["index_rows"] == 5
    assert t["index_unique"] == 4          # duplicate collapsed
    assert t["pickable"] == 1              # only 10000001

    stages = {s["stage"]: s for s in report["stages"]}
    assert stages["static_index -> valid_dims+unique"]["dropped"] == 2   # invalid dims + dup
    assert stages["valid -> has_clip_id"]["dropped"] == 1                # shortname
    assert stages["has_clip_id -> tagged(pickable)"]["dropped"] == 1     # 10000002 untagged
    assert "shortname.mp4" in stages["valid -> has_clip_id"]["dropped_sample"]


def test_s3_axis_reports_index_gap():
    index = [_asset("10000001.mp4")]
    tagged = {"10000001"}
    s3 = {"10000001.mp4", "10000002.mp4", "10000003.mp4"}  # S3 has 2 the index lost
    report = rec.reconcile(index_assets=index, tagged_clip_ids=tagged, s3_file_names=s3)
    assert report["totals"]["s3_objects"] == 3
    s3_stage = report["stages"][0]
    assert s3_stage["stage"] == "s3_listing -> static_index"
    assert s3_stage["dropped"] == 2
    assert set(s3_stage["dropped_sample"]) == {"10000002.mp4", "10000003.mp4"}


def test_prefix_collision_warning_narrow_vs_broad(monkeypatch):
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/pins2_1to1_20260323")
    # index built from the NARROW prefix -> warn
    warn = rec.prefix_collision_warning("pinterest_collection/pins2_1to1_20260323")
    assert warn and "narrow" in warn.lower()
    # index built from the BROAD prefix -> no warning
    assert rec.prefix_collision_warning("pinterest_collection") is None


def test_prefix_collision_no_env(monkeypatch):
    monkeypatch.delenv("S3_ASSET_PREFIX", raising=False)
    assert rec.prefix_collision_warning("anything") is None
