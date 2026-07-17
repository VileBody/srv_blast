#!/usr/bin/env python3
"""Generate data/photo_assets_index_1to1.json from the S3 PHOTO bucket prefix.

Photo analogue of scripts/build_static_assets_index.py. Photos are the asset pool
for the 4:3 photo flow (parallel to the footage flow). Differences vs the video
indexer:
  - scans image keys (.jpg/.png/...) under S3_PHOTO_PREFIX, not video keys
  - probes width/height only (photos have no playback duration); a nominal
    on-screen duration (PHOTO_DISPLAY_SEC, default 1.5s = the ~36-frame photo
    segment at 23.976fps) is stamped so footage_config.build_inventory_and_bundle
    consumes the index unchanged (it requires a positive duration_sec)

Output shape matches the video index (file_name/genre/tag/src_w/src_h/
duration_sec/dominant_color/palette_bins) so the shared inventory builder needs
no special case beyond the index path + S3 prefix.

Usage:
  S3_BUCKET_ASSET_STORAGE=... S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=... \
  S3_PHOTO_PREFIX=photo_collection/photos_4x3 \
  python scripts/build_photo_assets_index.py [out_path]

Default out_path: data/photo_assets_index_1to1.json
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Reuse the pure key-parsing + color-merge helpers from the video indexer so the
# folder→genre/tag convention stays identical across pools.
from scripts.build_static_assets_index import (  # noqa: E402
    _write_index_atomic,
    load_existing_color_meta,
    parse_key,
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_DEFAULT_OUT = "data/photo_assets_index_1to1.json"


def _photo_display_sec() -> float:
    raw = (os.environ.get("PHOTO_DISPLAY_SEC") or "1.5").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 1.5
    return v if v > 0 else 1.5


def parse_ffprobe_dims(raw: str) -> Optional[Tuple[int, int]]:
    """(width, height) from ffprobe -of json output, or None. Duration ignored —
    photos have none; the index stamps a nominal display duration instead."""
    try:
        data = json.loads(raw)
    except Exception:
        return None
    for s in data.get("streams") or []:
        try:
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
        except Exception:
            w = h = 0
        if w > 0 and h > 0:
            return w, h
    return None


def _ffprobe_dims_url(url: str, *, ffprobe_bin: str = "ffprobe", timeout: float = 60.0) -> Optional[str]:
    try:
        proc = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", url],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
        return proc.stdout if proc.returncode == 0 else None
    except Exception:
        return None


def probe_s3_photo_dims(
    bucket: str,
    key: str,
    *,
    ffprobe_bin: str = "ffprobe",
    timeout: float = 60.0,
) -> Optional[Tuple[int, int]]:
    """Download through the configured S3 client, then probe the local image.

    The photo bucket is private. In production ffprobe cannot reliably consume
    Timeweb presigned URLs, while the boto client used by tagging can download
    the same objects. Keep indexing on that proven transport.
    """
    from src.storage.s3 import download_from_s3

    suffix = Path(key).suffix.lower() or ".img"
    try:
        with tempfile.TemporaryDirectory(prefix="photo_index_") as td:
            local_path = Path(td) / f"photo{suffix}"
            download_from_s3(bucket, key, local_path)
            raw = _ffprobe_dims_url(
                str(local_path), ffprobe_bin=ffprobe_bin, timeout=timeout
            )
            return parse_ffprobe_dims(raw or "")
    except Exception:
        return None


def build_photo_index(
    *,
    bucket: str,
    prefix: str,
    out_path: Path,
    progress_cb=None,
    force_empty: bool = False,
) -> Dict[str, Any]:
    """List S3 photos under prefix, probe dims (no duration), write the photo
    index. Photo analogue of build_static_assets_index.build_index — reusable
    from the activation Celery task. Returns {assets_count, failed, out_path}."""
    prefix = str(prefix or "").strip().strip("/")
    if not bucket:
        raise RuntimeError("S3_BUCKET_ASSET_STORAGE not set")

    from src.storage.s3 import list_s3_objects

    keys: List[str] = []
    token = None
    while True:
        page = list_s3_objects(bucket, prefix=f"{prefix}/", continuation_token=token, max_keys=1000, delimiter="")
        for obj in page.get("objects") or []:
            k = str(obj.get("key") or "").strip().lstrip("/")
            if k and not k.endswith("/") and Path(k).suffix.lower() in _IMAGE_EXTS:
                keys.append(k)
        token = page.get("next_continuation_token")
        if not page.get("is_truncated") or not token:
            break
    print(f"[s3] bucket={bucket} prefix={prefix} photos={len(keys)}")
    if not keys and not force_empty:
        raise RuntimeError(
            "Refusing to replace photo index from an empty S3 listing: "
            f"bucket={bucket!r} prefix={prefix!r}; pass force_empty=True explicitly to override"
        )


    color_meta = load_existing_color_meta(out_path)
    ffprobe_bin = os.environ.get("FFPROBE_BIN", "ffprobe")
    display_sec = round(_photo_display_sec(), 3)

    def _probe(key: str) -> Optional[Dict[str, Any]]:
        parsed = parse_key(key, prefix)
        if not parsed:
            return None
        file_name, genre, tag = parsed
        dims = probe_s3_photo_dims(bucket, key, ffprobe_bin=ffprobe_bin)
        if not dims:
            return None
        w, h = dims
        cm = color_meta.get(file_name, {})
        return {
            "file_name": file_name,
            "genre": genre,
            "tag": tag,
            "src_w": w,
            "src_h": h,
            # Nominal on-screen duration so the shared inventory builder (which
            # validates duration_sec > 0) consumes photo rows unchanged.
            "duration_sec": display_sec,
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
    if keys and not assets:
        raise RuntimeError(
            f"Refusing to replace photo index with zero assets: listed={len(keys)} failed={len(failed)}"
        )


    assets.sort(key=lambda a: (str(a["genre"]).lower(), str(a["tag"]).lower(), str(a["file_name"])))
    obj = {
        "version": "photo-1to1-v1",
        "media_type": "photo",
        "source_root": f"s3://{bucket}/{prefix}",
        "assets_count": len(assets),
        "assets": assets,
    }
    _write_index_atomic(out_path, obj)
    print(f"[done] wrote {len(assets)} photos -> {out_path}  (failed/skipped={len(failed)})")
    if failed:
        print("[warn] first failed keys:", failed[:10])
    if progress_cb:
        progress_cb(len(keys), len(keys))
    return {"assets_count": len(assets), "failed": len(failed), "out_path": str(out_path)}


def main() -> int:
    args = list(sys.argv[1:])
    force_empty = False
    if "--force-empty" in args:
        args.remove("--force-empty")
        force_empty = True
    if len(args) > 1:
        raise SystemExit("usage: build_photo_assets_index.py [out_path] [--force-empty]")
    out_path = Path(args[0] if args else _DEFAULT_OUT)
    bucket = (os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
    prefix = (os.environ.get("S3_PHOTO_PREFIX") or "photo_collection").strip().strip("/")
    if not bucket:
        raise SystemExit("S3_BUCKET_ASSET_STORAGE not set")
    build_photo_index(bucket=bucket, prefix=prefix, out_path=out_path, force_empty=force_empty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
