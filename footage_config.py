#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.runtime_mode import MODE_DEV, MODE_PROD, get_runtime_mode


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class FootageAssetRow:
    file_name: str
    file_path: str  # local absolute path OR s3://bucket/key locator
    src_w: int
    src_h: int
    duration_sec: Optional[float]
    meta: Dict[str, Any]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _s3_bucket_assets() -> str:
    """
    Bucket where footage assets live.
    You said: use S3_BUCKET_ASSET_STORAGE.
    """
    return _env("S3_BUCKET_ASSET_STORAGE")


def _s3_prefix() -> str:
    """
    Optional prefix inside the bucket (root by default).
    Keep empty by default.
    """
    p = _env("S3_ASSET_PREFIX", "")
    return p.strip().strip("/")


def _s3_locator_for_filename(file_name: str) -> str:
    """
    Returns s3://bucket/<prefix>/<file_name> (or without prefix)
    """
    bucket = _s3_bucket_assets()
    if not bucket:
        # If S3 is enabled but bucket missing — keep something explicit
        return f"s3://<MISSING_BUCKET>/{file_name}"

    pref = _s3_prefix()
    if pref:
        return f"s3://{bucket}/{pref}/{file_name}"
    return f"s3://{bucket}/{file_name}"


def _read_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_description_json(desc_dir: Path) -> List[Path]:
    if not desc_dir.exists():
        return []
    return [p for p in sorted(desc_dir.rglob("*.json")) if p.is_file()]


def _extract_response_obj(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Some files may have:
      "response": {...}
    or
      "response": [{...}]
    Normalize to a dict.
    """
    r = d.get("response")
    if isinstance(r, dict):
        return r
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    return {}


def _tojson_compact(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _ffprobe_bin() -> str:
    return _env("FFPROBE_BIN", "ffprobe")


def _probe_duration_sec(local_path: Path) -> Optional[float]:
    """
    Read media duration with ffprobe.
    Returns None if probing fails or duration is invalid.
    """
    if not local_path.exists():
        return None
    try:
        cmd = [
            _ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(local_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None
        s = (proc.stdout or "").strip()
        if not s:
            return None
        v = float(s)
        if v <= 0:
            return None
        return v
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------

def build_inventory_and_bundle(
    *,
    repo_root: Path,
    footage_dir: Path,
    descriptions_dir: Path,
    inventory_out_path: Path,
    bundle_out_path: Path,
    adjustment_preset: Optional[Dict[str, Any]] = None,
    max_assets_in_bundle: Optional[int] = None,
) -> Tuple[Path, Path]:
    """
    Build BOTH shared artifacts from the same source (descriptions/*.json):
      1) inventory: contains file_path (local absolute path OR s3:// locator)
      2) bundle: LLM-friendly meta list (no file_path)

    S3 mode:
      - enabled when MODE=prod
      - file_path becomes s3://bucket/prefix/filename
      - we do NOT verify existence (no HEAD, no presign)

    Local mode:
      - file_path becomes (footage_dir / file_name).resolve()
      - we collect missing_files warnings
    """
    repo_root = repo_root.resolve()
    footage_dir = footage_dir.resolve()
    descriptions_dir = descriptions_dir.resolve()
    inventory_out_path = inventory_out_path.resolve()
    bundle_out_path = bundle_out_path.resolve()

    adjustment_preset = adjustment_preset or {
        "id": "ADJ_LAYER_16",
        "name": "Adjustment Layer 16",
        "dump_file": "data/0_4.504505__Adjustment Layer 16__adjustment.json",
        "time_warp_mode": "pin_edges_v1",
    }

    # 1) Parse descriptions -> collect options
    # (file_name, width, height, duration_sec, meta)
    raw_opts: List[Tuple[str, int, int, Optional[float], Dict[str, Any]]] = []

    for jf in _iter_description_json(descriptions_dir):
        d = _read_json(jf)
        if not isinstance(d, dict):
            continue

        resp = _extract_response_obj(d)
        opts = d.get("options") if isinstance(d.get("options"), list) else []

        for opt in opts:
            if not isinstance(opt, dict):
                continue

            fn = str(opt.get("file") or "").strip()
            if not fn:
                continue

            w = opt.get("width")
            h = opt.get("height")
            try:
                w_i = int(w)
                h_i = int(h)
            except Exception:
                continue

            duration_sec: Optional[float] = None
            if opt.get("duration_sec") is not None:
                try:
                    v = float(opt.get("duration_sec"))
                    if v > 0:
                        duration_sec = v
                except Exception:
                    duration_sec = None

            meta = {
                "description_file": str(jf.relative_to(repo_root)) if repo_root in jf.resolve().parents else str(jf),
                "summary": resp.get("summary"),
                "tags": resp.get("tags"),
                "objects": resp.get("objects"),
                "camera": resp.get("camera"),
                "visuals": resp.get("visuals"),
                "composition": resp.get("composition"),
            }

            raw_opts.append((fn, w_i, h_i, duration_sec, meta))

    # 2) Build inventory rows (dedupe by filename)
    runtime_mode = get_runtime_mode()
    mode = "local" if runtime_mode == MODE_DEV else "s3"
    assets_map: Dict[str, FootageAssetRow] = {}
    missing_files_local: List[str] = []

    for fn, w_i, h_i, desc_duration_sec, meta in raw_opts:
        if fn in assets_map:
            continue

        local_fp = (footage_dir / fn).resolve()
        duration_sec = desc_duration_sec
        if duration_sec is None and mode != "s3":
            # In local mode, try to probe as best-effort.
            # In s3 mode, probing local paths is not reliable/expected.
            duration_sec = _probe_duration_sec(local_fp)

        if mode == "s3":
            file_path = _s3_locator_for_filename(fn)
        else:
            file_path = str(local_fp)
            if not local_fp.exists():
                missing_files_local.append(fn)

        assets_map[fn] = FootageAssetRow(
            file_name=fn,
            file_path=file_path,
            src_w=w_i,
            src_h=h_i,
            duration_sec=duration_sec,
            meta=meta,
        )

    assets: List[Dict[str, Any]] = []
    for fn, row in sorted(assets_map.items(), key=lambda kv: kv[0]):
        assets.append(
            {
                "file_name": row.file_name,
                "file_path": row.file_path,
                "src_w": row.src_w,
                "src_h": row.src_h,
                "duration_sec": row.duration_sec,
                "meta": row.meta,
            }
        )

    inv_obj: Dict[str, Any] = {
        "version": "v1",
        "footage_dir": str(footage_dir),
        "descriptions_dir": str(descriptions_dir),
        "adjustment_preset": adjustment_preset,
        "assets": assets,
        "warnings": {
            "assets_count": len(assets),
            "mode": mode,
            "missing_files": sorted(set(missing_files_local)) if mode == "local" else [],
        },
    }

    if mode == "s3":
        inv_obj["warnings"]["s3_bucket"] = _s3_bucket_assets()
        inv_obj["warnings"]["s3_prefix"] = _s3_prefix()

    inventory_out_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_out_path.write_text(json.dumps(inv_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) Build bundle from inventory (always consistent with inventory)
    bundle_rows: List[Dict[str, Any]] = []
    for it in assets:
        fn = str(it.get("file_name") or "").strip()
        sw = int(it.get("src_w") or 0)
        sh = int(it.get("src_h") or 0)
        meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}

        if not fn or sw <= 0 or sh <= 0:
            continue

        row = {
            "file_name": fn,
            "src_w": sw,
            "src_h": sh,
            "duration_sec": it.get("duration_sec"),
            "summary": meta.get("summary"),
            "tags": meta.get("tags"),
            "objects": meta.get("objects"),
            "camera": meta.get("camera"),
            "visuals": meta.get("visuals"),
            "composition": meta.get("composition"),
        }

        cleaned: Dict[str, Any] = {}
        for k, v in row.items():
            if v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            cleaned[k] = v
        bundle_rows.append(cleaned)

    bundle_rows.sort(key=lambda x: str(x.get("file_name", "")))
    if max_assets_in_bundle is not None:
        bundle_rows = bundle_rows[: int(max_assets_in_bundle)]

    bundle_out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_out_path.write_text(_tojson_compact(bundle_rows), encoding="utf-8")

    return inventory_out_path, bundle_out_path


def main() -> None:
    repo_root = Path(__file__).resolve().parent

    # Local docker convention:
    #   ./footage        (mp4 files)
    #   ./descriptions   (json descriptors)
    footage_dir = Path(_env("FOOTAGE_DIR", str(repo_root / "footage"))).resolve()
    desc_dir = Path(_env("FOOTAGE_DESCRIPTIONS_DIR", str(repo_root / "descriptions"))).resolve()

    # Shared outputs (NOT job-scoped!)
    inventory_out = Path(_env("FOOTAGE_INVENTORY_OUT", str(repo_root / "data" / "footage_inventory.json"))).resolve()
    bundle_out = Path(_env("DESCRIPTIONS_BUNDLE_OUT", str(repo_root / "pins" / "descriptions_bundle.json"))).resolve()

    max_assets_env = _env("DESCRIPTIONS_BUNDLE_MAX_ASSETS", "")
    max_assets = int(max_assets_env) if max_assets_env else None

    inv_p, bun_p = build_inventory_and_bundle(
        repo_root=repo_root,
        footage_dir=footage_dir,
        descriptions_dir=desc_dir,
        inventory_out_path=inventory_out,
        bundle_out_path=bundle_out,
        max_assets_in_bundle=max_assets,
    )

    mode = "s3" if get_runtime_mode() == MODE_PROD else "local"
    print(f"[OK] mode:      {mode}")
    print(f"[OK] inventory: {inv_p}")
    print(f"[OK] bundle:    {bun_p}")

    if mode == "s3":
        print(f"[OK] bucket:    {_s3_bucket_assets()}")
        print(f"[OK] prefix:    {_s3_prefix()}")
    else:
        print(f"[OK] footage_dir: {footage_dir}")


if __name__ == "__main__":
    main()
