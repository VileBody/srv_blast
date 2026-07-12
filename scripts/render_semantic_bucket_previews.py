#!/usr/bin/env python3
"""Render the approved semantic preview wave from curated local sources.

Two deterministic variants are produced from identical source clips:
``bot`` has the bucket label burned in and is registered in both Telegram bots;
``site`` has no label and is stored separately for the website.
"""
from __future__ import annotations

import os
import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlcore import footage_bucket_previews as bp  # noqa: E402
from mlcore.footage_visual_catalog import load_visual_catalog  # noqa: E402
from scripts.build_bucket_previews import (  # noqa: E402
    _capture_file_ids,
    _montage_template_text,
    render_montage_local,
)

log = logging.getLogger("render_semantic_bucket_previews")


def _entry(contract, clips, *, existing=None, file_ids=("", "")) -> bp.PreviewEntry:
    previous = existing or {}
    return bp.PreviewEntry(
        bucket_id=contract.bucket_id,
        label=bp.display_label(contract.label),
        description=contract.label,
        s3_url=str(previous.get("s3_url") or ""),
        file_id=file_ids[0],
        file_id_public=file_ids[1],
        clip_ids=[Path(str(x["file_name"])).stem for x in clips],
        status="ok",
        built_at=bp.now_iso(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/footage_semantic_preview_wave_manifest.json")
    ap.add_argument("--variant", choices=("bot", "site", "both"), default="both")
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--register-only", action="store_true")
    ap.add_argument("--ae-bin", default=r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.com")
    ap.add_argument("--aerender-bin", default=r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\aerender.exe")
    ap.add_argument("--workdir", default=r"C:\ae_jobs\bucket_previews_v2")
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    manifest_path = (ROOT / args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = {x.bucket_id: x for x in load_visual_catalog()}
    wanted = set(args.only)
    variants = ("bot", "site") if args.variant == "both" else (args.variant,)
    rendered = registered = skipped = 0
    os.environ.setdefault("BUCKET_PREVIEW_AE_NEW_INSTANCE", "1")

    for variant in variants:
        variant_cfg = manifest["variants"][variant]
        store_path = ROOT / variant_cfg["store"]
        store = bp.load_previews_store(store_path)
        output_dir = ROOT / "outputs" / "bucket_previews_v2" / variant
        output_dir.mkdir(parents=True, exist_ok=True)
        for row in manifest["buckets"]:
            bid = str(row["bucket_id"])
            if wanted and bid not in wanted:
                continue
            contract = contracts[bid]
            variant_row = row["variants"][variant]
            final_mp4 = ROOT / variant_row["output_file"]
            existing = (store.get("previews") or {}).get(bid) or {}
            if not args.force and not args.register_only and final_mp4.exists() and bp.has_preview(store, bid):
                skipped += 1
                continue
            clips = []
            for source in row["sources"]:
                path = Path(source["source_path"])
                if not path.is_file() or path.stat().st_size <= 0:
                    raise RuntimeError(f"missing preview source {bid}: {path}")
                clips.append({"file_name": source["file_name"], "_local_path": str(path)})

            if not args.register_only:
                spec = bp.build_montage_spec(
                    contract, clips, comp_name=f"Bucket Preview {variant}",
                    with_label=bool(variant_cfg["with_on_video_label"]),
                )
                jsx = bp.render_montage_jsx(spec, _montage_template_text())
                local_mp4 = render_montage_local(
                    clips=clips, render_jsx=jsx, comp_name=f"Bucket Preview {variant}",
                    job_id=f"semantic_{variant}_{contract.tags_group}",
                    ae_bin=args.ae_bin, aerender_bin=args.aerender_bin,
                    workdir=Path(args.workdir), timeout_s=args.timeout,
                )
                final_mp4.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_mp4, final_mp4)
                rendered += 1

            file_ids = (str(existing.get("file_id") or ""), str(existing.get("file_id_public") or ""))
            if variant == "bot" and not args.no_telegram:
                file_ids = _capture_file_ids(final_mp4, "")
                registered += 1
            bp.previews_upsert(store, _entry(contract, clips, existing=existing, file_ids=file_ids))
            bp.save_previews_store(store_path, store)
            log.info("preview_ready variant=%s bucket=%s clips=%d output=%s", variant, bid, len(clips), final_mp4)

    log.info("preview_wave_done rendered=%d registered=%d skipped=%d", rendered, registered, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
