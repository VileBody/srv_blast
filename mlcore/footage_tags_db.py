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
    source       TEXT      NOT NULL DEFAULT 'video',
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Idempotent migration for tables created before the photo pool existed:
-- pre-existing rows are video by definition, so the default keeps them correct.
ALTER TABLE footage_tags ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'video';

CREATE INDEX IF NOT EXISTS idx_footage_tags_updated ON footage_tags(updated_at);
CREATE INDEX IF NOT EXISTS idx_footage_tags_source ON footage_tags(source);
"""

# Asset pool sources. video = footage clips (default), photo = 4:3 photo flow.
SOURCE_VIDEO = "video"
SOURCE_PHOTO = "photo"


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


# Vision models sometimes return synonyms outside the enum (e.g. "cool" for
# "cold"); map the common ones so the color signal isn't dropped.
_COLOR_SYNONYMS = {
    "cool": "cold", "blue": "cold", "cold tone": "cold", "cold tones": "cold",
    "warm tone": "warm", "warm tones": "warm", "golden": "warm", "orange": "warm",
    "bright": "light", "dark tone": "dark", "black": "dark", "monochrome": "neutral",
    "grayscale": "neutral", "greyscale": "neutral", "mixed": "neutral", "grey": "neutral", "gray": "neutral",
}


def _norm_color(v: Any) -> str:
    out = _norm(v)
    out = _COLOR_SYNONYMS.get(out, out)
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
        "source": SOURCE_VIDEO,
    }


def photo_clip_id(value: Any) -> Optional[str]:
    """Stable, namespaced id for a PHOTO from its file name / s3 key.

    Photos carry no embedded 8+ digit clip id (the video identity scheme), so we
    key them by their normalized file stem under a ``photo:`` namespace. The
    prefix guarantees photo ids never collide with the pure-digit video clip_ids
    that share the footage_tags primary key.
    """
    name = str(value or "").strip().lstrip("/").split("/")[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = _norm(stem).replace(" ", "_")
    return f"photo:{stem}" if stem else None


def build_photo_tag_record(raw: Dict[str, Any], *, tagger: str = "") -> Optional[Dict[str, Any]]:
    """Map one Groq-Vision photo result -> a footage_tags record (source=photo).

    Mirrors build_tag_record but keys by photo_clip_id (no embedded clip id) and
    stamps source='photo'. video_key is set to the file_name so the photo picker
    matches snapshot rows to the photo inventory by name. Returns None when the
    row has no keyable file_name / s3_key.
    """
    if not isinstance(raw, dict):
        return None
    file_name = str(raw.get("file_name") or "")
    clip_id = (
        photo_clip_id(file_name)
        or photo_clip_id(raw.get("s3_key"))
        or photo_clip_id(raw.get("video_key"))
    )
    if not clip_id:
        return None
    return {
        "clip_id": clip_id,
        "file_name": file_name,
        "s3_key": str(raw.get("s3_key") or ""),
        "video_key": file_name,
        "mood": _norm_mood(raw.get("mood")),
        "color_tone": _norm_color(raw.get("color_tone")),
        "people_type": _norm_people(raw.get("people_type")),
        "theme_tags": _dedup_tags(raw.get("theme_tags") or []),
        "tagger": str(tagger or ""),
        "source": SOURCE_PHOTO,
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
            r.get("source", SOURCE_VIDEO) or SOURCE_VIDEO,
        )
        for r in records
        if r.get("clip_id")
    ]
    await conn.executemany(
        """
        INSERT INTO footage_tags
            (clip_id, file_name, s3_key, video_key, mood, color_tone,
             people_type, theme_tags, tagger, source, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, NOW())
        ON CONFLICT (clip_id) DO UPDATE SET
            file_name   = EXCLUDED.file_name,
            s3_key      = EXCLUDED.s3_key,
            video_key   = EXCLUDED.video_key,
            mood        = EXCLUDED.mood,
            color_tone  = EXCLUDED.color_tone,
            people_type = EXCLUDED.people_type,
            theme_tags  = EXCLUDED.theme_tags,
            tagger      = EXCLUDED.tagger,
            source      = EXCLUDED.source,
            updated_at  = NOW()
        """,
        rows,
    )
    return len(rows)


async def fetch_all_records(conn: Any, *, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """All tag rows. source=None → every pool; source='video'|'photo' → one pool."""
    cols = ("clip_id, file_name, s3_key, video_key, mood, color_tone, "
            "people_type, theme_tags, source")
    if source:
        recs = await conn.fetch(f"SELECT {cols} FROM footage_tags WHERE source = $1", source)
    else:
        recs = await conn.fetch(f"SELECT {cols} FROM footage_tags")
    return [dict(r) for r in recs]


async def fetch_tagged_clip_ids(conn: Any, *, source: Optional[str] = None) -> set:
    """clip_ids that already have tags, optionally scoped to one pool."""
    if source:
        recs = await conn.fetch(
            "SELECT clip_id FROM footage_tags "
            "WHERE array_length(theme_tags, 1) > 0 AND source = $1",
            source,
        )
    else:
        recs = await conn.fetch(
            "SELECT clip_id FROM footage_tags WHERE array_length(theme_tags, 1) > 0"
        )
    return {str(r["clip_id"]) for r in recs}


async def build_snapshot(conn: Any, *, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """All footage_tags rows in the legacy video_database shape the picker reads."""
    recs = await fetch_all_records(conn, source=source)
    return [snapshot_row_from_record(r) for r in recs]


async def delete_by_clip_ids(conn: Any, clip_ids: Iterable[str], *, source: Optional[str] = None) -> int:
    """Delete tag rows for the given clip_ids (used when a clip leaves the pool,
    e.g. an Asset-UI delete). Returns the number deleted."""
    ids = [str(c).strip() for c in (clip_ids or []) if str(c or "").strip()]
    if not ids:
        return 0
    if source:
        res = await conn.execute(
            "DELETE FROM footage_tags WHERE clip_id = ANY($1::text[]) AND source = $2", ids, source
        )
    else:
        res = await conn.execute("DELETE FROM footage_tags WHERE clip_id = ANY($1::text[])", ids)
    try:
        return int(str(res).split()[-1])
    except Exception:
        return 0


def filter_snapshot_to_pool(
    rows: List[Dict[str, Any]], pool_clip_ids: Optional[set]
) -> List[Dict[str, Any]]:
    """Keep only snapshot rows whose clip_id is in the live pool (footage_assets),
    dropping orphans left by deleted clips. FAIL-SAFE: when pool_clip_ids is empty
    or None (registry not populated), return rows unchanged so we never blank the
    snapshot the picker reads. Non-destructive — footage_tags rows stay put."""
    if not pool_clip_ids:
        return list(rows or [])
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        # video ids are pure digits, photo ids are 'photo:'-prefixed → disjoint,
        # so trying both against the pool is unambiguous (works for either pool).
        candidates = {
            extract_clip_id(r.get("video_key")),
            extract_clip_id(r.get("video_path")),
            extract_clip_id(r.get("file_name")),
            photo_clip_id(r.get("video_key")),
            photo_clip_id(r.get("file_name")),
        }
        if candidates & pool_clip_ids:
            out.append(r)
    # FAIL-SAFE: if filtering wiped a NON-empty snapshot, the pool registry is out
    # of sync with footage_tags (e.g. registry not populated for this source, or
    # a keying mismatch) — NEVER emit an empty snapshot (it would break all
    # picking). Keep the tags; orphans are harmless (the picker joins to the live
    # inventory anyway). Only a true partial overlap actually drops orphans.
    if rows and not out:
        return list(rows)
    return out


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
