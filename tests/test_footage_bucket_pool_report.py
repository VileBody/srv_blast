"""Tests for the pure pieces of scripts/footage_bucket_pool_report.py — the
per-bucket pool sizing diagnostic. No S3/DB/AE I/O: synthetic mapped assets +
in-memory buckets. Verifies mood pre-filter, tag-overlap sizing, and the
metadata-source precedence (cli > env > legacy).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mlcore.footage_bucket_catalog import Bucket

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "footage_bucket_pool_report", _ROOT / "scripts" / "footage_bucket_pool_report.py"
)
rep = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rep)  # type: ignore[union-attr]


def _bucket(theme="romance_major", group="nature_sunset", mood="major",
            tags=("sunset", "beach", "ocean"), excl=(), color=("warm", "light")) -> Bucket:
    return Bucket(
        bucket_id=f"{theme}:{group}",
        theme=theme,
        tags_group=group,
        mood=mood,
        priority_tags=list(tags),
        exclude_tags=list(excl),
        color=list(color),
        theme_label="",
        subtheme_label="",
    )


def _asset(fn, tags, *, mood="major", color="warm", people="none", genre="g", tag="t"):
    return {
        "file_name": fn,
        "genre": genre,
        "tag": tag,
        "duration_sec": 5.0,
        "meta_mood": mood,
        "meta_theme_tags": list(tags),
        "meta_color_tone": color,
        "meta_people_type": people,
    }


def _mapped_by_mood(assets):
    return {
        "major": rep._mood_pool(assets, "major"),
        "minor": rep._mood_pool(assets, "minor"),
        "": list(assets),
    }


def test_pool_size_counts_only_matching_mood_and_overlap():
    b = _bucket()
    assets = [
        _asset("1.mp4", ["sunset", "beach", "ocean"], mood="major"),   # counts (overlap 3)
        _asset("2.mp4", ["sunset"], mood="major"),                     # counts (overlap 1)
        _asset("3.mp4", ["sunset", "beach"], mood="minor"),            # wrong mood -> out
        _asset("4.mp4", ["city", "night"], mood="major"),              # overlap 0 -> out
    ]
    row = rep.size_bucket(b, _mapped_by_mood(assets))
    assert row["pool_size"] == 2
    assert row["mood_candidates"] == 3  # three major-mood assets before tag scoring


def test_exclude_tags_shrink_pool():
    b = _bucket(excl=("beach",))
    assets = [
        _asset("1.mp4", ["sunset", "beach"], mood="major"),   # excluded by 'beach'
        _asset("2.mp4", ["sunset", "ocean"], mood="major"),   # counts
    ]
    row = rep.size_bucket(b, _mapped_by_mood(assets))
    assert row["pool_size"] == 1


def test_color_hits_track_color_bonus():
    b = _bucket(color=("warm",))
    assets = [
        _asset("1.mp4", ["sunset"], mood="major", color="warm"),   # +0.5 color
        _asset("2.mp4", ["sunset"], mood="major", color="dark"),   # no color bonus
    ]
    row = rep.size_bucket(b, _mapped_by_mood(assets))
    assert row["pool_size"] == 2
    assert row["color_hits"] == 1


def test_resolve_metadata_paths_precedence(monkeypatch, tmp_path):
    # cli wins over env
    monkeypatch.setenv("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", json.dumps(["/env/a.json"]))
    paths, src = rep.resolve_metadata_paths(["/cli/x.json"])
    assert [Path(p) for p in paths] == [Path("/cli/x.json")] and src == "cli"

    # env used when no cli
    paths, src = rep.resolve_metadata_paths([])
    assert [Path(p) for p in paths] == [Path("/env/a.json")]
    assert "env" in src

    # legacy fallback when neither
    monkeypatch.delenv("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", raising=False)
    paths, src = rep.resolve_metadata_paths([])
    assert src == "legacy video_database JSONs"


def test_build_report_shape(tmp_path):
    # clip ids must be 8+ digits to match footage_picker._CLIP_ID_RE
    inv = [
        {"file_name": "10000001.mp4", "genre": "g", "tag": "t", "duration_sec": 4.0},
        {"file_name": "10000002.mp4", "genre": "g", "tag": "t", "duration_sec": 4.0},
    ]
    inv_path = tmp_path / "inv.json"
    inv_path.write_text(json.dumps({"assets_count": 2, "assets": inv}), encoding="utf-8")
    meta = [
        {"video_key": "x__10000001", "mood": "minor", "color_tone": "dark",
         "people_type": "none", "theme_tags": ["fog", "dark forest"]},
        {"video_key": "x__10000002", "mood": "minor", "color_tone": "dark",
         "people_type": "none", "theme_tags": ["night", "shadows"]},
    ]
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    report = rep.build_report(
        inventory_path=inv_path,
        metadata_paths=[meta_path],
        all_buckets=False,
        thin=15,
        fat=120,
    )
    assert report["inventory"]["assets"] == 2
    assert report["inventory"]["mapped_to_tags"] == 2
    assert report["buckets"]["counted"] >= 1
    assert all("pool_size" in r for r in report["rows"])
    # rows sorted ascending by pool size
    sizes = [r["pool_size"] for r in report["rows"]]
    assert sizes == sorted(sizes)
