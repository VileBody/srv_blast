#!/usr/bin/env python3
"""Build example reels for the PHOTO bucket catalog (separate plane from footage).

Per photo bucket: facet-gate the snapshot -> top-N stills -> pull from asset_ui ->
local-AE montage (1920x1440) -> outputs/photo_bucket_examples/<bucket>.mp4, and a
per-bucket JSON (chosen photos + their tags) for spotting extraneous tags.

Reuses the montage machinery from footage_bucket_previews / build_bucket_previews
but drives it off mlcore.photo_bucket_catalog, so photo and video never share a
catalog. Local mode only (asset_ui presigned pull + local AE); --no-telegram.

Env: ASSET_UI_USER / ASSET_UI_PASS, MODE=dev.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); os.environ.setdefault("MODE", "dev")

from mlcore import footage_picker as fp
from mlcore import footage_bucket_previews as bp
from mlcore.photo_bucket_catalog import load_photo_catalog, evaluate, _n
import scripts.build_bucket_previews as bbp


def _mapped_from_snapshot(snapshot: Path):
    rows = fp.load_footage_style_metadata_rows(db_paths=[snapshot])
    index = fp.merge_footage_style_metadata_rows(rows)
    inv = [{"file_name": f"{c}.jpg"} for c in index]
    mapped, _ = fp.map_inventory_assets_with_style_metadata(assets=inv, metadata_index=index)
    return mapped


def _score(bucket, a):
    tags = [_n(t) for t in (a.get("meta_theme_tags") or [])]
    req = set(bucket.priority_tags)
    return sum(1 for t in tags if any(r == t or r in t for r in req))


def _pick(bucket, mapped, top_n, seed):
    m = [a for a in mapped if evaluate(bucket, a)[0]]
    m.sort(key=lambda a: (-_score(bucket, a), hashlib.sha256(f"{seed}:{a['file_name']}".encode()).hexdigest()))
    return m[:top_n]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="data/photo_tags_snapshot_real.json")
    ap.add_argument("--only", nargs="*", default=None, help="specific bucket_id(s)")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--seed", default="photo_examples_v1")
    ap.add_argument("--asset-ui-url", default="https://blast808.com/admin/assets/api")
    ap.add_argument("--local-dir", default="C:/photo_examples_src")
    ap.add_argument("--out-dir", default="outputs/photo_bucket_examples")
    ap.add_argument("--json-dir", default="data/photo_bucket_examples")
    ap.add_argument("--min-clips", type=int, default=3)
    ap.add_argument("--render-timeout-s", type=float, default=600.0)
    args = ap.parse_args(argv)

    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("photo_examples")

    mapped = _mapped_from_snapshot(Path(args.snapshot))
    catalog = load_photo_catalog()
    if args.only:
        want = set(args.only); catalog = [b for b in catalog if b.bucket_id in want]
    out_dir = _ROOT / args.out_dir; out_dir.mkdir(parents=True, exist_ok=True)
    json_dir = _ROOT / args.json_dir; json_dir.mkdir(parents=True, exist_ok=True)
    local_dir = Path(args.local_dir); local_dir.mkdir(parents=True, exist_ok=True)
    auth = (os.environ.get("ASSET_UI_USER", ""), os.environ.get("ASSET_UI_PASS", ""))
    tmpl = (_ROOT / "templates" / "bucket_preview" / "photo_montage_template.jsx").read_text(encoding="utf-8")
    ae_workdir = Path(r"C:\ae_jobs\photo_examples"); ae_workdir.mkdir(parents=True, exist_ok=True)

    built = thin = failed = 0
    for b in catalog:
        picks = _pick(b, mapped, args.top_n, args.seed)
        # per-bucket review JSON (always, even if thin)
        rec = {"bucket_id": b.bucket_id, "label": b.label, "lead": b.lead,
               "facets": dict(b.facets), "colors": list(b.colors), "people": b.people,
               "shown": len(picks),
               "photos": [{"clip_id": str(a.get("clip_id")), "color": a.get("meta_color_tone"),
                           "people": a.get("meta_people_type"), "tags": list(a.get("meta_theme_tags") or [])}
                          for a in picks]}
        (json_dir / f"{b.bucket_id.replace(':', '__')}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        if len(picks) < args.min_clips:
            log.warning("THIN %s: %d clips", b.bucket_id, len(picks)); thin += 1; continue

        needed = [p["file_name"] for p in picks]
        bbp.pull_clips_from_asset_ui(needed, base_url=args.asset_ui_url, dest_dir=local_dir,
                                     auth=auth if auth[0] else None, media_type="photo")
        local_index = bbp.index_local_footage(local_dir, media="photo")
        clips = [{**p, "_local_path": local_index[p["file_name"]]} for p in picks
                 if p["file_name"] in local_index]
        if len(clips) < args.min_clips:
            log.warning("THIN(after pull) %s: %d", b.bucket_id, len(clips)); thin += 1; continue

        spec = bp.build_photo_montage_spec(b, clips)
        render_jsx = bp.render_montage_jsx(spec, tmpl)
        job_id = f"photoex_{b.bucket_id.replace(':', '__')}"
        try:
            mp4 = bbp.render_montage_local(clips=clips, render_jsx=render_jsx,
                                           comp_name="Photo Bucket Preview", job_id=job_id,
                                           ae_bin="", aerender_bin="", workdir=ae_workdir,
                                           timeout_s=args.render_timeout_s)
        except Exception as e:
            log.exception("FAILED %s: %r", b.bucket_id, e); failed += 1; continue
        keep = out_dir / f"{b.bucket_id.replace(':', '__')}.mp4"
        shutil.copy2(mp4, keep)
        log.info("built %s -> %s (%d photos)", b.bucket_id, keep.name, len(clips))
        built += 1
    print(f"done: built={built} thin={thin} failed={failed}  mp4->{out_dir}  json->{json_dir}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
