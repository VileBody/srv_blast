#!/usr/bin/env python3
"""Per-bucket photo dump + tag histogram, for the exclude-tag analysis.

For each visual bucket it collects every photo the CURRENT photo gate admits,
with its full tags, and a tag-frequency histogram over the admitted set — the
raw material for deciding which tags contaminate a bucket's lead theme.

Read-only, offline: reads a photo tags snapshot (video_database shape) and the
visual catalog. Writes data/photo_bucket_analysis/<bucket>.json + a summary.

Usage:
  python scripts/photo_bucket_analysis.py --snapshot data/photo_tags_snapshot_real.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("MODE", "dev")

from mlcore import footage_picker as fp  # noqa: E402
from mlcore.footage_bucket_previews import _raw_pick_from_bucket  # noqa: E402
from mlcore.footage_visual_catalog import evaluate_asset, load_visual_catalog  # noqa: E402


def _mapped_from_snapshot(snapshot_path: Path) -> List[Dict[str, Any]]:
    rows = fp.load_footage_style_metadata_rows(db_paths=[snapshot_path])
    index = fp.merge_footage_style_metadata_rows(rows)
    inv = [{"file_name": f"{cid}.jpg"} for cid in index]
    mapped, _ = fp.map_inventory_assets_with_style_metadata(assets=inv, metadata_index=index)
    return mapped


def analyse(snapshot_path: Path, out_dir: Path) -> Dict[str, Any]:
    mapped = _mapped_from_snapshot(snapshot_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {"pool": len(mapped), "buckets": {}}
    for c in load_visual_catalog():
        passing = []
        for a in mapped:
            ok, _, _ = evaluate_asset(c, a, media_type="photo")
            if ok:
                passing.append(a)
        # scored pool order (what the picker would actually pick from)
        pool = fp._build_raw_pool(_raw_pick_from_bucket(c), mapped, media_type="photo")

        tag_hist: Counter = Counter()
        color_hist: Counter = Counter()
        people_hist: Counter = Counter()
        for a in passing:
            for t in (a.get("meta_theme_tags") or []):
                tag_hist[str(t)] += 1
            color_hist[str(a.get("meta_color_tone") or "")] += 1
            people_hist[str(a.get("meta_people_type") or "")] += 1

        photos = [
            {
                "clip_id": str(a.get("clip_id") or Path(a["file_name"]).stem),
                "color": a.get("meta_color_tone"),
                "people": a.get("meta_people_type"),
                "tags": list(a.get("meta_theme_tags") or []),
            }
            for a in passing
        ]
        rec = {
            "bucket_id": c.bucket_id,
            "label": c.label,
            "require_groups": [list(g) for g in c.require_groups],
            "colors": list(c.colors),
            "people": c.people,
            "count_passing": len(passing),
            "count_pool": len(pool),
            "color_hist": dict(color_hist.most_common()),
            "people_hist": dict(people_hist.most_common()),
            "tag_hist": tag_hist.most_common(60),
            "photos": photos,
        }
        (out_dir / f"{c.bucket_id.replace(':', '__')}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary["buckets"][c.bucket_id] = {
            "label": c.label,
            "count": len(passing),
            "top_tags": tag_hist.most_common(30),
        }
    (out_dir / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="data/photo_tags_snapshot_real.json")
    ap.add_argument("--out-dir", default="data/photo_bucket_analysis")
    args = ap.parse_args(argv)
    summary = analyse(Path(args.snapshot), Path(args.out_dir))
    print(f"pool={summary['pool']} buckets={len(summary['buckets'])}")
    for bid, e in sorted(summary["buckets"].items(), key=lambda kv: -kv[1]["count"]):
        top = " ".join(f"{t}:{n}" for t, n in e["top_tags"][:8])
        line = f"{e['count']:>4}  {bid}\n        {top}"
        sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
