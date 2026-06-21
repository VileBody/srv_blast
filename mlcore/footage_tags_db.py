"""Postgres-backed footage tag store (single source of truth for clip tags).

Architecture (decided 2026-06-20): Postgres is the WRITE source for tags
(populated by migration + the server-side tagging task). The footage picker
never reads Postgres directly — at inventory rebuild we EXPORT a JSON snapshot
in the legacy video_database shape, and the picker keeps reading JSON
(FOOTAGE_STYLE_METADATA_DB_PATHS_JSON). This keeps Postgres out of the render
hot path while giving us one deduplicated, concurrently-writable tag store.

Dedup key is clip_id (the 8+ digit id embedded in the file name / video_key).
Legacy genre folders are NOT part of identity — matching is tag-only now, so the
same physical clip living under several genre folders collapses to one row.

This module separates PURE transforms (testable without a live DB) from the
thin asyncpg I/O layer.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_CLIP_ID_RE = re.compile(r"(\d{8,})")

# Mirrors footage_picker normalization so snapshot tags match at pick time.
_PEOPLE_ALLOWED = {"none", "girls", "guys", "couple", "crowd", "driver"}
_COLOR_ALLOWED = {"dark", "light", "warm", "cold", "neutral"}
_MOOD_ALLOWED = {"major", "minor"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS footage_tags (
    clip_id      TEXT PRIMARY KEY,
    file_name    TEXT      NOT NULL DEFAULT '',
    s3_key       TEXT      NOT NULL DEFAULT '',
    video_key    TEXT      NOT NULL DEFAULT '',
    mood         TEXT      NOT NULL DEFAULT '',
    color_tone   TEXT      NOT NULL DEFAULT '',
    people_type  TEXT      NOT NULL DEFAULT 'none',
    theme_tags   TEXT[]    NOT NULL DEFAULT '{}',
    tagger       TEXT      NOT NULL DEFAULT '',
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_footage_tags_updated ON footage_tags(updated_at);
"""


# --------------------------------------------------------------------------- #
# Pure helpers (no DB)
# --------------------------------------------------------------------------- #
def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def extract_clip_id(value: Any) -> Optional[str]:
    m = _CLIP_ID_RE.search(str(value or ""))
    return m.group(1) if m else None


def _norm_people(v: Any) -> str:
    out = _norm(v)
    if out == "guy":
        out = "guys"
    return out if out in _PEOPLE_ALLOWED else "none"


def _norm_color(v: Any) -> str:
    out = _norm(v)
    return out if out in _COLOR_ALLOWED else ""


def _norm_mood(v: Any) -> str:
    out = _norm(v)
    return out if out in _MOOD_ALLOWED else ""


def _dedup_tags(tags: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for t in tags or []:
        nt = _norm(t)
        if nt and nt not in seen:
            seen.add(nt)
            out.append(nt)
    return out


def build_tag_record(raw: Dict[str, Any], *, tagger: str = "") -> Optional[Dict[str, Any]]:
    """Map one legacy video_database row -> a footage_tags record.

    Returns None when the row has no extractable clip_id (cannot be keyed).
    """
    if not isinstance(raw, dict):
        return None
    video_key = str(raw.get("video_key") or "")
    clip_id = (
        extract_clip_id(video_key)
        or extract_clip_id(raw.get("video_path"))
        or extract_clip_id(raw.get("file_name"))
    )
    if not clip_id:
        return None
    return {
        "clip_id": clip_id,
        "file_name": str(raw.get("file_name") or ""),
        "s3_key": str(raw.get("s3_key") or ""),
        "video_key": video_key,
        "mood": _norm_mood(raw.get("mood")),
        "color_tone": _norm_color(raw.get("color_tone")),
        "people_type": _norm_people(raw.get("people_type")),
        "theme_tags": _dedup_tags(raw.get("theme_tags") or []),
        "tagger": str(tagger or ""),
    }


def _record_completeness(rec: Dict[str, Any]) -> int:
    """Score how 'complete' a record is, to break ties when deduping clip_ids."""
    score = len(rec.get("theme_tags") or [])
    if rec.get("mood"):
        score += 1
    if rec.get("color_tone"):
        score += 1
    if rec.get("people_type") and rec.get("people_type") != "none":
        score += 1
    return score


def merge_records_by_clip_id(
    record_batches: Iterable[Iterable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Collapse many record sources into one row per clip_id.

    Within a clip_id the most COMPLETE record wins (most tags + filled fields);
    its theme_tags are unioned with the others so we never lose a tag. Later
    batches break exact-completeness ties (pass freshest source last).
    """
    chosen: Dict[str, Dict[str, Any]] = {}
    for batch in record_batches:
        for rec in batch:
            if not rec:
                continue
            cid = rec.get("clip_id")
            if not cid:
                continue
            prev = chosen.get(cid)
            if prev is None:
                chosen[cid] = dict(rec)
                continue
            merged_tags = _dedup_tags(list(prev.get("theme_tags") or []) + list(rec.get("theme_tags") or []))
            # >= so a later, equally-complete batch overrides (freshest last).
            winner = rec if _record_completeness(rec) >= _record_completeness(prev) else prev
            out = dict(winner)
            out["theme_tags"] = merged_tags
            chosen[cid] = out
    return list(chosen.values())


def snapshot_row_from_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Render a footage_tags record back into the legacy video_database shape
    that footage_picker.load_footage_style_metadata_rows() consumes."""
    return {
        "video_key": rec.get("video_key") or rec.get("clip_id") or "",
        "video_path": rec.get("file_name") or "",
        "mood": rec.get("mood") or "",
        "color_tone": rec.get("color_tone") or "",
        "people_type": rec.get("people_type") or "none",
        "theme_tags": list(rec.get("theme_tags") or []),
    }


# --------------------------------------------------------------------------- #
# Thin asyncpg I/O layer
# --------------------------------------------------------------------------- #
async def init_schema(conn: Any) -> None:
    await conn.execute(SCHEMA)


async def upsert_records(conn: Any, records: List[Dict[str, Any]]) -> int:
    """Upsert records (ON CONFLICT clip_id). Returns number written."""
    if not records:
        return 0
    rows = [
        (
            r["clip_id"],
            r.get("file_name", ""),
            r.get("s3_key", ""),
            r.get("video_key", ""),
            r.get("mood", ""),
            r.get("color_tone", ""),
            r.get("people_type", "none"),
            list(r.get("theme_tags") or []),
            r.get("tagger", ""),
        )
        for r in records
        if r.get("clip_id")
    ]
    await conn.executemany(
        """
        INSERT INTO footage_tags
            (clip_id, file_name, s3_key, video_key, mood, color_tone,
             people_type, theme_tags, tagger, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, NOW())
        ON CONFLICT (clip_id) DO UPDATE SET
            file_name   = EXCLUDED.file_name,
            s3_key      = EXCLUDED.s3_key,
            video_key   = EXCLUDED.video_key,
            mood        = EXCLUDED.mood,
            color_tone  = EXCLUDED.color_tone,
            people_type = EXCLUDED.people_type,
            theme_tags  = EXCLUDED.theme_tags,
            tagger      = EXCLUDED.tagger,
            updated_at  = NOW()
        """,
        rows,
    )
    return len(rows)


async def fetch_all_records(conn: Any) -> List[Dict[str, Any]]:
    recs = await conn.fetch(
        "SELECT clip_id, file_name, s3_key, video_key, mood, color_tone, "
        "people_type, theme_tags FROM footage_tags"
    )
    return [dict(r) for r in recs]


async def fetch_tagged_clip_ids(conn: Any) -> set:
    recs = await conn.fetch("SELECT clip_id FROM footage_tags WHERE array_length(theme_tags, 1) > 0")
    return {str(r["clip_id"]) for r in recs}


async def build_snapshot(conn: Any) -> List[Dict[str, Any]]:
    """All footage_tags rows in the legacy video_database shape the picker reads."""
    recs = await fetch_all_records(conn)
    return [snapshot_row_from_record(r) for r in recs]


def pick_snapshot_path(
    *,
    explicit: str = "",
    metadata_paths_json: str = "",
    default: str = "data/footage_tags_snapshot.json",
) -> str:
    """Resolve where to write the tags snapshot so it matches what the picker
    reads. Priority: explicit env > first path in FOOTAGE_STYLE_METADATA_DB_PATHS_JSON
    > default. Pure (no env access) for testability."""
    e = str(explicit or "").strip()
    if e:
        return e
    raw = str(metadata_paths_json or "").strip()
    if raw:
        try:
            import json as _json

            arr = _json.loads(raw)
            if isinstance(arr, list) and arr:
                first = str(arr[0] or "").strip()
                if first:
                    return first
        except Exception:
            pass
    return default
