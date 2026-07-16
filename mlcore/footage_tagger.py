"""Server-side footage tagger: S3 clip -> 3 ffmpeg frames -> Vision -> record.

Ported from the offline pin/scan.py pipeline, adapted for the server:
  - source is an S3 object (downloaded), not a local file
  - frames extracted with ffmpeg/ffprobe (already in the runtime image), no cv2
  - Qwen/DashScope vision only; API keys come from env, never hardcoded
  - output is a footage_tags record (see footage_tags_db.build_tag_record)

PURE helpers (parse / vote / untagged-diff / record shaping) are separated from
the I/O layer (ffmpeg subprocess, Vision HTTP, S3 download) so the logic is unit
testable without network, ffmpeg, or a live DB.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mlcore.footage_tags_db import build_tag_record, extract_clip_id

log = logging.getLogger("footage_tagger")

TAGGER_VERSION = "vision-v2"

_COLOR_ALLOWED = frozenset({"dark", "light", "warm", "cold", "neutral"})
_MOOD_ALLOWED = frozenset({"minor", "major"})
_PEOPLE_ALLOWED = frozenset({"none", "girls", "guys", "couple", "crowd", "driver"})
_MIN_CANONICAL_TAGS = 4
_MAX_CANONICAL_TAGS = 10


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


@lru_cache(maxsize=1)
def canonical_theme_tags() -> frozenset[str]:
    """Tags that can actually affect production bucket matching."""
    from mlcore.footage_bucket_catalog import build_buckets

    tags: set[str] = set()
    for bucket in build_buckets():
        tags.update(_norm(x) for x in bucket.priority_tags)
        tags.update(_norm(x) for x in bucket.exclude_tags)
    tags.discard("")
    if not tags:
        raise RuntimeError("canonical footage tag vocabulary is empty")
    return frozenset(tags)


@lru_cache(maxsize=1)
def _tag_aliases() -> Dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "data" / "tag_aliases.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"tag alias vocabulary is unavailable: {path}: {exc!r}") from exc
    aliases = raw.get("aliases") if isinstance(raw, dict) else None
    if not isinstance(aliases, dict):
        raise RuntimeError(f"tag alias vocabulary has no aliases object: {path}")
    allowed = canonical_theme_tags()
    return {
        _norm(source): _norm(target)
        for source, target in aliases.items()
        if _norm(source) and _norm(target) in allowed
    }


def build_vision_prompt(*, media_kind: str) -> str:
    """Strict V2 prompt shared by still photos and extracted video frames."""
    vocabulary = ", ".join(sorted(canonical_theme_tags()))
    return f"""Analyze this {media_kind} for visual footage selection.
Return ONLY one valid JSON object, with no markdown and no commentary:

{{
  "color_tone": "dark | light | warm | cold | neutral",
  "people_type": "none | girls | guys | couple | crowd | driver",
  "theme_tags": ["6-10 values copied exactly from ALLOWED THEME TAGS"],
  "mood": "minor | major"
}}

STRICT RULES:
- Describe only clearly visible content. Never infer story, profession, relationship or location.
- theme_tags must contain 6-10 DISTINCT values copied EXACTLY from ALLOWED THEME TAGS below.
- Cover the strongest visible subject, setting, action, lighting/time and weather when available.
- Prefer specific tags ("wet road", "night city") over generic tags ("road", "city") when visible.
- Do not output synonyms, explanations, adjectives or any tag outside the allowed list.
- people_type: none=no visible person; couple=exactly two people presented together; crowd=3+ people;
  driver=person clearly inside/operating a vehicle; girls/guys=dominant visible gender group.
- color_tone: dark=low-light/night/shadows; light=bright daylight/high-key; warm=orange/gold/sunset;
  cold=blue/grey/rain/snow; neutral=no clear dominant treatment. Choose the dominant treatment only.
- mood: major=bright/uplifting/peaceful/celebratory; minor=tense/lonely/dark/melancholic/aggressive.

ALLOWED THEME TAGS:
{vocabulary}
"""


_PROMPT = build_vision_prompt(media_kind="video frame")


# --------------------------------------------------------------------------- #
# Config (env)
# --------------------------------------------------------------------------- #
# --- Qwen-VL (Alibaba DashScope, OpenAI-compatible). -------------------------
_QWEN_BASE_DEFAULT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _fallback_dashscope_keys() -> List[str]:
    from pathlib import Path as _P

    src = _P(__file__).resolve().parents[1] / "config" / "dashscope_keys_fallback.json"
    if not src.exists():
        return []
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [str(k).strip() for k in (data.get("keys") or []) if str(k).strip()]


def dashscope_api_keys() -> List[str]:
    """Qwen/DashScope keys: DASHSCOPE_API_KEYS (csv) > DASHSCOPE_API_KEY > file."""
    multi = (os.environ.get("DASHSCOPE_API_KEYS") or "").strip()
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]
    single = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if single:
        return [single]
    return _fallback_dashscope_keys()


def qwen_model() -> str:
    return (os.environ.get("QWEN_VISION_MODEL") or "qwen-vl-max").strip()


def qwen_base_url() -> str:
    return (os.environ.get("DASHSCOPE_BASE_URL") or _QWEN_BASE_DEFAULT).strip().rstrip("/")


def vision_endpoints() -> List[Dict[str, str]]:
    """Qwen/DashScope endpoints, one per configured API key.

    Groq is intentionally not a tagging provider. Old GROQ_* and
    TAG_PROVIDER_ORDER environment values are ignored.
    """
    return [
        {
            "provider": "qwen",
            "base_url": qwen_base_url(),
            "api_key": key,
            "model": qwen_model(),
        }
        for key in dashscope_api_keys()
    ]


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O)
# --------------------------------------------------------------------------- #
def parse_vision_json(raw: str) -> Optional[Dict[str, Any]]:
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


_COLOR_SYNONYMS = {
    "cool": "cold",
    "blue": "cold",
    "grey": "neutral",
    "gray": "neutral",
    "mixed": "neutral",
    "bright": "light",
    "golden": "warm",
    "orange": "warm",
}
_PEOPLE_SYNONYMS = {
    "no people": "none",
    "guy": "guys",
    "man": "guys",
    "male": "guys",
    "girl": "girls",
    "woman": "girls",
    "female": "girls",
    "group": "crowd",
}


def normalize_vision_result(raw: Any) -> Optional[Dict[str, Any]]:
    """Validate one model response and map known synonyms to picker vocabulary.

    A syntactically valid but semantically weak response is a failed endpoint
    result: the explicitly configured next provider may be tried. Unknown tags
    are never persisted.
    """
    if not isinstance(raw, dict):
        return None

    color = _COLOR_SYNONYMS.get(_norm(raw.get("color_tone")), _norm(raw.get("color_tone")))
    mood = _norm(raw.get("mood"))
    people = _PEOPLE_SYNONYMS.get(_norm(raw.get("people_type")), _norm(raw.get("people_type")))
    if color not in _COLOR_ALLOWED or mood not in _MOOD_ALLOWED or people not in _PEOPLE_ALLOWED:
        return None

    raw_tags = raw.get("theme_tags")
    if not isinstance(raw_tags, list):
        return None
    allowed = canonical_theme_tags()
    aliases = _tag_aliases()
    tags: List[str] = []
    seen: set[str] = set()
    for value in raw_tags:
        tag = _norm(value)
        tag = aliases.get(tag, tag)
        if tag in allowed and tag not in seen:
            seen.add(tag)
            tags.append(tag)
        if len(tags) >= _MAX_CANONICAL_TAGS:
            break
    if len(tags) < _MIN_CANONICAL_TAGS:
        return None

    return {
        "color_tone": color,
        "people_type": people,
        "has_people": people != "none",
        "theme_tags": tags,
        "mood": mood,
    }


def merge_frame_votes(frames: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Majority vote fields; rank canonical tags by cross-frame frequency."""
    def vote(field: str) -> str:
        vals = [str(f[field]) for f in frames if field in f and f[field] is not None]
        return Counter(vals).most_common(1)[0][0] if vals else ""

    tag_counts: Counter[str] = Counter()
    first_seen: Dict[str, int] = {}
    position = 0
    for frame in frames:
        for value in frame.get("theme_tags") or []:
            tag = _norm(value)
            if not tag:
                continue
            tag_counts[tag] += 1
            first_seen.setdefault(tag, position)
            position += 1
    unique_tags = sorted(tag_counts, key=lambda tag: (-tag_counts[tag], first_seen[tag]))[
        :_MAX_CANONICAL_TAGS
    ]

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


def record_from_votes(*, s3_key: str, votes: Dict[str, Any], tagger: str = TAGGER_VERSION) -> Optional[Dict[str, Any]]:
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


def call_openai_vision(
    image_b64: str, *, base_url: str, api_key: str, model: str, timeout: float = 30.0, prompt: str = "",
) -> Optional[Dict[str, Any]]:
    """One Qwen/DashScope vision request -> parsed JSON dict, or None on failure."""
    import requests  # local import: keep module import-light for unit tests

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt or _PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]}],
        "temperature": 0.1,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/chat/completions"
    # The build worker has a global HTTP(S)_PROXY for unrelated outbound
    # traffic. That proxy currently returns 502 while tunnelling to DashScope.
    # Qwen therefore uses a direct session by default. A dedicated working proxy
    # can still be opted in explicitly without inheriting the global one.
    session = requests.Session()
    session.trust_env = False
    explicit_proxy = str(os.environ.get("DASHSCOPE_PROXY_URL") or "").strip()
    if explicit_proxy:
        session.proxies.update({"http": explicit_proxy, "https": explicit_proxy})
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        log.warning("vision request error (%s): %r", base_url, e)
        return None
    finally:
        session.close()
    if resp.status_code != 200:
        log.warning("vision HTTP %s base=%s model=%s body=%s", resp.status_code, base_url, model, (resp.text or "")[:300])
        return None
    try:
        data = resp.json()
    except Exception as e:
        log.warning("vision bad JSON (%s): %r", base_url, e)
        return None
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        log.warning("vision no choices (%s): %s", base_url, json.dumps(data)[:300] if isinstance(data, dict) else type(data))
        return None
    return parse_vision_json(choices[0].get("message", {}).get("content", ""))


def _tag_one_frame(image_b64: str, endpoints: List[Dict[str, str]], prompt: str) -> Optional[Dict[str, Any]]:
    """Try configured Qwen keys in order until one returns a valid result."""
    for ep in endpoints:
        parsed = call_openai_vision(
            image_b64, base_url=ep["base_url"], api_key=ep["api_key"], model=ep["model"], prompt=prompt,
        )
        normalized = normalize_vision_result(parsed)
        if normalized:
            return normalized
        if parsed is not None:
            log.warning(
                "vision semantic validation failed provider=%s model=%s; trying next configured endpoint",
                ep.get("provider"),
                ep.get("model"),
            )
    return None


def tag_video_file(
    path: Path, *, endpoints: Optional[List[Dict[str, str]]] = None, prompt: str = "",
) -> Optional[Dict[str, Any]]:
    """Extract frames, tag each with Qwen, and majority-vote the results."""
    if endpoints is None:
        endpoints = vision_endpoints()
    if not endpoints:
        raise RuntimeError("no_vision_keys")  # set DASHSCOPE_API_KEYS
    with tempfile.TemporaryDirectory(prefix="tagframes_") as tmp:
        frames = extract_frames(path, Path(tmp))
        if not frames:
            raise RuntimeError("no_frames")  # ffmpeg/ffprobe failed or 0-duration
        results: List[Dict[str, Any]] = []
        for fp in frames:
            parsed = _tag_one_frame(_encode_image_b64(fp), endpoints, prompt=prompt)
            if parsed:
                results.append(parsed)
    if not results:
        raise RuntimeError("vision_no_result")  # all Qwen keys failed (see vision logs)
    return merge_frame_votes(results)


def tag_clip_from_s3(
    *, bucket: str, s3_key: str, endpoints: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Download an S3 clip, tag it, return a footage_tags record.

    Raises a short categorized RuntimeError on failure (download_failed /
    no_frames / vision_no_result / no_clip_id) so the batch can tally reasons.
    """
    from src.storage.s3 import download_from_s3

    with tempfile.TemporaryDirectory(prefix="tagclip_") as tmp:
        suffix = Path(s3_key).suffix or ".mp4"
        dest = Path(tmp) / f"clip{suffix}"
        try:
            download_from_s3(bucket, s3_key, dest)
        except Exception as e:
            raise RuntimeError(f"download_failed: {e}") from e
        votes = tag_video_file(dest, endpoints=endpoints)
    rec = record_from_votes(s3_key=s3_key, votes=votes, tagger=TAGGER_VERSION)
    if not rec:
        raise RuntimeError("no_clip_id")
    return rec


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
    the orchestration is unit-testable without S3, ffmpeg, Qwen, or a DB. In
    production the defaults wire to S3 + Qwen + asyncpg.

    progress_cb(done:int, total:int, written:int) is called after each clip.
    Returns a summary dict.
    """
    import asyncio as _asyncio

    endpoints = vision_endpoints()
    log.warning(
        "tagging start: provider=qwen endpoints=%d suffixes=%s direct=%s",
        len(endpoints),
        [ep["api_key"][-4:] for ep in endpoints],
        not bool(str(os.environ.get("DASHSCOPE_PROXY_URL") or "").strip()),
    )

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
            return tag_clip_from_s3(bucket=bucket, s3_key=s3_key, endpoints=endpoints)

    tagged_ids = fetch_tagged_fn()
    all_keys = list_keys_fn()
    untagged = select_untagged_keys(all_keys, tagged_ids)
    if limit and limit > 0:
        untagged = untagged[:limit]

    total = len(untagged)
    written = 0
    failed = 0
    reasons: Counter = Counter()
    pending: List[Dict[str, Any]] = []
    for i, key in enumerate(untagged, start=1):
        rec = None
        try:
            rec = tag_fn(key)
        except Exception as e:
            # Keep the reason short (token before ':') for tallying.
            reasons[str(e).split(":", 1)[0].strip()[:40] or "error"] += 1
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

    top_failures = dict(reasons.most_common(5))
    if failed:
        log.warning("tagging batch: processed=%d written=%d failed=%d reasons=%s", total, written, failed, top_failures)
    return {
        "total_s3": len(all_keys),
        "already_tagged": len(tagged_ids),
        "untagged_processed": total,
        "written": written,
        "failed": failed,
        "failure_reasons": top_failures,
    }
