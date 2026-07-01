#!/usr/bin/env python3
"""Measure the picker's clip pool size for every footage BUCKET = (theme,
tags_group), so we can see which buckets are THIN (too few clips -> picker
widens/repeats -> off-target) and which are FAT after a tagging/ingest run.

A bucket's pool = the clips that (a) match the bucket mood, (b) share >=1 of the
bucket's priority tags, and (c) survive the bucket's people/tag exclusions. This
is exactly what the production picker draws from (mlcore.footage_picker
_build_raw_pool + the mood pre-filter from resolve_style_pick_from_raw_filters),
so the counts here are the real per-bucket depth the user experiences.

Data axes (see FOOTAGE_PIPELINE.md):
  - POOL (inventory)  = data/static_assets_index_1to1.json (S3 scan). --inventory
  - TAGS (metadata)   = the footage_tags snapshot the picker reads, via
    FOOTAGE_STYLE_METADATA_DB_PATHS_JSON. Falls back to the legacy
    2nd_footage_selection_prompt/video_database*.json when that env is unset
    (LEGACY tags — for the freshly grown base, export a snapshot and point the
    env at it, or pass --metadata <snapshot.json> ...).

Bucket catalog = mlcore.footage_bucket_catalog (parsed from footage_v2.py THEMES
LOGIC). By default reports the DEDUPED catalog (visual twins collapsed); use
--all-buckets to see every raw (theme, tags_group) pair.

Usage:
  python scripts/footage_bucket_pool_report.py [out_report.json]
      [--inventory data/static_assets_index_1to1.json]
      [--metadata a.json --metadata b.json]   # overrides env / legacy default
      [--thin N] [--fat M] [--all-buckets] [--top K]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlcore import footage_picker as fp  # noqa: E402
from mlcore.footage_bucket_catalog import (  # noqa: E402
    Bucket,
    build_buckets,
    get_bucket_catalog,
)
from mlcore.footage_bucket_previews import _raw_pick_from_bucket  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = "data/footage_bucket_pool_report.json"
_DEFAULT_INVENTORY = _ROOT / "data" / "static_assets_index_1to1.json"
_LEGACY_METADATA = [
    _ROOT / "2nd_footage_selection_prompt" / "video_database (2).json",
    _ROOT / "2nd_footage_selection_prompt" / "video_database2.json",
]


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_inventory_assets(path: Path) -> List[Dict[str, Any]]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return [a for a in obj if isinstance(a, dict)]
    for key in ("assets", "items", "videos"):
        v = obj.get(key)
        if isinstance(v, list):
            return [a for a in v if isinstance(a, dict)]
    raise RuntimeError(f"inventory index has no assets list: {path}")


def resolve_metadata_paths(cli_paths: List[str]) -> Tuple[List[Path], str]:
    if cli_paths:
        return [Path(p) for p in cli_paths], "cli"
    raw = (os.environ.get("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON") or "").strip()
    if raw:
        try:
            arr = json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"Invalid FOOTAGE_STYLE_METADATA_DB_PATHS_JSON: {e!r}") from e
        if isinstance(arr, list) and arr:
            return [Path(str(p)) for p in arr], "env FOOTAGE_STYLE_METADATA_DB_PATHS_JSON"
    return [p for p in _LEGACY_METADATA if p.exists()], "legacy video_database JSONs"


def build_mapped_assets(
    inventory: List[Dict[str, Any]], metadata_paths: List[Path]
) -> Tuple[List[Dict[str, Any]], int, int]:
    rows = fp.load_footage_style_metadata_rows(db_paths=metadata_paths)
    merged = fp.merge_footage_style_metadata_rows(rows)
    mapped, unmapped = fp.map_inventory_assets_with_style_metadata(
        assets=inventory, metadata_index=merged
    )
    return mapped, len(merged), len(unmapped)


# --------------------------------------------------------------------------- #
# Per-bucket sizing (prod-faithful: mood pre-filter + _build_raw_pool)
# --------------------------------------------------------------------------- #
def _mood_pool(mapped_assets: List[Dict[str, Any]], mood: str) -> List[Dict[str, Any]]:
    m = fp._normalize_mood(mood)
    if not m:
        return list(mapped_assets)
    return [it for it in mapped_assets if fp._normalize_mood(it.get("meta_mood")) == m]


def size_bucket(bucket: Bucket, mapped_by_mood: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    raw_pick = _raw_pick_from_bucket(bucket)
    candidates = mapped_by_mood.get(fp._normalize_mood(bucket.mood), [])
    pool = fp._build_raw_pool(raw_pick, candidates)
    color_hits = 0
    genre_tags: set = set()
    for it in pool:
        if float(it.get(fp._SELECTION_RANK_SCORE_KEY) or 0.0) - float(int(it.get(fp._SELECTION_RANK_SCORE_KEY) or 0.0)) >= 0.5:
            color_hits += 1
        g = str(it.get("genre") or "").strip()
        t = str(it.get("tag") or "").strip()
        if g and t:
            genre_tags.add(f"{g}/{t}")
    return {
        "bucket_id": bucket.bucket_id,
        "theme": bucket.theme,
        "tags_group": bucket.tags_group,
        "mood": bucket.mood,
        "label": bucket.label,
        "priority_tags": list(bucket.priority_tags),
        "pool_size": len(pool),
        "color_hits": color_hits,
        "distinct_inventory_groups": len(genre_tags),
        "mood_candidates": len(candidates),
    }


def build_report(
    *,
    inventory_path: Path,
    metadata_paths: List[Path],
    all_buckets: bool,
    thin: int,
    fat: int,
) -> Dict[str, Any]:
    inventory = load_inventory_assets(inventory_path)
    mapped, merged_rows, unmapped = build_mapped_assets(inventory, metadata_paths)

    src = build_buckets() if all_buckets else get_bucket_catalog()
    mapped_by_mood: Dict[str, List[Dict[str, Any]]] = {
        "major": _mood_pool(mapped, "major"),
        "minor": _mood_pool(mapped, "minor"),
        "": list(mapped),
    }

    rows = [size_bucket(b, mapped_by_mood) for b in src]
    rows.sort(key=lambda r: (r["pool_size"], r["bucket_id"]))

    thin_rows = [r for r in rows if r["pool_size"] < thin]
    fat_rows = [r for r in rows if r["pool_size"] >= fat]
    sizes = [r["pool_size"] for r in rows] or [0]
    return {
        "inventory": {
            "path": str(inventory_path),
            "assets": len(inventory),
            "mapped_to_tags": len(mapped),
            "unmapped": unmapped,
            "metadata_rows_merged": merged_rows,
            "metadata_paths": [str(p) for p in metadata_paths],
        },
        "buckets": {
            "counted": len(rows),
            "mode": "all (theme,tags_group)" if all_buckets else "deduped catalog",
            "thin_threshold": thin,
            "fat_threshold": fat,
            "thin_count": len(thin_rows),
            "fat_count": len(fat_rows),
            "pool_min": min(sizes),
            "pool_max": max(sizes),
            "pool_median": sorted(sizes)[len(sizes) // 2],
        },
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _pop_flag_value(args: List[str], flag: str) -> Optional[str]:
    if flag in args:
        i = args.index(flag)
        val = args[i + 1]
        del args[i : i + 2]
        return val
    return None


def _pop_multi(args: List[str], flag: str) -> List[str]:
    out: List[str] = []
    while flag in args:
        i = args.index(flag)
        out.append(args[i + 1])
        del args[i : i + 2]
    return out


def main() -> int:
    args = list(sys.argv[1:])
    all_buckets = False
    if "--all-buckets" in args:
        all_buckets = True
        args.remove("--all-buckets")
    thin = int(_pop_flag_value(args, "--thin") or 15)
    fat = int(_pop_flag_value(args, "--fat") or 120)
    top = int(_pop_flag_value(args, "--top") or 20)
    inv = _pop_flag_value(args, "--inventory")
    meta = _pop_multi(args, "--metadata")
    out_path = Path(args[0] if args else _DEFAULT_OUT)

    inventory_path = Path(inv) if inv else _DEFAULT_INVENTORY
    metadata_paths, meta_src = resolve_metadata_paths(meta)

    report = build_report(
        inventory_path=inventory_path,
        metadata_paths=metadata_paths,
        all_buckets=all_buckets,
        thin=thin,
        fat=fat,
    )
    report["metadata_source"] = meta_src

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    inv_i = report["inventory"]
    b = report["buckets"]
    print(f"inventory={inv_i['assets']} mapped_to_tags={inv_i['mapped_to_tags']} "
          f"unmapped={inv_i['unmapped']} tag_rows={inv_i['metadata_rows_merged']} "
          f"(tags source: {meta_src})")
    print(f"buckets[{b['mode']}]={b['counted']} pool min/median/max="
          f"{b['pool_min']}/{b['pool_median']}/{b['pool_max']} "
          f"thin(<{thin})={b['thin_count']} fat(>={fat})={b['fat_count']}")
    print(f"\nTHINNEST {min(top, len(report['rows']))} buckets (grow these):")
    print(f"  {'pool':>4} {'grp':>3} {'clr':>3}  bucket_id")
    for r in report["rows"][:top]:
        print(f"  {r['pool_size']:>4} {r['distinct_inventory_groups']:>3} "
              f"{r['color_hits']:>3}  {r['bucket_id']}")
    print(f"\nFATTEST {min(top, len(report['rows']))} buckets:")
    for r in report["rows"][-top:][::-1]:
        print(f"  {r['pool_size']:>4} {r['distinct_inventory_groups']:>3} "
              f"{r['color_hits']:>3}  {r['bucket_id']}")
    print(f"\nfull report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
