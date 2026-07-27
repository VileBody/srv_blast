#!/usr/bin/env python3
"""Backfill framing into a local photo tag snapshot from downloaded images.

This is the offline/review counterpart of ``run_photo_framing_batch``.  It never
changes semantic tags and writes a new snapshot unless ``--in-place`` is used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlcore.photo_framing import OpenCvYoloXDetector, analyze_photo_framing
from mlcore.photo_quality import attach_photo_quality


def _candidate_names(row: Dict[str, Any]) -> Iterable[str]:
    for key in ("file_name", "video_path", "video_key"):
        raw = str(row.get(key) or "").strip().replace("\\", "/")
        if raw:
            yield raw.rsplit("/", 1)[-1]


def enrich_snapshot(
    *,
    snapshot_path: Path,
    images_dir: Path,
    output_path: Path,
    model_path: Path,
    only_missing: bool = True,
) -> Dict[str, int]:
    rows = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError(f"photo snapshot root must be a list: {snapshot_path}")
    by_name = {p.name: p for p in images_dir.rglob("*") if p.is_file()}
    detector = OpenCvYoloXDetector(model_path)
    analyzed = missing_file = failed = skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            only_missing
            and isinstance(row.get("framing"), dict)
            and isinstance(row["framing"].get("quality"), dict)
        ):
            skipped += 1
            continue
        image = next((by_name[name] for name in _candidate_names(row) if name in by_name), None)
        if image is None:
            missing_file += 1
            continue
        try:
            existing = row.get("framing")
            if isinstance(existing, Mapping) and existing:
                framing = dict(existing)
            else:
                framing = analyze_photo_framing(
                    image,
                    theme_tags=row.get("theme_tags") or [],
                    people_type=row.get("people_type") or "none",
                    detector=detector,
                )
            row["framing"] = attach_photo_quality(framing, image)
            analyzed += 1
        except Exception:
            failed += 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "rows": len(rows),
        "analyzed": analyzed,
        "skipped": skipped,
        "missing_file": missing_file,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("images_dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/photo_tags_snapshot_framed.json"),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("data/models/object_detection_yolox_2022nov.onnx"),
    )
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    output = args.snapshot if args.in_place else args.output
    summary = enrich_snapshot(
        snapshot_path=args.snapshot,
        images_dir=args.images_dir,
        output_path=output,
        model_path=args.model,
        only_missing=not args.refresh,
    )
    print(json.dumps({**summary, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
