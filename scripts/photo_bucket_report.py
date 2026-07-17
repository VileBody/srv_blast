#!/usr/bin/env python3
"""Measure the PHOTO pool of every visual bucket — the report that replaces
eyeballing the base.

For each contract in the visual catalog it reports the stills the production
picker would actually draw (same gate, media_type=photo), why the rest were
rejected, and which tags dominate what survives. That is enough to decide, per
bucket, whether it is empty, thin, healthy, oversized, or semantically mixed —
without opening a single image.

Read-only: SELECTs from Postgres (or reads snapshot/inventory files), builds
throwaway artifacts in a temp dir. It never writes the production caches.

Usage:
  # on a node (reads the durable registry)
  python scripts/photo_bucket_report.py --dsn "$CREDITS_DB_URL" -o report.json

  # offline, from exported artifacts
  python scripts/photo_bucket_report.py \
      --inventory data/photo_inventory.json --snapshot data/photo_tags_snapshot.json

Thresholds: --thin (default 15), --target-min/--target-max (default 100/150 —
the size band a large working group should land in).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mlcore import footage_picker as fp  # noqa: E402
from mlcore.footage_bucket_previews import _raw_pick_from_bucket  # noqa: E402
from mlcore.footage_visual_catalog import evaluate_asset, load_visual_catalog  # noqa: E402
from services.orchestrator.picker_readiness import (  # noqa: E402
    _materialize_inventory,
    load_pool_from_postgres,
)

MEDIA = "photo"


def _load_from_postgres(dsn: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    data = asyncio.run(load_pool_from_postgres(dsn, source=MEDIA))
    return data["records"], data["snapshot_rows"]


def _mapped_assets(
    *, records: List[Dict[str, Any]], snapshot_rows: List[Dict[str, Any]], workdir: Path
) -> Tuple[List[Dict[str, Any]], int]:
    """Registry+snapshot -> the mapped assets the picker sees (prod code path)."""
    picker_assets = _materialize_inventory(
        records=records, media_type=MEDIA, workdir=workdir
    )
    snap_path = workdir / "photo_tags_snapshot.json"
    snap_path.write_text(json.dumps(snapshot_rows, ensure_ascii=False), encoding="utf-8")
    rows = fp.load_footage_style_metadata_rows(db_paths=[snap_path])
    index = fp.merge_footage_style_metadata_rows(rows)
    mapped, unmapped = fp.map_inventory_assets_with_style_metadata(
        assets=picker_assets, metadata_index=index
    )
    return mapped, len(unmapped)


def _mapped_from_files(inventory: Path, snapshot: Path) -> Tuple[List[Dict[str, Any]], int]:
    inv = json.loads(Path(inventory).read_text(encoding="utf-8"))
    picker_assets = fp.load_picker_assets_from_inventory(inv)
    rows = fp.load_footage_style_metadata_rows(db_paths=[Path(snapshot)])
    index = fp.merge_footage_style_metadata_rows(rows)
    mapped, unmapped = fp.map_inventory_assets_with_style_metadata(
        assets=picker_assets, metadata_index=index
    )
    return mapped, len(unmapped)


def _status(size: int, *, thin: int, target_min: int, target_max: int) -> str:
    if size == 0:
        return "empty"
    if size < thin:
        return "thin"
    if size > target_max:
        return "oversized"
    if size >= target_min:
        return "healthy"
    return "small"


def size_bucket(
    contract, mapped: List[Dict[str, Any]], *, thin: int, target_min: int, target_max: int
) -> Dict[str, Any]:
    pool = fp._build_raw_pool(_raw_pick_from_bucket(contract), mapped, media_type=MEDIA)

    # Why everything else was rejected — the calibration signal.
    stages: Counter = Counter()
    for asset in mapped:
        ok, stage, _ = evaluate_asset(contract, asset, media_type=MEDIA)
        if not ok:
            stages[stage] += 1

    # What actually survived — a bucket whose survivors are dominated by tags from
    # a neighbouring theme is semantically mixed, and this is where you see it.
    tags: Counter = Counter()
    for it in pool:
        for t in (it.get("meta_theme_tags") or []):
            tags[str(t)] += 1

    size = len(pool)
    return {
        "bucket_id": contract.bucket_id,
        "label": contract.label,
        "pool_size": size,
        "status": _status(size, thin=thin, target_min=target_min, target_max=target_max),
        "photo_gate_rejects": stages.get("photo_missing_anchor", 0)
        + stages.get("photo_semantic_exclude", 0),
        "reject_stages": dict(stages.most_common()),
        "top_tags": [{"tag": t, "n": n} for t, n in tags.most_common(12)],
    }


def build_report(
    *,
    mapped: List[Dict[str, Any]],
    unmapped: int,
    thin: int,
    target_min: int,
    target_max: int,
) -> Dict[str, Any]:
    rows = [
        size_bucket(c, mapped, thin=thin, target_min=target_min, target_max=target_max)
        for c in load_visual_catalog()
    ]
    rows.sort(key=lambda r: (r["pool_size"], r["bucket_id"]))
    by_status: Counter = Counter(r["status"] for r in rows)
    sizes = [r["pool_size"] for r in rows] or [0]
    return {
        "pool": {
            "mapped_assets": len(mapped),
            "unmapped_assets": unmapped,
            "media_type": MEDIA,
        },
        "thresholds": {"thin": thin, "target_min": target_min, "target_max": target_max},
        "summary": {
            "buckets": len(rows),
            "by_status": dict(by_status),
            "pool_min": min(sizes),
            "pool_median": sorted(sizes)[len(sizes) // 2],
            "pool_max": max(sizes),
        },
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Photo pool report per visual bucket.")
    ap.add_argument("--dsn", default="", help="Postgres DSN (default: CREDITS_DB_URL).")
    ap.add_argument("--inventory", default="", help="Offline: photo inventory JSON.")
    ap.add_argument("--snapshot", default="", help="Offline: photo tags snapshot JSON.")
    ap.add_argument("-o", "--out", default="data/photo_bucket_report.json")
    ap.add_argument("--thin", type=int, default=15)
    ap.add_argument("--target-min", type=int, default=100)
    ap.add_argument("--target-max", type=int, default=150)
    args = ap.parse_args(argv)

    import os

    if args.inventory and args.snapshot:
        mapped, unmapped = _mapped_from_files(Path(args.inventory), Path(args.snapshot))
        report = build_report(
            mapped=mapped, unmapped=unmapped, thin=args.thin,
            target_min=args.target_min, target_max=args.target_max,
        )
    else:
        dsn = (args.dsn or os.environ.get("CREDITS_DB_URL") or "").strip()
        if not dsn:
            print("need --dsn/CREDITS_DB_URL, or --inventory + --snapshot", file=sys.stderr)
            return 2
        records, snapshot_rows = _load_from_postgres(dsn)
        with tempfile.TemporaryDirectory(prefix="photo_report_") as tmp:
            mapped, unmapped = _mapped_assets(
                records=records, snapshot_rows=snapshot_rows, workdir=Path(tmp)
            )
            report = build_report(
                mapped=mapped, unmapped=unmapped, thin=args.thin,
                target_min=args.target_min, target_max=args.target_max,
            )
        report["pool"]["registry_rows"] = len(records)
        report["pool"]["snapshot_rows"] = len(snapshot_rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    p, s = report["pool"], report["summary"]
    print(f"photo pool: mapped={p['mapped_assets']} unmapped={p['unmapped_assets']}")
    print(
        f"buckets={s['buckets']} min/median/max={s['pool_min']}/{s['pool_median']}/{s['pool_max']} "
        f"status={s['by_status']}"
    )
    print(f"\n{'pool':>5}  {'status':<10} bucket_id")
    for r in report["rows"]:
        print(f"{r['pool_size']:>5}  {r['status']:<10} {r['bucket_id']}")
    print(f"\nfull report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
