"""Server-side PHOTO tagger: S3 image -> Vision -> footage_tags record.

Photo analogue of mlcore/footage_tagger.py. The only differences vs the video
tagger are structural, not conceptual:
  - the source is a single still image (no ffmpeg frame extraction, no majority
    vote across 3 frames) — one Qwen Vision call per photo
  - records are keyed by photo_clip_id and stamped source='photo'

The taxonomy (color_tone/people_type/theme_tags/mood) is identical
to the video tagger so the photo pool ranks against the SAME buckets.

PURE helpers (parse/shape/untagged-diff) are separated from the I/O layer (Vision
HTTP, S3 download) so the logic is unit-testable without network or a live DB.
The provider plumbing (keys, model, HTTP call, json parse, b64) is reused from
mlcore.footage_tagger to avoid duplication.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from mlcore.footage_tagger import (
    TAGGER_VERSION,
    _encode_image_b64,
    _tag_one_frame,
    build_vision_prompt,
    vision_endpoints,
)
from mlcore.footage_tags_db import (
    SOURCE_PHOTO,
    build_photo_tag_record,
    photo_clip_id,
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

_PHOTO_PROMPT = build_vision_prompt(media_kind="photo")


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O)
# --------------------------------------------------------------------------- #
def select_untagged_photo_keys(s3_keys: Iterable[str], tagged_clip_ids: set) -> List[str]:
    """Photo S3 keys whose photo_clip_id is not yet in the tag store.

    Dedups by photo_clip_id so the same image under several folders is tagged
    once; skips keys that don't resolve to a photo id.
    """
    out: List[str] = []
    seen: set = set()
    for key in s3_keys:
        cid = photo_clip_id(Path(str(key)).name) or photo_clip_id(str(key))
        if not cid or cid in tagged_clip_ids or cid in seen:
            continue
        seen.add(cid)
        out.append(str(key))
    return out


def record_from_photo_result(
    *, s3_key: str, result: Dict[str, Any], tagger: str = TAGGER_VERSION,
    framing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Shape a single Qwen Vision result into a footage_tags record (source=photo)."""
    file_name = Path(str(s3_key)).name
    raw = {
        "file_name": file_name,
        "s3_key": str(s3_key),
        "video_key": file_name,
        "mood": result.get("mood"),
        "color_tone": result.get("color_tone"),
        "people_type": result.get("people_type"),
        "theme_tags": result.get("theme_tags") or [],
        "framing": dict(framing or {}),
    }
    return build_photo_tag_record(raw, tagger=tagger)


# --------------------------------------------------------------------------- #
# I/O layer
# --------------------------------------------------------------------------- #
# Full-resolution phone/DSLR photos make the base64 payload huge (413 errors and
# needless vision tokens). Downscale the longest side before sending — quality is
# more than enough for tagging. Reuses ffmpeg (already in the runtime image), so
# no new Pillow dependency.
_PHOTO_MAX_SIDE = int(os.environ.get("PHOTO_TAG_MAX_SIDE", "1280") or "1280")


def _downscale_photo(src: Path, dst: Path, *, max_side: int = _PHOTO_MAX_SIDE, ffmpeg_bin: str = "") -> Path:
    """Downscale the longer side to <= max_side (never upscale). Returns dst on
    success, else the original src (tagging must not fail just because resize did)."""
    ffmpeg_bin = ffmpeg_bin or os.environ.get("FFMPEG_BIN", "ffmpeg")
    vf = (
        f"scale='if(gte(iw,ih),min({max_side},iw),-2)':"
        f"'if(gte(iw,ih),-2,min({max_side},ih))'"
    )
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(src), "-vf", vf, "-q:v", "3", str(dst)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    return src


def tag_photo_file(path: Path, *, endpoints: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, Any]]:
    """Tag a single still image with Qwen-VL using the same strict taxonomy as video."""
    if endpoints is None:
        endpoints = vision_endpoints()
    if not endpoints:
        raise RuntimeError("no_vision_keys: set DASHSCOPE_API_KEYS (Qwen)")
    with tempfile.TemporaryDirectory(prefix="tagphoto_scale_") as tmp:
        scaled = _downscale_photo(path, Path(tmp) / "small.jpg")
        return _tag_one_frame(_encode_image_b64(scaled), endpoints, _PHOTO_PROMPT)


def tag_photo_from_s3(
    *, bucket: str, s3_key: str, endpoints: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Download an S3 photo, tag it, return a footage_tags record (or None)."""
    from src.storage.s3 import download_from_s3

    with tempfile.TemporaryDirectory(prefix="tagphoto_") as tmp:
        suffix = Path(s3_key).suffix or ".jpg"
        dest = Path(tmp) / f"photo{suffix}"
        download_from_s3(bucket, s3_key, dest)
        result = tag_photo_file(dest, endpoints=endpoints)
        if not result:
            return None
        from mlcore.photo_framing import analyze_photo_framing
        framing = analyze_photo_framing(
            dest,
            theme_tags=result.get("theme_tags") or [],
            people_type=result.get("people_type") or "none",
        )
        from mlcore.photo_quality import attach_photo_quality
        framing = attach_photo_quality(framing, dest)
    return record_from_photo_result(
        s3_key=s3_key, result=result, tagger=TAGGER_VERSION, framing=framing
    )


# --------------------------------------------------------------------------- #
# Framing-only backfill (does not call Qwen and does not alter semantic tags)
# --------------------------------------------------------------------------- #
def run_photo_framing_batch(
    *, bucket: str, db_url: str, flush_every: int = 20, progress_cb=None,
    fetch_fn=None, update_fn=None, analyze_fn=None, quality_fn=None, download_fn=None,
) -> Dict[str, Any]:
    import asyncio as _asyncio
    from mlcore.photo_framing import OpenCvYoloXDetector, analyze_photo_framing

    if download_fn is None:
        from src.storage.s3 import download_from_s3
        download_fn = download_from_s3

    if fetch_fn is None:
        from mlcore.footage_assets_db import init_schema as init_assets_schema
        from mlcore.footage_tags_db import fetch_unframed_photo_records, init_schema
        def fetch_fn():
            async def _go():
                import asyncpg
                conn = await asyncpg.connect(dsn=db_url)
                try:
                    await init_schema(conn)
                    await init_assets_schema(conn)
                    return await fetch_unframed_photo_records(conn)
                finally:
                    await conn.close()
            return _asyncio.run(_go())
    if update_fn is None:
        from mlcore.footage_tags_db import update_photo_framing
        def update_fn(rows):
            async def _go():
                import asyncpg
                conn = await asyncpg.connect(dsn=db_url)
                try:
                    return await update_photo_framing(conn, rows)
                finally:
                    await conn.close()
            return _asyncio.run(_go())

    rows = list(fetch_fn() or [])
    if quality_fn is None and analyze_fn is None:
        from mlcore.photo_quality import attach_photo_quality
        quality_fn = attach_photo_quality
    detector = None
    written = failed = 0
    failure_reasons: Dict[str, int] = {}
    pending: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        try:
            with tempfile.TemporaryDirectory(prefix="framephoto_") as tmp:
                suffix = Path(str(row.get("s3_key") or row.get("file_name") or "photo.jpg")).suffix or ".jpg"
                dest = Path(tmp) / f"photo{suffix}"
                download_fn(bucket, str(row["s3_key"]), dest)
                existing = row.get("framing")
                if quality_fn is not None and isinstance(existing, Mapping) and existing:
                    framing = quality_fn(existing, dest)
                else:
                    if analyze_fn is None and detector is None:
                        detector = OpenCvYoloXDetector(
                            os.environ.get("PHOTO_FRAMING_MODEL_PATH")
                            or "data/models/object_detection_yolox_2022nov.onnx"
                        )
                    framing = (analyze_fn or analyze_photo_framing)(
                        dest,
                        theme_tags=row.get("theme_tags") or [],
                        people_type=row.get("people_type") or "none",
                        **({"detector": detector} if analyze_fn is None else {}),
                    )
                    if quality_fn is not None:
                        framing = quality_fn(framing, dest)
            pending.append({"clip_id": row["clip_id"], "framing": framing})
        except Exception as exc:
            failed += 1
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        if len(pending) >= max(1, flush_every):
            written += update_fn(pending)
            pending = []
        if progress_cb:
            progress_cb(idx, len(rows), written + len(pending))
    if pending:
        written += update_fn(pending)
    ordered_failures = dict(
        sorted(failure_reasons.items(), key=lambda item: (-item[1], item[0]))
    )
    if rows and written <= 0:
        raise RuntimeError(
            "photo framing backfill produced zero rows: "
            f"pending={len(rows)} failed={failed} failure_reasons={ordered_failures}"
        )
    return {
        "framing_pending": len(rows),
        "framing_written": written,
        "framing_failed": failed,
        "framing_failure_reasons": ordered_failures,
    }


# --------------------------------------------------------------------------- #
# Batch runner (used by the Celery task)
# --------------------------------------------------------------------------- #
def run_photo_tagging_batch(
    *,
    bucket: str,
    source_prefix: str,
    db_url: str,
    limit: int = 0,
    flush_every: int = 20,
    progress_cb=None,
    list_keys_fn=None,
    tag_fn=None,
    fetch_tagged_fn=None,
    upsert_fn=None,
) -> Dict[str, Any]:
    """Tag every untagged S3 photo and upsert results into Postgres (source=photo).

    I/O is injectable (list_keys_fn / tag_fn / fetch_tagged_fn / upsert_fn) so the
    orchestration is unit-testable without S3, Qwen, or a DB. In production the
    defaults wire to S3 + Qwen + asyncpg, scoped to the photo pool.
    """
    import asyncio as _asyncio

    endpoints = vision_endpoints()
    providers = [str(e.get("provider") or "") for e in endpoints]

    if list_keys_fn is None:
        def list_keys_fn() -> List[str]:
            from src.storage.s3 import list_s3_objects
            out: List[str] = []
            token = None
            pref = source_prefix.strip("/")
            pref = f"{pref}/" if pref else ""
            while True:
                page = list_s3_objects(bucket, prefix=pref, continuation_token=token, max_keys=1000, delimiter="")
                for obj in page.get("objects") or []:
                    k = str(obj.get("key") or "").strip().lstrip("/")
                    if k and not k.endswith("/") and Path(k).suffix.lower() in _IMAGE_EXTS:
                        out.append(k)
                token = page.get("next_continuation_token")
                if not page.get("is_truncated") or not token:
                    break
            return out

    if fetch_tagged_fn is None:
        def fetch_tagged_fn() -> set:
            from mlcore.footage_tags_db import fetch_tagged_clip_ids, init_schema

            async def _go() -> set:
                import asyncpg  # type: ignore
                conn = await asyncpg.connect(dsn=db_url)
                try:
                    await init_schema(conn)
                    return await fetch_tagged_clip_ids(conn, source=SOURCE_PHOTO)
                finally:
                    await conn.close()
            return _asyncio.run(_go())

    if upsert_fn is None:
        def upsert_fn(records: List[Dict[str, Any]]) -> int:
            from mlcore.footage_tags_db import upsert_records

            async def _go() -> int:
                import asyncpg  # type: ignore
                conn = await asyncpg.connect(dsn=db_url)
                try:
                    return await upsert_records(conn, records)
                finally:
                    await conn.close()
            return _asyncio.run(_go())

    if tag_fn is None:
        def tag_fn(s3_key: str) -> Optional[Dict[str, Any]]:
            return tag_photo_from_s3(bucket=bucket, s3_key=s3_key, endpoints=endpoints)

    tagged_ids = fetch_tagged_fn()
    all_keys = list_keys_fn()
    untagged = select_untagged_photo_keys(all_keys, tagged_ids)
    if limit and limit > 0:
        untagged = untagged[:limit]

    total = len(untagged)
    written = 0
    failed = 0
    pending: List[Dict[str, Any]] = []
    for i, key in enumerate(untagged, start=1):
        try:
            rec = tag_fn(key)
        except Exception:
            rec = None
        if rec:
            pending.append(rec)
        else:
            failed += 1
        if len(pending) >= max(1, flush_every):
            written += upsert_fn(pending)
            pending = []
        if progress_cb:
            progress_cb(i, total, written + len(pending))
    if pending:
        written += upsert_fn(pending)

    return {
        "total_s3": len(all_keys),
        "already_tagged": len(tagged_ids),
        "untagged_processed": total,
        "written": written,
        "failed": failed,
        "providers": providers,
    }
