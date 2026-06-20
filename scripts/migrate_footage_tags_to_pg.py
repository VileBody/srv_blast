#!/usr/bin/env python3
"""One-shot migration: load legacy video_database JSON files into Postgres.

Dedups by clip_id (most complete record wins, theme_tags unioned). Pass the
freshest source LAST so it breaks ties. Idempotent — safe to re-run (upsert).

Usage:
  CREDITS_DB_URL=postgres://... \
  python scripts/migrate_footage_tags_to_pg.py \
      "2nd_footage_selection_prompt/video_database (2).json" \
      "2nd_footage_selection_prompt/video_database2.json" \
      "C:/Users/User/Desktop/Папки/blast/pin/meta/video_database.json"

If no paths are given, the two in-repo copies are used. Connection comes from
CREDITS_DB_URL, or POSTGRES_HOST/PORT/DB/USER/PASSWORD/SSLMODE.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

# Make repo root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlcore.footage_tags_db import (  # noqa: E402
    build_tag_record,
    fetch_all_records,
    init_schema,
    merge_records_by_clip_id,
    upsert_records,
)

_DEFAULT_PATHS = [
    "2nd_footage_selection_prompt/video_database (2).json",
    "2nd_footage_selection_prompt/video_database2.json",
]


def _db_url() -> str:
    explicit = (os.environ.get("CREDITS_DB_URL") or "").strip()
    if explicit:
        return explicit
    host = (os.environ.get("POSTGRES_HOST") or "").strip()
    db = (os.environ.get("POSTGRES_DB") or "").strip()
    user = (os.environ.get("POSTGRES_USER") or "").strip()
    pw = (os.environ.get("POSTGRES_PASSWORD") or "").strip()
    port = (os.environ.get("POSTGRES_PORT") or "5432").strip()
    sslmode = (os.environ.get("POSTGRES_SSLMODE") or "prefer").strip()
    if not host or not db or not user:
        raise SystemExit("No DB config: set CREDITS_DB_URL or POSTGRES_HOST/DB/USER[/PASSWORD]")
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(pw)}@{host}:{int(port)}/{db}"
        f"?sslmode={quote_plus(sslmode)}"
    )


def _load_rows(path: Path) -> list[dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    items = obj if isinstance(obj, list) else (obj.get("items") or obj.get("videos") or obj.get("assets") or [])
    if not isinstance(items, list):
        raise SystemExit(f"Unexpected JSON root (not a list of rows): {path}")
    return [r for r in items if isinstance(r, dict)]


async def _amain(paths: list[str]) -> None:
    import asyncpg  # type: ignore

    sources = paths or _DEFAULT_PATHS
    batches: list[list[dict]] = []
    for p in sources:
        fp = Path(p)
        if not fp.exists():
            raise SystemExit(f"Source file missing: {fp}")
        rows = _load_rows(fp)
        recs = [r for r in (build_tag_record(x, tagger="migration") for x in rows) if r]
        print(f"  loaded {len(recs):5d} keyed records from {fp.name}")
        batches.append(recs)

    merged = merge_records_by_clip_id(batches)
    print(f"merged -> {len(merged)} unique clip_ids")

    conn = await asyncpg.connect(dsn=_db_url())
    try:
        await init_schema(conn)
        before = len(await fetch_all_records(conn))
        written = await upsert_records(conn, merged)
        after = len(await fetch_all_records(conn))
        print(f"upserted {written} rows | table {before} -> {after} rows")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(_amain(sys.argv[1:]))


if __name__ == "__main__":
    main()
