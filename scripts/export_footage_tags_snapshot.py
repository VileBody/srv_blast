#!/usr/bin/env python3
"""Export Postgres footage_tags -> JSON snapshot in the legacy video_database
shape that footage_picker reads. Run at inventory rebuild; point the picker at
the output via FOOTAGE_STYLE_METADATA_DB_PATHS_JSON.

Usage:
  CREDITS_DB_URL=postgres://... \
  python scripts/export_footage_tags_snapshot.py [out_path] [--source video|photo]

--source scopes the export to one asset pool (default: video). The photo pool
(--source photo) writes its own snapshot consumed by the media_type=photo picker.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlcore.footage_tags_db import (  # noqa: E402
    SOURCE_PHOTO,
    SOURCE_VIDEO,
    fetch_all_records,
    snapshot_row_from_record,
)

_DEFAULT_OUT = "data/footage_tags_snapshot.json"
_DEFAULT_OUT_PHOTO = "data/photo_tags_snapshot.json"


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


async def _amain(out_path: str, source: str) -> None:
    import asyncpg  # type: ignore

    conn = await asyncpg.connect(dsn=_db_url())
    try:
        recs = await fetch_all_records(conn, source=source)
    finally:
        await conn.close()

    snapshot = [snapshot_row_from_record(r) for r in recs]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tagged = sum(1 for r in snapshot if r.get("theme_tags"))
    print(f"wrote {len(snapshot)} rows ({tagged} tagged, source={source}) -> {out}")


def _parse_args(argv: list[str]) -> tuple[str, str]:
    """(out_path, source) from argv. Positional out_path + optional --source."""
    source = SOURCE_VIDEO
    positional: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--source":
            source = (argv[i + 1] if i + 1 < len(argv) else "").strip().lower()
            i += 2
            continue
        if a.startswith("--source="):
            source = a.split("=", 1)[1].strip().lower()
            i += 1
            continue
        positional.append(a)
        i += 1
    if source not in (SOURCE_VIDEO, SOURCE_PHOTO):
        raise SystemExit(f"--source must be {SOURCE_VIDEO!r} or {SOURCE_PHOTO!r}, got {source!r}")
    default_out = _DEFAULT_OUT_PHOTO if source == SOURCE_PHOTO else _DEFAULT_OUT
    out = positional[0] if positional else default_out
    return out, source


def main() -> None:
    out, source = _parse_args(sys.argv[1:])
    asyncio.run(_amain(out, source))


if __name__ == "__main__":
    main()
