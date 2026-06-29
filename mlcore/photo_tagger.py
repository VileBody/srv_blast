"""Server-side PHOTO tagger: S3 image -> Groq Vision -> footage_tags record.

Photo analogue of mlcore/footage_tagger.py. The only differences vs the video
tagger are structural, not conceptual:
  - the source is a single still image (no ffmpeg frame extraction, no majority
    vote across 3 frames) — one Groq Vision call per photo
  - records are keyed by photo_clip_id and stamped source='photo'

The taxonomy (color_tone/energy/scene/people_type/theme_tags/mood) is identical
to the video tagger so the photo pool ranks against the SAME buckets.

PURE helpers (parse/shape/untagged-diff) are separated from the I/O layer (Groq
HTTP, S3 download) so the logic is unit-testable without network or a live DB.
The Groq plumbing (keys, model, HTTP call, json parse, b64) is reused from
mlcore.footage_tagger to avoid duplication.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mlcore.footage_tagger import (
    call_groq_vision,
    groq_api_keys,
    groq_model,
    _encode_image_b64,
)
from mlcore.footage_tags_db import (
    SOURCE_PHOTO,
    build_photo_tag_record,
    photo_clip_id,
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

_PHOTO_PROMPT = """Analyze this photo and return ONLY valid JSON, no markdown, no extra text.

{
  "color_tone": "dark | light | warm | cold | neutral",
  "energy": "calm | dynamic | aggressive",
  "scene": "street | interior | nature | garage | track | city",
  "has_people": true or false,
  "people_type": "none | girls | guys | couple | crowd | driver",
  "theme_tags": ["2-4 short english tags describing what is in the photo"],
  "mood": "minor | major"
}

Rules:
- color_tone: dark=night/shadows, light=bright daylight, warm=sunset/orange/gold, cold=blue/grey/rain, neutral=mixed
- energy: calm=slow/static, dynamic=movement/speed, aggressive=chaos/burnout/fight
- scene: pick the single best match
- people_type: if no people -> "none". If mixed -> pick dominant group
- theme_tags: specific, e.g. ["night drift", "wet road", "neon lights"]
- mood: overall emotional feel of the photo
"""


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
    *, s3_key: str, result: Dict[str, Any], tagger: str = "groq",
) -> Optional[Dict[str, Any]]:
    """Shape a single Groq Vision result into a footage_tags record (source=photo)."""
    file_name = Path(str(s3_key)).name
    raw = {
        "file_name": file_name,
        "s3_key": str(s3_key),
        "video_key": file_name,
        "mood": result.get("mood"),
        "color_tone": result.get("color_tone"),
        "people_type": result.get("people_type"),
        "theme_tags": result.get("theme_tags") or [],
    }
    return build_photo_tag_record(raw, tagger=tagger)


# --------------------------------------------------------------------------- #
# I/O layer
# --------------------------------------------------------------------------- #
def tag_photo_file(path: Path, *, keys: List[str], model: str) -> Optional[Dict[str, Any]]:
    """One Groq Vision call on a single still image -> parsed taxonomy dict."""
    if not keys:
        raise RuntimeError("No Groq API keys configured (set GROQ_API_KEYS or GROQ_API_KEY)")
    return call_groq_vision(
        _encode_image_b64(path), api_key=keys[0], model=model, prompt=_PHOTO_PROMPT,
    )


def tag_photo_from_s3(*, bucket: str, s3_key: str, keys: List[str], model: str) -> Optional[Dict[str, Any]]:
    """Download an S3 photo, tag it, return a footage_tags record (or None)."""
    from src.storage.s3 import download_from_s3

    with tempfile.TemporaryDirectory(prefix="tagphoto_") as tmp:
        suffix = Path(s3_key).suffix or ".jpg"
        dest = Path(tmp) / f"photo{suffix}"
        download_from_s3(bucket, s3_key, dest)
        result = tag_photo_file(dest, keys=keys, model=model)
    if not result:
        return None
    return record_from_photo_result(s3_key=s3_key, result=result, tagger="groq")


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
    orchestration is unit-testable without S3, Groq, or a DB. In production the
    defaults wire to S3 + Groq + asyncpg, scoped to the photo pool.
    """
    import asyncio as _asyncio

    keys = groq_api_keys()
    model = groq_model()

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
            return tag_photo_from_s3(bucket=bucket, s3_key=s3_key, keys=keys, model=model)

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
    }
