#!/usr/bin/env python3
"""Validate and summarize the resolved semantic visual catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


CURRENT_RAW_SLOTS = 48
CURRENT_TRACK_THEMES = 25
SEMANTIC_RISK = {
    "hustle_minor": "high: no verified luxury/static-brand bucket",
    "nostalgia_city_minor": "high: no verified retro/lofi bucket",
    "sex_minor": "medium: no standalone dark-intimate-detail bucket",
    "mysticism_fate_minor": "medium: no standalone gothic-architecture bucket",
    "cyber_alienation_minor": "low-medium: cyberpunk city removed; glitch/silhouette remain",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    buckets = list(catalog.get("buckets") or [])
    themes = dict(catalog.get("theme_buckets") or {})
    ids = [str(bucket.get("bucket_id") or "") for bucket in buckets]
    if len(ids) != len(set(ids)) or not all(item.startswith("visual:") for item in ids):
        raise SystemExit("invalid or duplicate visual bucket IDs")
    if len(themes) != CURRENT_TRACK_THEMES:
        raise SystemExit(f"expected {CURRENT_TRACK_THEMES} themes, got {len(themes)}")
    potentials = {str(bucket["bucket_id"]): int(bucket.get("potential") or 0) for bucket in buckets}
    unknown = {theme: sorted(set(refs) - set(ids)) for theme, refs in themes.items() if set(refs) - set(ids)}
    if unknown:
        raise SystemExit(f"unknown visual IDs in theme map: {unknown}")
    total = sum(potentials.values())
    payload = {
        "status": "final_resolved_audit",
        "snapshot_rows": int(catalog.get("snapshot_rows") or 0),
        "current_raw_slots": CURRENT_RAW_SLOTS,
        "final_visual_slots": len(buckets),
        "slot_reduction": CURRENT_RAW_SLOTS - len(buckets),
        "slot_reduction_pct": round((CURRENT_RAW_SLOTS - len(buckets)) / CURRENT_RAW_SLOTS * 100.0, 1),
        "track_themes_covered": len(themes),
        "total_slot_potential": total,
        "total_slot_potential_vs_snapshot_pct": round(total / int(catalog["snapshot_rows"]) * 100.0, 1),
        "average_slot_potential": round(total / len(buckets), 1),
        "thin_slots": [
            {
                "bucket_id": bucket["bucket_id"],
                "potential": int(bucket.get("potential") or 0),
                "family_potential": int(bucket.get("family_potential") or 0),
                "fallback_potential": int(bucket.get("fallback_potential") or 0),
            }
            for bucket in buckets
            if int(bucket.get("potential") or 0) < 30
        ],
        "coverage_note": "slot potential is not unique asset coverage because compatible buckets may overlap",
        "theme_report": {
            theme: {
                "bucket_count": len(refs),
                "slot_potential_sum": sum(potentials[ref] for ref in refs),
                "semantic_risk": SEMANTIC_RISK.get(theme, "normal"),
            }
            for theme, refs in themes.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "final_visual_slots", "slot_reduction_pct", "track_themes_covered",
        "total_slot_potential", "average_slot_potential", "thin_slots"
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
