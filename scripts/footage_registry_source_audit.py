#!/usr/bin/env python3
"""Read-only audit of the clip_id/source collision risk. Run BEFORE touching the
production schema.

THE LATENT BUG: footage_assets and footage_tags both key on `clip_id TEXT PRIMARY
KEY` while carrying rows for source='video' AND source='photo'. clip_id is the
8+ digit id embedded in a file name, so the two pools share an id space. A photo
upsert with a clip_id that already belongs to a video does not collide — it
OVERWRITES the video row and flips `source` to 'photo' (upsert_assets does
`ON CONFLICT (clip_id) DO UPDATE SET ... source = EXCLUDED.source`). The video
silently leaves its pool: no error, no prune log, just a smaller registry. The
correct key is (source, clip_id).

Because clip_id is globally unique today, a collision cannot be observed as a
duplicate row — only as damage. So this audit looks for:
  1. cross-table source disagreement (assets says photo, tags says video, or the
     reverse) — a half-overwritten clip,
  2. tag rows orphaned from the registry per source,
  3. the real forward risk: clip_id overlap between the video and photo S3
     indexes, i.e. collisions that WILL happen on the next ingest.

Zero findings => the (source, clip_id) migration is a pure schema change.
Non-zero => reconcile FIRST; the migration would otherwise freeze the damage in.

Usage:
  python scripts/footage_registry_source_audit.py --dsn "$CREDITS_DB_URL" \
      [--video-index data/static_assets_index_1to1.json] \
      [--photo-index data/photo_assets_index.json] [-o audit.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def _audit_db(dsn: str) -> Dict[str, Any]:
    import asyncpg  # type: ignore

    conn = await asyncpg.connect(dsn=dsn)
    try:
        counts = {}
        for table in ("footage_assets", "footage_tags"):
            rows = await conn.fetch(
                f"SELECT source, COUNT(*) AS n FROM {table} GROUP BY source ORDER BY source"
            )
            counts[table] = {str(r["source"]): int(r["n"]) for r in rows}

        # A clip whose registry row and tag row disagree on source is the
        # fingerprint of an overwrite that only got one of the two tables.
        disagree = await conn.fetch(
            """
            SELECT a.clip_id, a.source AS asset_source, t.source AS tag_source
            FROM footage_assets a
            JOIN footage_tags t ON t.clip_id = a.clip_id
            WHERE a.source <> t.source
            ORDER BY a.clip_id
            LIMIT 200
            """
        )

        orphan_tags = await conn.fetch(
            """
            SELECT t.source, COUNT(*) AS n
            FROM footage_tags t
            LEFT JOIN footage_assets a ON a.clip_id = t.clip_id
            WHERE a.clip_id IS NULL
            GROUP BY t.source
            ORDER BY t.source
            """
        )
        return {
            "counts": counts,
            "source_disagreement": [
                {
                    "clip_id": str(r["clip_id"]),
                    "asset_source": str(r["asset_source"]),
                    "tag_source": str(r["tag_source"]),
                }
                for r in disagree
            ],
            "orphan_tag_rows": {str(r["source"]): int(r["n"]) for r in orphan_tags},
        }
    finally:
        await conn.close()


def _index_clip_ids(path: Path) -> set:
    from mlcore.footage_tags_db import extract_clip_id

    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    assets = obj.get("assets") if isinstance(obj, dict) else obj
    out = set()
    for a in assets or []:
        if not isinstance(a, dict):
            continue
        cid = extract_clip_id(a.get("file_name") or a.get("s3_key") or a.get("video_key"))
        if cid:
            out.add(str(cid))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="clip_id/source collision audit (read-only).")
    ap.add_argument("--dsn", default="", help="Postgres DSN (default: CREDITS_DB_URL).")
    ap.add_argument("--video-index", default="", help="Video S3 index JSON (forward risk).")
    ap.add_argument("--photo-index", default="", help="Photo S3 index JSON (forward risk).")
    ap.add_argument("-o", "--out", default="data/footage_registry_source_audit.json")
    args = ap.parse_args(argv)

    report: Dict[str, Any] = {}
    findings: List[str] = []

    dsn = (args.dsn or os.environ.get("CREDITS_DB_URL") or "").strip()
    if dsn:
        report["db"] = asyncio.run(_audit_db(dsn))
        if report["db"]["source_disagreement"]:
            findings.append(
                f"source_disagreement: {len(report['db']['source_disagreement'])} clip(s) "
                "have a registry row and a tag row claiming different pools — a clip was "
                "overwritten across sources"
            )
        orphans = {k: v for k, v in report["db"]["orphan_tag_rows"].items() if v}
        if orphans:
            findings.append(f"orphan_tag_rows: {orphans} (tags with no registry row)")
    else:
        report["db"] = {"skipped": "no dsn"}

    if args.video_index and args.photo_index:
        v = _index_clip_ids(Path(args.video_index))
        p = _index_clip_ids(Path(args.photo_index))
        overlap = sorted(v & p)
        report["index_overlap"] = {
            "video_clip_ids": len(v),
            "photo_clip_ids": len(p),
            "overlap_count": len(overlap),
            "overlap_sample": overlap[:50],
        }
        if overlap:
            findings.append(
                f"index_overlap: {len(overlap)} clip_id(s) exist in BOTH the video and "
                "photo S3 indexes — the next ingest WILL overwrite across pools"
            )
    else:
        report["index_overlap"] = {"skipped": "need --video-index and --photo-index"}

    report["findings"] = findings
    report["safe_to_migrate"] = not findings

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"safe_to_migrate": report["safe_to_migrate"], "findings": findings},
                     ensure_ascii=False, indent=2))
    print(f"full audit -> {out}")
    # Exit non-zero on findings so this can gate the migration in CI/a runbook.
    return 0 if report["safe_to_migrate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
