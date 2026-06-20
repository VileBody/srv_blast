#!/usr/bin/env python3
"""Export Postgres footage_tags -> JSON snapshot in the legacy video_database
shape that footage_picker reads. Run at inventory rebuild; point the picker at
the output via FOOTAGE_STYLE_METADATA_DB_PATHS_JSON.

Usage:
  CREDITS_DB_URL=postgres://... \
  python scripts/export_footage_tags_snapshot.py data/footage_tags_snapshot.json
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlcore.footage_tags_db import fetch_all_records, snapshot_row_from_record  # noqa: E402

_DEFAULT_OUT = "data/footage_tags_snapshot.json"


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


async def _amain(out_path: str) -> None:
    import asyncpg  # type: ignore

    conn = await asyncpg.connect(dsn=_db_url())
    try:
        recs = await fetch_all_records(conn)
    finally:
        await conn.close()

    snapshot = [snapshot_row_from_record(r) for r in recs]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tagged = sum(1 for r in snapshot if r.get("theme_tags"))
    print(f"wrote {len(snapshot)} rows ({tagged} tagged) -> {out}")


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_OUT
    asyncio.run(_amain(out))


if __name__ == "__main__":
    main()
