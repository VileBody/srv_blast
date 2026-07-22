#!/usr/bin/env python3
"""Emit, per photo bucket, the top-N representative stills + their tags — the
review artifact for spotting extraneous tags before rendering example reels.

Offline: photo catalog gate over the snapshot, deterministic top-N by facet-tag
overlap. Writes data/photo_bucket_examples/<bucket>.json + _index.json.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, tempfile
from pathlib import Path
from collections import Counter

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); os.environ.setdefault("MODE", "dev")
from mlcore import footage_picker as fp
from mlcore.photo_bucket_catalog import load_photo_catalog, evaluate, representative_score, _matches, _n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="data/photo_tags_snapshot_real.json")
    ap.add_argument("--out-dir", default="data/photo_bucket_examples")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--seed", default="photo_examples_v1")
    args = ap.parse_args(argv)

    snap = json.load(open(args.snapshot, encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "s.json"; sp.write_text(json.dumps(snap), encoding="utf-8")
        rows = fp.load_footage_style_metadata_rows(db_paths=[sp])
    index = fp.merge_footage_style_metadata_rows(rows)
    inv = [{"file_name": f"{c}.jpg"} for c in index]
    mapped, _ = fp.map_inventory_assets_with_style_metadata(assets=inv, metadata_index=index)

    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_photo_catalog()
    summary = []
    for b in catalog:
        m = [a for a in mapped if evaluate(b, a)[0]]
        m.sort(key=lambda a: (-representative_score(b, a), hashlib.sha256(f"{args.seed}:{a['file_name']}".encode()).hexdigest()))
        picks = m[: args.top_n]
        # extraneous-tag scan: tags on the picks that are NOT part of the bucket's
        # required facet vocabulary — the candidates for the user to prune.
        req = set(b.priority_tags)
        foreign = Counter()
        for a in picks:
            for t in (a.get("meta_theme_tags") or []):
                tn = _n(t)
                if not any(_matches((tn,), r) or _matches((r,), tn) for r in req):
                    foreign[str(t)] += 1
        rec = {
            "bucket_id": b.bucket_id,
            "label": b.label,
            "lead": b.lead,
            "facets": dict(b.facets),
            "colors": list(b.colors),
            "people": b.people,
            "pool_size": len(m),
            "shown": len(picks),
            "foreign_tags_on_picks": foreign.most_common(15),
            "photos": [
                {"clip_id": str(a.get("clip_id")), "color": a.get("meta_color_tone"),
                 "people": a.get("meta_people_type"), "tags": list(a.get("meta_theme_tags") or [])}
                for a in picks
            ],
        }
        (out_dir / f"{b.bucket_id.replace(':', '__')}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append({"bucket_id": b.bucket_id, "label": b.label, "lead": b.lead,
                        "pool_size": len(m), "facets": dict(b.facets)})
    (out_dir / "_index.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    covered = set()
    for b in catalog:
        for a in mapped:
            if evaluate(b, a)[0]: covered.add(id(a))
    print(f"buckets={len(catalog)} pool={len(mapped)} covered={len(covered)} ({round(100*len(covered)/len(mapped))}%) -> {out_dir}")
    for s in sorted(summary, key=lambda x: -x["pool_size"]):
        line = f'{s["pool_size"]:>4}  {s["bucket_id"]:<34} {s["lead"]}'
        sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
