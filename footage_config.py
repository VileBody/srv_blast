#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_mode import MODE_DEV, MODE_PROD, get_runtime_mode


@dataclass(frozen=True)
class FootageAssetRow:
    file_name: str
    file_path: str
    src_w: int
    src_h: int
    duration_sec: float
    genre: str
    tag: str
    dominant_color: Optional[str]
    palette_bins: Optional[List[Dict[str, Any]]]


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _s3_bucket_assets() -> str:
    bucket = _env("S3_BUCKET_ASSET_STORAGE")
    if get_runtime_mode() == MODE_PROD and not bucket:
        raise RuntimeError("MODE=prod requires S3_BUCKET_ASSET_STORAGE")
    return bucket


def _read_json(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return obj


def _as_pos_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except Exception:
        return None
    if x <= 0:
        return None
    return x


def _s3_locator_for_asset(*, file_name: str, genre: str, tag: str) -> str:
    bucket = _s3_bucket_assets()
    return f"s3://{bucket}/pinterest_collection/{genre}/{tag}/{file_name}"


def _to_compact_json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _clean_palette_bins(v: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(v, list):
        return None
    out: List[Dict[str, Any]] = []
    for it in v:
        if not isinstance(it, dict):
            continue
        b = str(it.get("bin") or "").strip()
        w = _as_pos_float(it.get("weight"))
        if not b or w is None:
            continue
        out.append({"bin": b, "weight": float(w)})
    return out or None


def _require_str(it: Dict[str, Any], key: str) -> str:
    s = str(it.get(key) or "").strip()
    if not s:
        raise RuntimeError(f"Missing required field {key!r} in static assets index row")
    return s


def build_inventory_and_bundle(
    *,
    repo_root: Path,
    footage_dir: Path,
    static_assets_index_path: Path,
    inventory_out_path: Path,
    bundle_out_path: Path,
    adjustment_preset: Optional[Dict[str, Any]] = None,
    max_assets_in_bundle: Optional[int] = None,
) -> Tuple[Path, Path]:
    """
    Build both shared artifacts from static technical index:
      1) inventory: includes deterministic file_path (local path in dev, s3:// in prod)
      2) bundle: technical-only rows for Gemini context

    Source index must be data/static_assets_index.json-like format.
    """
    repo_root = repo_root.resolve()
    footage_dir = footage_dir.resolve()
    static_assets_index_path = static_assets_index_path.resolve()
    inventory_out_path = inventory_out_path.resolve()
    bundle_out_path = bundle_out_path.resolve()

    if not static_assets_index_path.exists():
        raise FileNotFoundError(f"Static assets index missing: {static_assets_index_path}")

    adjustment_preset = adjustment_preset or {
        "id": "ADJ_LAYER_16",
        "name": "Adjustment Layer 16",
        "dump_file": "data/0_4.504505__Adjustment Layer 16__adjustment.json",
        "time_warp_mode": "pin_edges_v1",
    }

    index_obj = _read_json(static_assets_index_path)
    source_assets = index_obj.get("assets")
    if not isinstance(source_assets, list):
        raise RuntimeError(f"Invalid static assets index (missing assets[]): {static_assets_index_path}")

    runtime_mode = get_runtime_mode()
    mode = "local" if runtime_mode == MODE_DEV else "s3"

    assets_map: Dict[str, FootageAssetRow] = {}
    invalid_rows = 0
    missing_local_files: List[str] = []

    for it in source_assets:
        if not isinstance(it, dict):
            invalid_rows += 1
            continue

        try:
            file_name = _require_str(it, "file_name")
            genre = _require_str(it, "genre")
            tag = _require_str(it, "tag")
            src_w = int(it.get("src_w") or 0)
            src_h = int(it.get("src_h") or 0)
            duration_sec = _as_pos_float(it.get("duration_sec"))
            if src_w <= 0 or src_h <= 0 or duration_sec is None:
                raise RuntimeError("Invalid src size or duration_sec")
        except Exception:
            invalid_rows += 1
            continue

        if file_name in assets_map:
            continue

        if mode == "s3":
            file_path = _s3_locator_for_asset(file_name=file_name, genre=genre, tag=tag)
        else:
            local_fp = (footage_dir / file_name).resolve()
            file_path = str(local_fp)
            if not local_fp.exists():
                missing_local_files.append(file_name)

        dominant_color = str(it.get("dominant_color") or "").strip() or None
        palette_bins = _clean_palette_bins(it.get("palette_bins"))

        assets_map[file_name] = FootageAssetRow(
            file_name=file_name,
            file_path=file_path,
            src_w=src_w,
            src_h=src_h,
            duration_sec=float(duration_sec),
            genre=genre,
            tag=tag,
            dominant_color=dominant_color,
            palette_bins=palette_bins,
        )

    assets: List[Dict[str, Any]] = []
    for file_name, row in sorted(assets_map.items(), key=lambda kv: kv[0]):
        obj: Dict[str, Any] = {
            "file_name": row.file_name,
            "file_path": row.file_path,
            "src_w": row.src_w,
            "src_h": row.src_h,
            "duration_sec": row.duration_sec,
            "genre": row.genre,
            "tag": row.tag,
        }
        if row.dominant_color:
            obj["dominant_color"] = row.dominant_color
        if row.palette_bins:
            obj["palette_bins"] = row.palette_bins
        assets.append(obj)

    inv_obj: Dict[str, Any] = {
        "version": "v2",
        "source_static_assets_index": str(static_assets_index_path),
        "footage_dir": str(footage_dir),
        "adjustment_preset": adjustment_preset,
        "assets": assets,
        "warnings": {
            "mode": mode,
            "assets_count": len(assets),
            "invalid_rows": int(invalid_rows),
            "missing_files": sorted(set(missing_local_files)) if mode == "local" else [],
        },
    }
    if mode == "s3":
        inv_obj["warnings"]["s3_bucket"] = _s3_bucket_assets()

    inventory_out_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_out_path.write_text(json.dumps(inv_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    bundle_rows: List[Dict[str, Any]] = []
    for it in assets:
        row: Dict[str, Any] = {
            "file_name": it["file_name"],
            "src_w": int(it["src_w"]),
            "src_h": int(it["src_h"]),
            "duration_sec": float(it["duration_sec"]),
            "genre": str(it["genre"]),
            "tag": str(it["tag"]),
        }
        if it.get("dominant_color"):
            row["dominant_color"] = it["dominant_color"]
        if it.get("palette_bins"):
            row["palette_bins"] = it["palette_bins"]
        bundle_rows.append(row)

    bundle_rows.sort(key=lambda x: str(x.get("file_name", "")))
    if max_assets_in_bundle is not None:
        bundle_rows = bundle_rows[: int(max_assets_in_bundle)]

    bundle_out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_out_path.write_text(_to_compact_json(bundle_rows), encoding="utf-8")
    return inventory_out_path, bundle_out_path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    footage_dir = Path(_env("FOOTAGE_DIR", str(repo_root / "footage"))).resolve()
    static_assets_index_path = Path(
        _env("STATIC_ASSETS_INDEX_JSON", str(repo_root / "data" / "static_assets_index.json"))
    ).resolve()

    inventory_out = Path(
        _env("FOOTAGE_INVENTORY_OUT", str(repo_root / "data" / "footage_inventory.json"))
    ).resolve()
    bundle_out = Path(
        _env("DESCRIPTIONS_BUNDLE_OUT", str(repo_root / "pins" / "descriptions_bundle.json"))
    ).resolve()

    max_assets_env = _env("DESCRIPTIONS_BUNDLE_MAX_ASSETS", "")
    max_assets = int(max_assets_env) if max_assets_env else None

    inv_p, bun_p = build_inventory_and_bundle(
        repo_root=repo_root,
        footage_dir=footage_dir,
        static_assets_index_path=static_assets_index_path,
        inventory_out_path=inventory_out,
        bundle_out_path=bundle_out,
        max_assets_in_bundle=max_assets,
    )

    mode = "s3" if get_runtime_mode() == MODE_PROD else "local"
    print(f"[OK] mode: {mode}")
    print(f"[OK] static index: {static_assets_index_path}")
    print(f"[OK] inventory: {inv_p}")
    print(f"[OK] bundle: {bun_p}")


if __name__ == "__main__":
    main()
