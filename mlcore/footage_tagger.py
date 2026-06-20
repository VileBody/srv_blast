"""Server-side footage tagger: S3 clip -> 3 ffmpeg frames -> Groq Vision -> record.

Ported from the offline pin/scan.py pipeline, adapted for the server:
  - source is an S3 object (downloaded), not a local file
  - frames extracted with ffmpeg/ffprobe (already in the runtime image), no cv2
  - Groq only (Gemini dropped); API keys come from env, never hardcoded
  - output is a footage_tags record (see footage_tags_db.build_tag_record)

PURE helpers (parse / vote / untagged-diff / record shaping) are separated from
the I/O layer (ffmpeg subprocess, Groq HTTP, S3 download) so the logic is unit
testable without network, ffmpeg, or a live DB.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mlcore.footage_tags_db import build_tag_record, extract_clip_id

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_PROMPT = """Analyze this video frame and return ONLY valid JSON, no markdown, no extra text.

{
  "color_tone": "dark | light | warm | cold | neutral",
  "energy": "calm | dynamic | aggressive",
  "scene": "street | interior | nature | garage | track | city",
  "has_people": true or false,
  "people_type": "none | girls | guys | couple | crowd | driver",
  "theme_tags": ["2-4 short english tags describing what is happening"],
  "mood": "minor | major"
}

Rules:
- color_tone: dark=night/shadows, light=bright daylight, warm=sunset/orange/gold, cold=blue/grey/rain, neutral=mixed
- energy: calm=slow/static, dynamic=movement/speed, aggressive=chaos/burnout/fight
- scene: pick the single best match
- people_type: if no people -> "none". If mixed -> pick dominant group
- theme_tags: specific, e.g. ["night drift", "wet road", "neon lights"]
- mood: overall emotional feel of the frame
"""


# --------------------------------------------------------------------------- #
# Config (env)
# --------------------------------------------------------------------------- #
def groq_api_keys() -> List[str]:
    """Groq keys from env. Prefer GROQ_API_KEYS (comma-separated), else GROQ_API_KEY."""
    multi = (os.environ.get("GROQ_API_KEYS") or "").strip()
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]
    single = (os.environ.get("GROQ_API_KEY") or "").strip()
    return [single] if single else []


def groq_model() -> str:
    return (os.environ.get("GROQ_VISION_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct").strip()


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O)
# --------------------------------------------------------------------------- #
def parse_groq_json(raw: str) -> Optional[Dict[str, Any]]:
    """Parse a model JSON reply, tolerating ```json fences."""
    s = str(raw or "").strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.startswith("json"):
                s = s[4:]
            s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def merge_frame_votes(frames: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Majority vote across per-frame results; theme_tags unioned (top 8)."""
    def vote(field: str) -> str:
        vals = [str(f[field]) for f in frames if field in f and f[field] is not None]
        return Counter(vals).most_common(1)[0][0] if vals else ""

    all_tags: List[str] = []
    for f in frames:
        all_tags.extend(f.get("theme_tags") or [])
    unique_tags = list(dict.fromkeys(all_tags))[:8]

    return {
        "color_tone": vote("color_tone"),
        "energy": vote("energy"),
        "scene": vote("scene"),
        "has_people": sum(1 for f in frames if f.get("has_people")) >= 2,
        "people_type": vote("people_type"),
        "theme_tags": unique_tags,
        "mood": vote("mood"),
    }


def select_untagged_keys(s3_keys: Iterable[str], tagged_clip_ids: set) -> List[str]:
    """S3 keys whose clip_id is not yet present in the tag store.

    Skips keys without an extractable clip_id (cannot be keyed/tagged) and
    dedups by clip_id so the same physical clip in several genre folders is
    tagged once.
    """
    out: List[str] = []
    seen: set = set()
    for key in s3_keys:
        cid = extract_clip_id(Path(str(key)).name) or extract_clip_id(str(key))
        if not cid or cid in tagged_clip_ids or cid in seen:
            continue
        seen.add(cid)
        out.append(str(key))
    return out


def record_from_votes(*, s3_key: str, votes: Dict[str, Any], tagger: str = "groq") -> Optional[Dict[str, Any]]:
    """Shape merged votes into a footage_tags record (clip_id-keyed, normalized)."""
    file_name = Path(str(s3_key)).name
    raw = {
        "video_key": file_name,
        "file_name": file_name,
        "s3_key": str(s3_key),
        "mood": votes.get("mood"),
        "color_tone": votes.get("color_tone"),
        "people_type": votes.get("people_type"),
        "theme_tags": votes.get("theme_tags") or [],
    }
    return build_tag_record(raw, tagger=tagger)


# --------------------------------------------------------------------------- #
# I/O layer
# --------------------------------------------------------------------------- #
def _ffprobe_duration_sec(path: Path, *, ffprobe_bin: str) -> Optional[float]:
    try:
        proc = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            return None
        v = float(str(proc.stdout or "").strip() or 0.0)
        return v if v > 0 else None
    except Exception:
        return None


def extract_frames(path: Path, out_dir: Path, *, ffmpeg_bin: str = "", ffprobe_bin: str = "") -> List[Path]:
    """Grab 3 JPEG frames at 25/50/75% of duration. Returns existing frame paths."""
    ffmpeg_bin = ffmpeg_bin or os.environ.get("FFMPEG_BIN", "ffmpeg")
    ffprobe_bin = ffprobe_bin or os.environ.get("FFPROBE_BIN", "ffprobe")
    dur = _ffprobe_duration_sec(path, ffprobe_bin=ffprobe_bin) or 0.0
    if dur <= 0:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: List[Path] = []
    for pct in (0.25, 0.50, 0.75):
        ts = max(0.0, dur * pct)
        fp = out_dir / f"f{int(pct * 100)}.jpg"
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-ss", f"{ts:.3f}", "-i", str(path),
             "-frames:v", "1", "-q:v", "3", str(fp)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0 and fp.exists() and fp.stat().st_size > 0:
            frames.append(fp)
    return frames


def _encode_image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def call_groq_vision(image_b64: str, *, api_key: str, model: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """Single Groq vision request -> parsed JSON dict (or None on failure)."""
    import requests  # local import: keep module import-light for unit tests

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]}],
        "temperature": 0.1,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=timeout)
        data = resp.json()
    except Exception:
        return None
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return None
    return parse_groq_json(choices[0].get("message", {}).get("content", ""))


def tag_video_file(path: Path, *, keys: List[str], model: str) -> Optional[Dict[str, Any]]:
    """Extract frames, tag each via Groq (round-robin keys), majority-vote merge."""
    if not keys:
        raise RuntimeError("No Groq API keys configured (set GROQ_API_KEYS or GROQ_API_KEY)")
    with tempfile.TemporaryDirectory(prefix="tagframes_") as tmp:
        frames = extract_frames(path, Path(tmp))
        if not frames:
            return None
        results: List[Dict[str, Any]] = []
        for i, fp in enumerate(frames):
            key = keys[i % len(keys)]
            parsed = call_groq_vision(_encode_image_b64(fp), api_key=key, model=model)
            if parsed:
                results.append(parsed)
    if not results:
        return None
    return merge_frame_votes(results)


def tag_clip_from_s3(*, bucket: str, s3_key: str, keys: List[str], model: str) -> Optional[Dict[str, Any]]:
    """Download an S3 clip, tag it, return a footage_tags record (or None)."""
    from src.storage.s3 import download_from_s3

    with tempfile.TemporaryDirectory(prefix="tagclip_") as tmp:
        suffix = Path(s3_key).suffix or ".mp4"
        dest = Path(tmp) / f"clip{suffix}"
        download_from_s3(bucket, s3_key, dest)
        votes = tag_video_file(dest, keys=keys, model=model)
    if not votes:
        return None
    return record_from_votes(s3_key=s3_key, votes=votes, tagger="groq")


# --------------------------------------------------------------------------- #
# Batch runner (used by the Celery task)
# --------------------------------------------------------------------------- #
def run_tagging_batch(
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
    """Tag every untagged S3 clip and upsert results into Postgres.

    I/O is injectable (list_keys_fn / tag_fn / fetch_tagged_fn / upsert_fn) so
    the orchestration is unit-testable without S3, ffmpeg, Groq, or a DB. In
    production the defaults wire to S3 + Groq + asyncpg.

    progress_cb(done:int, total:int, written:int) is called after each clip.
    Returns a summary dict.
    """
    import asyncio as _asyncio

    keys = groq_api_keys()
    model = groq_model()

    if list_keys_fn is None:
        def list_keys_fn() -> List[str]:
            from src.storage.s3 import list_s3_objects
            from pathlib import Path as _P
            out: List[str] = []
            token = None
            pref = source_prefix.strip("/")
            pref = f"{pref}/" if pref else ""
            while True:
                page = list_s3_objects(bucket, prefix=pref, continuation_token=token, max_keys=1000, delimiter="")
                for obj in page.get("objects") or []:
                    k = str(obj.get("key") or "").strip().lstrip("/")
                    if k and not k.endswith("/") and _P(k).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}:
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
                    return await fetch_tagged_clip_ids(conn)
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
            return tag_clip_from_s3(bucket=bucket, s3_key=s3_key, keys=keys, model=model)

    tagged_ids = fetch_tagged_fn()
    all_keys = list_keys_fn()
    untagged = select_untagged_keys(all_keys, tagged_ids)
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
