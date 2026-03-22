from __future__ import annotations

import json
from pathlib import Path

import pytest

import footage_config
from footage_config import build_inventory_and_bundle


def _write_min_index(path: Path) -> None:
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
    path.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")


def test_s3_preflight_strict_raises_on_missing_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/selected")
    monkeypatch.setenv("FOOTAGE_S3_PREFLIGHT_MODE", "strict")

    static_index = tmp_path / "selected_index.json"
    _write_min_index(static_index)
    inv_out = tmp_path / "inventory.json"
    bun_out = tmp_path / "bundle.json"

    def _fake_missing(rows):
        assert rows, "strict preflight must pass rows to checker"
        return rows

    monkeypatch.setattr(footage_config, "_s3_missing_asset_rows", _fake_missing)

    with pytest.raises(RuntimeError, match="missing_s3_assets_in_selected_source"):
        build_inventory_and_bundle(
            repo_root=tmp_path,
            footage_dir=tmp_path / "footage",
            static_assets_index_path=static_index,
            inventory_out_path=inv_out,
            bundle_out_path=bun_out,
        )


def test_s3_preflight_strict_marks_warning_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/selected")
    monkeypatch.setenv("FOOTAGE_S3_PREFLIGHT_MODE", "strict")

    static_index = tmp_path / "selected_index.json"
    _write_min_index(static_index)
    inv_out = tmp_path / "inventory.json"
    bun_out = tmp_path / "bundle.json"

    monkeypatch.setattr(footage_config, "_s3_missing_asset_rows", lambda _rows: [])

    build_inventory_and_bundle(
        repo_root=tmp_path,
        footage_dir=tmp_path / "footage",
        static_assets_index_path=static_index,
        inventory_out_path=inv_out,
        bundle_out_path=bun_out,
    )

    inv = json.loads(inv_out.read_text(encoding="utf-8"))
    assert inv["warnings"]["s3_preflight_mode"] == "strict"

