from __future__ import annotations

import json
from pathlib import Path

from footage_config import build_inventory_and_bundle


def test_inventory_uses_s3_asset_prefix_in_prod(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/selected")

    src = {
        "assets": [
            {
                "file_name": "clip.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
            }
        ]
    }
    static_index = tmp_path / "static_index.json"
    static_index.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")

    inv_out = tmp_path / "inventory.json"
    bun_out = tmp_path / "bundle.json"

    build_inventory_and_bundle(
        repo_root=tmp_path,
        footage_dir=tmp_path / "footage",
        static_assets_index_path=static_index,
        inventory_out_path=inv_out,
        bundle_out_path=bun_out,
    )

    inv = json.loads(inv_out.read_text(encoding="utf-8"))
    assets = inv["assets"]
    assert len(assets) == 1
    assert (
        assets[0]["file_path"]
        == "s3://bucket/pinterest_collection/selected/Alternative/dark_social_aesthetic/clip.mp4"
    )


def test_inventory_enriches_color_meta_from_fallback_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/selected")

    selected_like = {
        "assets": [
            {
                "file_name": "clip.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
            }
        ]
    }
    selected_path = tmp_path / "selected_index.json"
    selected_path.write_text(json.dumps(selected_like, ensure_ascii=False), encoding="utf-8")

    full_index = {
        "assets": [
            {
                "file_name": "clip.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
                "dominant_color": "H09_L0",
                "palette_bins": [{"bin": "H09_L0", "weight": 1.0}],
            }
        ]
    }
    full_index_path = tmp_path / "static_assets_index.json"
    full_index_path.write_text(json.dumps(full_index, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STATIC_ASSETS_ENRICH_INDEX_JSON", str(full_index_path))

    inv_out = tmp_path / "inventory.json"
    bun_out = tmp_path / "bundle.json"

    build_inventory_and_bundle(
        repo_root=tmp_path,
        footage_dir=tmp_path / "footage",
        static_assets_index_path=selected_path,
        inventory_out_path=inv_out,
        bundle_out_path=bun_out,
    )

    inv = json.loads(inv_out.read_text(encoding="utf-8"))
    row = inv["assets"][0]
    assert row["dominant_color"] == "H09_L0"
    assert row["palette_bins"] == [{"bin": "H09_L0", "weight": 1.0}]
    assert inv["warnings"]["color_meta_enriched_rows"] == 1


def test_inventory_enriches_color_meta_with_normalized_filename_lookup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/selected")

    selected_like = {
        "assets": [
            {
                "file_name": "gecestudio geçecekmi.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
            }
        ]
    }
    selected_path = tmp_path / "selected_index.json"
    selected_path.write_text(json.dumps(selected_like, ensure_ascii=False), encoding="utf-8")

    full_index = {
        "assets": [
            {
                "file_name": "gecestudio geçecekmi.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
                "dominant_color": "H09_L0",
                "palette_bins": [{"bin": "H09_L0", "weight": 1.0}],
            }
        ]
    }
    full_index_path = tmp_path / "static_assets_index.json"
    full_index_path.write_text(json.dumps(full_index, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STATIC_ASSETS_ENRICH_INDEX_JSON", str(full_index_path))

    inv_out = tmp_path / "inventory.json"
    bun_out = tmp_path / "bundle.json"

    build_inventory_and_bundle(
        repo_root=tmp_path,
        footage_dir=tmp_path / "footage",
        static_assets_index_path=selected_path,
        inventory_out_path=inv_out,
        bundle_out_path=bun_out,
    )

    inv = json.loads(inv_out.read_text(encoding="utf-8"))
    row = inv["assets"][0]
    assert row["dominant_color"] == "H09_L0"


def test_inventory_enriches_color_meta_with_copy_suffix_alias(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/selected")

    selected_like = {
        "assets": [
            {
                "file_name": "clip — копия.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
            }
        ]
    }
    selected_path = tmp_path / "selected_index.json"
    selected_path.write_text(json.dumps(selected_like, ensure_ascii=False), encoding="utf-8")

    full_index = {
        "assets": [
            {
                "file_name": "clip.mp4",
                "genre": "Alternative",
                "tag": "dark_social_aesthetic",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.5,
                "dominant_color": "H09_L0",
                "palette_bins": [{"bin": "H09_L0", "weight": 1.0}],
            }
        ]
    }
    full_index_path = tmp_path / "static_assets_index.json"
    full_index_path.write_text(json.dumps(full_index, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STATIC_ASSETS_ENRICH_INDEX_JSON", str(full_index_path))

    inv_out = tmp_path / "inventory.json"
    bun_out = tmp_path / "bundle.json"

    build_inventory_and_bundle(
        repo_root=tmp_path,
        footage_dir=tmp_path / "footage",
        static_assets_index_path=selected_path,
        inventory_out_path=inv_out,
        bundle_out_path=bun_out,
    )

    inv = json.loads(inv_out.read_text(encoding="utf-8"))
    row = inv["assets"][0]
    assert row["dominant_color"] == "H09_L0"
