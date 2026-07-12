#!/usr/bin/env python3
"""Validate final preview sources and emit bot/site render manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def is_mp4(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as fh:
        head = fh.read(8)
    return len(head) == 8 and head[4:8] == b"ftyp"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds-per-clip", type=float, default=1.5)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    source_root = args.source_root.resolve()
    variants = {
        "bot": {
            "with_on_video_label": True,
            "capture_telegram_file_ids": True,
            "store": "data/footage_bucket_previews.json",
            "s3_prefix": "footage_bucket_previews/v2/bot",
        },
        "site": {
            "with_on_video_label": False,
            "capture_telegram_file_ids": False,
            "store": "data/footage_bucket_previews_site.json",
            "s3_prefix": "footage_bucket_previews/v2/site",
        },
    }
    buckets_out = []
    missing = []
    invalid = []
    for bucket in catalog.get("buckets") or []:
        bucket_id = str(bucket["bucket_id"])
        slug = bucket_id.split(":", 1)[1]
        sources = []
        for asset_id in bucket.get("sources") or []:
            path = source_root / slug / f"{asset_id}.mp4"
            if not path.exists():
                missing.append(str(path))
                continue
            if not is_mp4(path):
                invalid.append(str(path))
                continue
            sources.append(
                {
                    "asset_id": str(asset_id),
                    "file_name": path.name,
                    "source_path": str(path),
                    "relpath": f"media/video/{path.name}",
                    "size_bytes": path.stat().st_size,
                }
            )
        if len(sources) < 3:
            raise SystemExit(f"thin preview source set for {bucket_id}: {len(sources)}")
        rendered = {}
        for variant, config in variants.items():
            rendered[variant] = {
                "output_file": f"outputs/bucket_previews_v2/{variant}/{slug}.mp4",
                "s3_key": f"{config['s3_prefix']}/{slug}.mp4",
                "store": config["store"],
                "capture_telegram_file_ids": config["capture_telegram_file_ids"],
                "montage_spec": {
                    "comp_name": f"Bucket Preview {variant}",
                    "width": 1080,
                    "height": 1920,
                    "fps": 23.976,
                    "seconds_per_clip": args.seconds_per_clip,
                    "label": bucket.get("label_ru", "") if config["with_on_video_label"] else "",
                    "label_font": "Point-Regular",
                    "clips": [
                        {"file_name": source["file_name"], "relpath": source["relpath"]}
                        for source in sources
                    ],
                },
            }
        buckets_out.append(
            {
                "bucket_id": bucket_id,
                "label_ru": bucket.get("label_ru", ""),
                "potential": int(bucket.get("potential") or 0),
                "sources": sources,
                "variants": rendered,
            }
        )

    if missing or invalid:
        raise SystemExit(f"source validation failed: missing={len(missing)} invalid={len(invalid)}")
    payload = {
        "schema_version": 1,
        "status": "ready_to_render",
        "catalog": str(args.catalog),
        "source_root": str(source_root),
        "bucket_count": len(buckets_out),
        "source_slot_count": sum(len(item["sources"]) for item in buckets_out),
        "render_count": len(buckets_out) * len(variants),
        "variants": variants,
        "buckets": buckets_out,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"ready: buckets={payload['bucket_count']} sources={payload['source_slot_count']} "
        f"renders={payload['render_count']} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
