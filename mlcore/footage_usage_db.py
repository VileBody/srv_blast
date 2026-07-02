"""Postgres-backed footage USAGE ledger — cross-user variety for the picker.

Companion to footage_tags / footage_assets. Records which clips were served in
which bucket, so the picker can spread DIFFERENT users across the quality band
instead of everyone getting the objectively-top clip (score is user-independent
→ without this, two users who pick the same vibe get near-identical videos).

Two roles off ONE table (keyed by the stable clip_id, same identity as tags):
  - GLOBAL per-bucket cooldown: clips served recently to ANYONE in a bucket are
    deprioritized (LRU), so consecutive renders rotate through the band. This is
    a SOFT signal (reorder within the band, never a hard exclude), so recording
    at pick time is safe — a failed render just briefly cools a few clips.
  - PER-USER dedup (durable): clips this chat already got in this bucket (a hard
    exclude, for "each of my iterations is fresh"). Built here; the bot already
    carries a per-chat exclude too, so this is the durable backstop.

PURE helpers (recency ranking) are separated from the thin asyncpg I/O layer so
the logic is unit-testable without a live DB.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS footage_usage (
    id         BIGSERIAL PRIMARY KEY,
    bucket_id  TEXT      NOT NULL,
    clip_id    TEXT      NOT NULL,
    chat_id    TEXT      NOT NULL DEFAULT '',
    job_id     TEXT      NOT NULL DEFAULT '',
    served_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_footage_usage_bucket_time ON footage_usage(bucket_id, served_at DESC);
CREATE INDEX IF NOT EXISTS idx_footage_usage_user ON footage_usage(chat_id, bucket_id);
"""


def _norm(v: Any) -> str:
    return str(v or "").strip()


# --------------------------------------------------------------------------- #
# Pure helpers (no DB)
# --------------------------------------------------------------------------- #
def recency_index(recent_clip_ids: List[str]) -> Dict[str, int]:
    """recent_clip_ids is newest-first (as returned by fetch_recent_clip_ids).
    Returns {clip_id: index}, 0 = most recently served (hottest / most avoided).
    Distinct clip_ids only (first occurrence wins)."""
    out: Dict[str, int] = {}
    rank = 0
    for cid in recent_clip_ids:
        c = _norm(cid)
        if c and c not in out:
            out[c] = rank  # contiguous rank on first occurrence (no gaps on dup input)
            rank += 1
    return out


def coldness(clip_id: str, index: Dict[str, int], *, window: int) -> float:
    """Higher = colder = safer to reuse. A clip NOT in the recency window is
    coldest (never/long-ago served → +inf-ish). Within the window, an OLDER entry
    (larger index) is colder than a fresh one (index 0).

    The picker orders the quality band by `coldness` DESC (seed breaks ties), so
    consecutive renders/users walk down the band from the least-recently-used.
    """
    c = _norm(clip_id)
    if c not in index:
        return float(window) + 1.0  # cold: outside the cooldown window
    return float(index[c])           # 0 (hottest) .. window-1 (coolest in-window)


# --------------------------------------------------------------------------- #
# Thin asyncpg I/O layer
# --------------------------------------------------------------------------- #
async def init_schema(conn: Any) -> None:
    await conn.execute(SCHEMA)


async def record_usages(
    conn: Any,
    *,
    bucket_id: str,
    clip_ids: Iterable[str],
    chat_id: str = "",
    job_id: str = "",
) -> int:
    """Append one row per served clip. Deduplicates clip_ids within the call."""
    b = _norm(bucket_id)
    if not b:
        return 0
    seen: set = set()
    rows: List[tuple] = []
    for cid in clip_ids or []:
        c = _norm(cid)
        if not c or c in seen:
            continue
        seen.add(c)
        rows.append((b, c, _norm(chat_id), _norm(job_id)))
    if not rows:
        return 0
    await init_schema(conn)
    await conn.executemany(
        "INSERT INTO footage_usage (bucket_id, clip_id, chat_id, job_id, served_at) "
        "VALUES ($1,$2,$3,$4, NOW())",
        rows,
    )
    return len(rows)


async def fetch_recent_clip_ids(conn: Any, *, bucket_id: str, limit: int) -> List[str]:
    """Distinct clip_ids served in this bucket, newest-first, up to `limit`
    (the cooldown window). Feeds recency_index()/coldness() for band ordering."""
    b = _norm(bucket_id)
    if not b or int(limit) <= 0:
        return []
    await init_schema(conn)
    recs = await conn.fetch(
        """
        SELECT clip_id, MAX(served_at) AS last_served
        FROM footage_usage
        WHERE bucket_id = $1
        GROUP BY clip_id
        ORDER BY last_served DESC
        LIMIT $2
        """,
        b, int(limit),
    )
    return [str(r["clip_id"]) for r in recs]


async def fetch_user_seen(conn: Any, *, chat_id: str, bucket_id: str) -> set:
    """clip_ids this chat has already been served in this bucket (durable per-user
    dedup backstop). Empty when chat_id is blank."""
    ch = _norm(chat_id)
    b = _norm(bucket_id)
    if not ch or not b:
        return set()
    await init_schema(conn)
    recs = await conn.fetch(
        "SELECT DISTINCT clip_id FROM footage_usage WHERE chat_id = $1 AND bucket_id = $2",
        ch, b,
    )
    return {str(r["clip_id"]) for r in recs}
