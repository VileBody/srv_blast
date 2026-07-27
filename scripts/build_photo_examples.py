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
from mlcore.photo_bucket_catalog import load_photo_catalog, evaluate, representative_score
import scripts.build_bucket_previews as bbp


def _mapped_from_snapshot(snapshot: Path):
    rows = fp.load_footage_style_metadata_rows(db_paths=[snapshot])
    index = fp.merge_footage_style_metadata_rows(rows)
    inv = [{"file_name": f"{c}.jpg"} for c in index]
    mapped, _ = fp.map_inventory_assets_with_style_metadata(assets=inv, metadata_index=index)
    return mapped


def _pick(bucket, mapped, top_n, seed):
    # Review reels must never silently include legacy photos that have not yet
    # passed the quality backfill. Production rollout has the same requirement
    # enforced by the photo readiness gate before the feature is enabled.
    m = [
        a for a in mapped
        if isinstance((a.get("meta_framing") or {}).get("quality"), dict)
        and evaluate(bucket, a)[0]
    ]
    m.sort(key=lambda a: (-representative_score(bucket, a), hashlib.sha256(f"{seed}:{a['file_name']}".encode()).hexdigest()))
    return m[:top_n]


def _register(catalog, *, out_dir: Path, store_path: Path, log) -> int:
    """Publish the reviewed reels to the bots: Telegram file_id per bucket, then
    rewrite the previews store so it holds EXACTLY the active buckets (stale
    entries from retired/merged buckets are dropped)."""
    store = bp.empty_store()
    sent = missing = 0
    for b in catalog:
        mp4 = out_dir / f"{b.bucket_id.replace(':', '__')}.mp4"
        if not mp4.exists():
            log.warning("no reel for %s (%s)", b.bucket_id, mp4.name); missing += 1; continue
        # previews are sent caption-less: the name lives on the video and button
        file_id, file_id_public = bbp._capture_file_ids(mp4, "")
        if not file_id and not file_id_public:
            log.error("no file_id captured for %s — check bot tokens / chat id", b.bucket_id)
            missing += 1
            continue
        bp.previews_upsert(store, bp.PreviewEntry(
            bucket_id=b.bucket_id, label=b.label, description=b.lead,
            file_id=file_id, file_id_public=file_id_public,
            status="ok", built_at=bp.now_iso(),
        ))
        bp.save_previews_store(store_path, store)
        log.info("registered %s file_id=%s public=%s", b.bucket_id,
                 (file_id[:10] + "…") if file_id else "-",
                 (file_id_public[:10] + "…") if file_id_public else "-")
        sent += 1
    log.info("register done: sent=%d missing=%d -> %s", sent, missing, store_path)
    return 1 if missing else 0


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
    ap.add_argument("--register", action="store_true",
                    help="skip rendering: send the already-built reels to the backlog chat, "
                         "capture Telegram file_id(s) and write the photo previews store "
                         "(what the bots actually read). Needs TG_BOT_TOKEN / "
                         "TG_PREVIEW_SOURCE_BOT_TOKEN + FOOTAGE_PREVIEW_BACKLOG_CHAT_ID.")
    ap.add_argument("--previews-path", default="data/photo_bucket_previews.json")
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

    if args.register:
        return _register(catalog, out_dir=_ROOT / args.out_dir,
                         store_path=_ROOT / args.previews_path, log=log)

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
