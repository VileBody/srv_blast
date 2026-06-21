#!/usr/bin/env python3
"""Export Postgres curation overrides -> data/tag_overrides.json (the file the
footage picker reads). Run after blacklist changes, alongside the footage_tags
snapshot export, then deploy to the worker/orchestrator nodes.

Usage:
  CREDITS_DB_URL=postgres://... \
  python scripts/export_tag_overrides.py [out_path]

Default out_path: data/tag_overrides.json
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlcore.footage_overrides_db import (  # noqa: E402
    build_tag_overrides_doc,
    fetch_blacklisted_tags,
    init_schema,
)

_DEFAULT_OUT = "data/tag_overrides.json"


def _db_url() -> str:
    explicit = (os.environ.get("CREDITS_DB_URL") or "").strip()
    if explicit:
        return explicit
    host = (os.environ.get("POSTGRES_HOST") or "").strip()
    db = (os.environ.get("POSTGRES_DB") or "").strip()
    user = (os.environ.get("POSTGRES_USER") or "").strip()
    if not host or not db or not user:
        raise SystemExit("No DB config: set CREDITS_DB_URL or POSTGRES_HOST/DB/USER[/PASSWORD]")
    pw = (os.environ.get("POSTGRES_PASSWORD") or "").strip()
    port = (os.environ.get("POSTGRES_PORT") or "5432").strip()
    sslmode = (os.environ.get("POSTGRES_SSLMODE") or "prefer").strip()
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(pw)}@{host}:{int(port)}/{db}"
        f"?sslmode={quote_plus(sslmode)}"
    )


def _existing_assignments(out_path: Path) -> list:
    """Preserve any existing tag_assignments (file-based, unused but kept)."""
    if not out_path.exists():
        return []
    try:
        return json.loads(out_path.read_text(encoding="utf-8")).get("tag_assignments", []) or []
    except Exception:
        return []


async def _amain(out_path: str) -> None:
    import asyncpg  # type: ignore

    conn = await asyncpg.connect(dsn=_db_url())
    try:
        await init_schema(conn)
        blacklist = await fetch_blacklisted_tags(conn)
    finally:
        await conn.close()

    out = Path(out_path)
    doc = build_tag_overrides_doc(blacklist, _existing_assignments(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(doc['blacklisted_tags'])} blacklisted tags -> {out}")


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_OUT
    asyncio.run(_amain(out))


if __name__ == "__main__":
    main()
