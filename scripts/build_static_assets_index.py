#!/usr/bin/env python3
"""Generate data/static_assets_index_1to1.json from the S3 footage bucket.

This is the SOURCE OF TRUTH for the selection pool: footage_config.py turns it
into footage_inventory.json, which the picker uses. Until now it was produced by
an external/manual process — this commits it so growing the base is repeatable.

For each video object under the S3 prefix it derives genre/tag from the key path
and probes width/height/duration via ffprobe over a presigned URL (header read,
no full download). Existing dominant_color/palette_bins are preserved by merging
the previous index by file_name (color enrichment is a separate step).

Usage:
  S3_BUCKET_ASSET_STORAGE=... S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=... \
  S3_ASSET_PREFIX=pinterest_collection/pins2_1to1_20260323 \
  python scripts/build_static_assets_index.py [out_path]

Default out_path: data/static_assets_index_1to1.json
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_DEFAULT_OUT = "data/static_assets_index_1to1.json"


def resolve_pool_source_prefix() -> str:
    """The S3 prefix that IS the footage pool — single source of truth.

    Both builders (this script's main() AND the activate_footage_base Celery
    task) MUST use this so a manual index rebuild can never scan a different
    prefix than activation and silently shrink the pool. It mirrors the Asset
    UI browse prefix (asset_routes._asset_ui_source_prefix) so the pool == what
    the UI lists == what tagging scans.

    Priority: ASSET_UI_SOURCE_PREFIX (explicit) > top-level of S3_ASSET_PREFIX
    (e.g. `pinterest_collection` from `pinterest_collection/pins2_1to1_...`) >
    `pinterest_collection`. The top-level split is deliberate: the pool is the
    whole collection, not one dated 1:1 subfolder.
    """
    explicit = (os.environ.get("ASSET_UI_SOURCE_PREFIX") or "").strip().strip("/")
    if explicit:
        return explicit
    s3_prefix = (os.environ.get("S3_ASSET_PREFIX") or "").strip().strip("/")
    if s3_prefix:
        return s3_prefix.split("/", 1)[0]
    return "pinterest_collection"


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O) — unit tested
# --------------------------------------------------------------------------- #
def parse_key(key: str, prefix: str) -> Optional[Tuple[str, str, str]]:
    """(file_name, genre, tag) from an S3 key, or None if it has no folder.

    Mirrors the existing index: 1 folder level -> genre == tag == folder;
    2+ levels -> genre = second-to-last folder, tag = last folder.
    """
    k = str(key or "").strip().lstrip("/")
    pref = str(prefix or "").strip().strip("/")
    if pref and k.startswith(pref + "/"):
        k = k[len(pref) + 1:]
    parts = [p for p in k.split("/") if p]
    if len(parts) < 2:
        return None  # need at least <folder>/<file>
    file_name = parts[-1]
    folders = parts[:-1]
    if len(folders) >= 2:
        genre, tag = folders[-2], folders[-1]
    else:
        genre = tag = folders[-1]
    return file_name, genre, tag


def parse_ffprobe_json(raw: str) -> Optional[Tuple[int, int, float]]:
    """(width, height, duration_sec) from ffprobe -of json output, or None."""
    try:
        data = json.loads(raw)
    except Exception:
        return None
    streams = data.get("streams") or []
    w = h = 0
    for s in streams:
        try:
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
        except Exception:
            w = h = 0
        if w > 0 and h > 0:
            break
    dur = 0.0
    try:
        dur = float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        dur = 0.0
    if w <= 0 or h <= 0 or dur <= 0:
        return None
    return w, h, dur


def load_existing_color_meta(out_path: Path) -> Dict[str, Dict[str, Any]]:
    """Preserve dominant_color/palette_bins from a prior index, keyed by file_name."""
    if not out_path.exists():
        return {}
    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for a in data.get("assets", []) if isinstance(data, dict) else []:
        fn = str(a.get("file_name") or "")
        if not fn:
            continue
        if a.get("dominant_color") or a.get("palette_bins"):
            out[fn] = {
                "dominant_color": a.get("dominant_color"),
                "palette_bins": a.get("palette_bins") or [],
            }
    return out


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def _ffprobe_url(url: str, *, ffprobe_bin: str = "ffprobe", timeout: float = 60.0) -> Optional[str]:
    try:
        proc = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=duration",
             "-of", "json", url],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
        return proc.stdout if proc.returncode == 0 else None
    except Exception:
        return None


def build_index(
    *,
    bucket: str,
    prefix: str,
    out_path: Path,
    progress_cb=None,
) -> Dict[str, Any]:
    """List S3 videos under prefix, ffprobe dims/duration, write the static
    index. Reusable from the activation Celery task. progress_cb(done, total)
    is called periodically. Returns {assets_count, failed, out_path}."""
    prefix = str(prefix or "").strip().strip("/")
    if not bucket:
        raise RuntimeError("S3_BUCKET_ASSET_STORAGE not set")

    from src.storage.s3 import generate_presigned_url, list_s3_objects

    # 1) list video keys under the prefix
    keys: List[str] = []
    token = None
    while True:
        page = list_s3_objects(bucket, prefix=f"{prefix}/", continuation_token=token, max_keys=1000, delimiter="")
        for obj in page.get("objects") or []:
            k = str(obj.get("key") or "").strip().lstrip("/")
            if k and not k.endswith("/") and Path(k).suffix.lower() in _VIDEO_EXTS:
                keys.append(k)
        token = page.get("next_continuation_token")
        if not page.get("is_truncated") or not token:
            break
    print(f"[s3] bucket={bucket} prefix={prefix} videos={len(keys)}")

    color_meta = load_existing_color_meta(out_path)
    ffprobe_bin = os.environ.get("FFPROBE_BIN", "ffprobe")

    def _probe(key: str) -> Optional[Dict[str, Any]]:
        parsed = parse_key(key, prefix)
        if not parsed:
            return None
        file_name, genre, tag = parsed
        try:
            url = generate_presigned_url(bucket, key, expires_in=3600)
        except Exception:
            return None
        raw = _ffprobe_url(url, ffprobe_bin=ffprobe_bin)
        dims = parse_ffprobe_json(raw or "")
        if not dims:
            return None
        w, h, dur = dims
        cm = color_meta.get(file_name, {})
        return {
            "file_name": file_name,
            "genre": genre,
            "tag": tag,
            "src_w": w,
            "src_h": h,
            "duration_sec": round(dur, 3),
            "dominant_color": cm.get("dominant_color"),
            "palette_bins": cm.get("palette_bins") or [],
        }

    assets: List[Dict[str, Any]] = []
    failed: List[str] = []
    max_workers = int(os.environ.get("INDEX_BUILD_WORKERS", "8") or "8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_probe, k): k for k in keys}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            row = fut.result()
            if row:
                assets.append(row)
            else:
                failed.append(futs[fut])
            if done % 100 == 0:
                print(f"[probe] {done}/{len(keys)} ok={len(assets)} failed={len(failed)}")
                if progress_cb:
                    progress_cb(done, len(keys))

    assets.sort(key=lambda a: (str(a["genre"]).lower(), str(a["tag"]).lower(), str(a["file_name"])))
    obj = {
        "version": "1to1-v2",
        "source_root": f"s3://{bucket}/{prefix}",
        "assets_count": len(assets),
        "assets": assets,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {len(assets)} assets -> {out_path}  (failed/skipped={len(failed)})")
    if failed:
        print("[warn] first failed keys:", failed[:10])
    if progress_cb:
        progress_cb(len(keys), len(keys))
    return {"assets_count": len(assets), "failed": len(failed), "out_path": str(out_path)}


def main() -> int:
    out_path = Path(sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_OUT)
    bucket = (os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
    # Use the SHARED pool-prefix resolver so a manual rebuild scans exactly the
    # same prefix as the activation task — never a narrower subfolder that would
    # silently shrink the pool (the old default read the full S3_ASSET_PREFIX).
    prefix = resolve_pool_source_prefix()
    build_index(bucket=bucket, prefix=prefix, out_path=out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
