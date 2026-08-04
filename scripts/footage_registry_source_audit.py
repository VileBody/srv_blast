#!/usr/bin/env python3
"""Read-only health audit of the two-pool footage registry.

THE BUG THIS TRACKS: footage_assets and footage_tags used to key on `clip_id TEXT
PRIMARY KEY` while carrying rows for source='video' AND source='photo'. clip_id is
the id embedded in a file name, so the two pools share an id space. A photo upsert
with a clip_id that already belonged to a video did not collide — it OVERWROTE the
video row and flipped `source` (`ON CONFLICT (clip_id) DO UPDATE SET ... source =
EXCLUDED.source`). The video left its pool silently: no error, no prune log, just a
smaller registry. It happened to 221 clips before the key became (source, clip_id).

Checks, all read-only:
  1. primary keys are composite — the fix itself, re-verified every run,
  2. untagged_assets: registry rows with no tag row IN THE SAME POOL. This is the
     damage signature; the picker joins assets to tags on source, so these clips
     cannot be selected. Repair = re-tag that pool,
  3. orphan_tag_rows: the mirror case, tags with no registry row,
  4. clip_id overlap between the video and photo S3 indexes. A finding only while
     a key is still single-column; afterwards it is the supported normal case.

`ok` is false when nothing was audited — an audit that never reached the database
must not report clean.

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

        # Post-migration both tables must be keyed by (source, clip_id). While the
        # key is still single-column every check below understates the damage,
        # because a clip cannot yet hold one row per pool.
        pkeys = await conn.fetch(
            """
            SELECT conrelid::regclass::text AS tbl, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE contype = 'p'
              AND conrelid::regclass::text IN ('footage_assets', 'footage_tags')
            """
        )
        primary_keys = {str(r["tbl"]): str(r["def"]) for r in pkeys}

        # THE damage signature: a registry row with no tag row for the SAME pool.
        # Those clips are invisible to the picker (it joins both sides on source).
        # Joins are composite on purpose — under the new key a clip_id legitimately
        # exists once per pool, so joining on clip_id alone would pair a video
        # asset with a photo tag and call the healthy case a finding.
        missing_tags = await conn.fetch(
            """
            SELECT a.source, COUNT(*) AS n
            FROM footage_assets a
            LEFT JOIN footage_tags t
              ON t.clip_id = a.clip_id AND t.source = a.source
            WHERE t.clip_id IS NULL
            GROUP BY a.source
            ORDER BY a.source
            """
        )

        orphan_tags = await conn.fetch(
            """
            SELECT t.source, COUNT(*) AS n
            FROM footage_tags t
            LEFT JOIN footage_assets a
              ON a.clip_id = t.clip_id AND a.source = t.source
            WHERE a.clip_id IS NULL
            GROUP BY t.source
            ORDER BY t.source
            """
        )

        # Informational after the migration, not a finding: this is exactly the
        # shared id space the composite key exists to support.
        both_pools = await conn.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT clip_id FROM footage_assets
                GROUP BY clip_id HAVING COUNT(DISTINCT source) > 1
            ) s
            """
        )
        return {
            "counts": counts,
            "primary_keys": primary_keys,
            "untagged_assets": {str(r["source"]): int(r["n"]) for r in missing_tags},
            "orphan_tag_rows": {str(r["source"]): int(r["n"]) for r in orphan_tags},
            "clip_ids_in_both_pools": int(both_pools or 0),
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
        for tbl, definition in sorted(report["db"]["primary_keys"].items()):
            if "(source, clip_id)" not in definition.replace('"', ""):
                findings.append(
                    f"primary_key: {tbl} is still keyed by {definition} — cross-pool "
                    "upserts keep overwriting rows until this is composite"
                )
        untagged = {k: v for k, v in report["db"]["untagged_assets"].items() if v}
        if untagged:
            findings.append(
                f"untagged_assets: {untagged} — registry rows with no tag row in the "
                "same pool; the picker joins on source, so these clips are unpickable"
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
        # Overlap is only a finding while the key is single-column. Once it is
        # composite the shared id space is supported, and flagging it forever
        # would train everyone to ignore this report.
        keys_are_composite = bool(report.get("db", {}).get("primary_keys")) and all(
            "(source, clip_id)" in d.replace('"', "")
            for d in report["db"]["primary_keys"].values()
        )
        if overlap and not keys_are_composite:
            findings.append(
                f"index_overlap: {len(overlap)} clip_id(s) exist in BOTH the video and "
                "photo S3 indexes — the next ingest WILL overwrite across pools"
            )
    else:
        report["index_overlap"] = {"skipped": "need --video-index and --photo-index"}

    report["findings"] = findings
    # An audit that reached no database checked nothing. Reporting that as "clean"
    # is the one failure mode a gate must never have, so a skipped DB is not a pass.
    report["db_audited"] = "skipped" not in report["db"]
    report["ok"] = report["db_audited"] and not findings
    report["safe_to_migrate"] = report["ok"]  # kept: runbooks read this key

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"ok": report["ok"], "db_audited": report["db_audited"], "findings": findings},
                     ensure_ascii=False, indent=2))
    print(f"full audit -> {out}")
    # Exit non-zero on findings so this can gate the migration in CI/a runbook.
    return 0 if report["safe_to_migrate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
