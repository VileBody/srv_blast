"""Postgres-backed footage curation overrides (currently: global tag blacklist).

Why: the admin UI runs in the asset-ui container without a shared ./data mount,
so file-based overrides never reached the picker on the multi-node deploy. The
blacklist now lives in Postgres (written by admin, shared across nodes) and is
exported to data/tag_overrides.json — the file the picker already reads — so the
picker stays file-based (DB out of the render hot path), same pattern as
footage_tags -> snapshot.

Per-asset exclude and tag->theme assignment are intentionally NOT modeled here:
deletion already works via S3 trash + index rebuild, and assignment is unused.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS footage_blacklisted_tags (
    tag        TEXT PRIMARY KEY,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


def _norm_tag(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


# --------------------------------------------------------------------------- #
# Pure helper (no I/O)
# --------------------------------------------------------------------------- #
def build_tag_overrides_doc(
    blacklisted_tags: List[str],
    tag_assignments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Shape the document the picker reads (footage_picker._load_global_tag_overrides).

    Tags are normalized + deduped. tag_assignments is preserved as-is (kept for
    schema compatibility; unused today -> defaults to []).
    """
    out: List[str] = []
    seen: set[str] = set()
    for t in blacklisted_tags or []:
        nt = _norm_tag(t)
        if nt and nt not in seen:
            seen.add(nt)
            out.append(nt)
    return {"blacklisted_tags": out, "tag_assignments": list(tag_assignments or [])}


# --------------------------------------------------------------------------- #
# Thin asyncpg I/O
# --------------------------------------------------------------------------- #
async def init_schema(conn: Any) -> None:
    await conn.execute(SCHEMA)


async def fetch_blacklisted_tags(conn: Any) -> List[str]:
    rows = await conn.fetch("SELECT tag FROM footage_blacklisted_tags ORDER BY tag")
    return [str(r["tag"]) for r in rows]


async def add_blacklisted_tag(conn: Any, tag: str) -> str:
    nt = _norm_tag(tag)
    if not nt:
        raise ValueError("empty tag")
    await conn.execute(
        "INSERT INTO footage_blacklisted_tags (tag) VALUES ($1) ON CONFLICT (tag) DO NOTHING",
        nt,
    )
    return nt


async def remove_blacklisted_tag(conn: Any, tag: str) -> str:
    nt = _norm_tag(tag)
    await conn.execute("DELETE FROM footage_blacklisted_tags WHERE tag = $1", nt)
    return nt
