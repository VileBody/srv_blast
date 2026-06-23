#!/usr/bin/env python3
"""Build footage BUCKET PREVIEWS (precision flow, phase 4).

For each (theme, tags_group) bucket from the catalog:
  1. pick ~5 representative clips (deterministic, prod picker scoring),
     keeping only clips that actually exist in S3 (as the prod picker does);
  2. build a short 1080x1920 example montage (~1.5s/clip) via the render node
     (inline AE example-montage flow — does NOT touch the main render template);
  3. write a short RU description (label + tags, no LLM);
  4. register the mp4: upload to S3 (done by the node) + capture a Telegram
     file_id by sending the video to the backlog chat with the bot(s);
  5. persist into data/footage_bucket_previews.json keyed by bucket_id.

Idempotent: buckets that already have a usable preview are skipped unless
--force / --only is given. Deterministic: a fixed --seed reproduces the same
clips. Thin buckets (fewer than --min-clips matchable clips) are logged and
marked status="thin" (the "nothing to show" metric that motivates growing the
base) instead of producing a misleading reel.

Runs on the Windows render node (AE there). The tag source-of-truth is the
Postgres footage_tags snapshot — point FOOTAGE_STYLE_METADATA_DB_PATHS_JSON at
data/footage_tags_snapshot.json (same env the prod picker reads); locally it
falls back to the legacy video_database json files.

NOTE: the base is expanding fast, so this is a per-bucket / small-batch tool by
design. A full 59-bucket sweep requires the explicit --all flag.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from mlcore import footage_picker as fp  # noqa: E402
from mlcore.footage_bucket_catalog import Bucket, get_bucket_catalog  # noqa: E402
from mlcore import footage_bucket_previews as bp  # noqa: E402

log = logging.getLogger("build_bucket_previews")


# --------------------------------------------------------------------------- #
# Inventory + metadata (reuses the production loaders)
# --------------------------------------------------------------------------- #
def _resolve_inventory_path() -> Path:
    env = (os.environ.get("FOOTAGE_INVENTORY_JSON") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (ROOT / "data" / "footage_inventory.json").resolve()


def _ensure_inventory(inv_path: Path) -> Path:
    """Load the inventory; build it from the 1:1 static assets index if missing."""
    if inv_path.exists():
        return inv_path
    log.info("inventory missing -> building from static_assets_index_1to1.json: %s", inv_path)
    from footage_config import build_inventory_and_bundle

    static_index = (ROOT / "data" / "static_assets_index_1to1.json").resolve()
    footage_dir = Path(os.environ.get("FOOTAGE_DIR", str(ROOT / "footage"))).resolve()
    bundle_out = (ROOT / "pins" / "descriptions_bundle.json").resolve()
    build_inventory_and_bundle(
        repo_root=ROOT,
        footage_dir=footage_dir,
        static_assets_index_path=static_index,
        inventory_out_path=inv_path,
        bundle_out_path=bundle_out,
    )
    return inv_path


def _load_inventory_raw(inv_path: Path) -> Dict[str, Any]:
    obj = json.loads(inv_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("assets"), list):
        raise RuntimeError(f"invalid inventory (need assets[]): {inv_path}")
    return obj


def _build_mapped_assets(inv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Inventory assets enriched with footage_tags metadata (prod path)."""
    from mlcore.gemini_orchestrator import _resolve_style_metadata_db_paths

    picker_assets = fp.load_picker_assets_from_inventory(inv)
    db_paths = _resolve_style_metadata_db_paths(root=ROOT)
    rows = fp.load_footage_style_metadata_rows(db_paths=db_paths)
    index = fp.merge_footage_style_metadata_rows(rows)
    mapped, unmapped = fp.map_inventory_assets_with_style_metadata(
        assets=picker_assets, metadata_index=index
    )
    log.info(
        "metadata loaded db_files=%d rows=%d merged_ids=%d inventory=%d mapped=%d unmapped=%d",
        len(db_paths), len(rows), len(index), len(picker_assets), len(mapped), len(unmapped),
    )
    if not mapped:
        raise RuntimeError(
            "no inventory assets mapped to footage_tags — check "
            "FOOTAGE_STYLE_METADATA_DB_PATHS_JSON / snapshot and inventory clip ids"
        )
    return mapped


def _url_by_file_name(inv: Dict[str, Any]) -> Dict[str, str]:
    """file_name -> remote source url (inventory file_path, s3:// in prod)."""
    out: Dict[str, str] = {}
    for a in inv.get("assets") or []:
        fn = str(a.get("file_name") or "").strip()
        fpth = str(a.get("file_path") or "").strip()
        if fn and fpth:
            out[fn] = fpth
    return out


# --------------------------------------------------------------------------- #
# S3 existence filter (mirrors prod preflight: only render clips really in S3)
# --------------------------------------------------------------------------- #
def _parse_s3_url(url: str) -> Optional[Tuple[str, str]]:
    u = str(url or "").strip()
    if not u.startswith("s3://"):
        return None
    rest = u[len("s3://"):]
    if "/" not in rest:
        return None
    bucket, key = rest.split("/", 1)
    return bucket, key


def _filter_clips_in_s3(
    clips: List[Dict[str, Any]],
    url_by_fn: Dict[str, str],
    *,
    check_s3: bool,
) -> List[Dict[str, Any]]:
    from src.storage.s3 import S3ObjectNotFoundError, head_s3_object

    kept: List[Dict[str, Any]] = []
    for c in clips:
        fn = str(c["file_name"])
        url = url_by_fn.get(fn)
        if not url:
            log.debug("drop clip (no inventory url): %s", fn)
            continue
        parsed = _parse_s3_url(url)
        if parsed is None:
            # local/dev path: keep only when not enforcing S3 (dry/local runs)
            if check_s3:
                log.debug("drop clip (non-s3 url, check_s3): %s -> %s", fn, url)
                continue
            kept.append(c)
            continue
        if not check_s3:
            kept.append(c)
            continue
        bucket, key = parsed
        try:
            head_s3_object(bucket, key)
            kept.append(c)
        except S3ObjectNotFoundError:
            log.warning("drop clip (missing in S3): s3://%s/%s", bucket, key)
        except Exception as e:  # transient/credentials — surface, don't silently drop
            raise RuntimeError(f"S3 head failed for s3://{bucket}/{key}: {e!r}") from e
    return kept


# --------------------------------------------------------------------------- #
# Render node (inline AE example-montage; async /render + poll)
# --------------------------------------------------------------------------- #
def _montage_template_text() -> str:
    return (ROOT / "templates" / "bucket_preview" / "montage_template.jsx").read_text(encoding="utf-8")


def _render_via_node(
    *,
    node_url: str,
    job_id: str,
    render_jsx: str,
    media: List[Dict[str, str]],
    output_s3_bucket: str,
    output_s3_key: str,
    timeout_s: float,
    poll_s: float,
) -> str:
    """Dispatch the inline montage job, poll to completion, return output url."""
    payload = {
        "job_id": job_id,
        "render_jsx": render_jsx,
        "media": media,
        "entry_comp": "Bucket Preview",
        "output_relpath": "work/output.mp4",
        "output_s3_bucket": output_s3_bucket,
        "output_s3_key": output_s3_key,
    }
    base = node_url.rstrip("/")
    resp = requests.post(f"{base}/render", json=payload, timeout=120)
    resp.raise_for_status()
    render_id = str(resp.json().get("render_id") or "").strip()
    if not render_id:
        raise RuntimeError(f"node /render returned no render_id: {resp.text}")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = requests.get(f"{base}/render/{render_id}", timeout=60).json()
        status = str(st.get("status") or "").strip()
        if status == "succeeded":
            if not st.get("success"):
                raise RuntimeError(f"render reported failure: {st.get('message')}")
            return str(st.get("output_url") or "").strip()
        if status == "failed":
            raise RuntimeError(f"render failed: {st.get('message')}")
        time.sleep(poll_s)
    raise RuntimeError(f"render timeout after {timeout_s}s (render_id={render_id})")


# --------------------------------------------------------------------------- #
# Telegram file_id capture (mirrors the artist-preview model)
# --------------------------------------------------------------------------- #
def capture_telegram_file_id(*, token: str, chat_id: str, video_path: Path, caption: str) -> str:
    """Send the video to chat_id with `token`; return the resulting video file_id.

    file_id is valid only for the bot identified by `token` (Telegram rule), so
    we capture one per bot we need (internal bot -> file_id, public preview-source
    bot -> file_id_public), exactly like artist previews.
    """
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    with open(video_path, "rb") as fh:
        files = {"video": (video_path.name, fh, "video/mp4")}
        data = {"chat_id": str(chat_id), "caption": caption[:1024], "supports_streaming": "true"}
        resp = requests.post(url, data=data, files=files, timeout=300)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"telegram sendVideo not ok: {payload}")
    result = payload.get("result") or {}
    video = result.get("video") or {}
    fid = str(video.get("file_id") or "").strip()
    if not fid:
        raise RuntimeError(f"telegram sendVideo returned no video.file_id: {payload}")
    return fid


def _download_to_temp(url: str, dest_dir: Path) -> Path:
    """Fetch the rendered mp4 locally (s3:// via s3 client, else http)."""
    dest = dest_dir / "preview.mp4"
    parsed = _parse_s3_url(url)
    if parsed is not None:
        from src.storage.s3 import download_from_s3

        bucket, key = parsed
        return download_from_s3(bucket, key, dest)
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return dest


# --------------------------------------------------------------------------- #
# Per-bucket pipeline
# --------------------------------------------------------------------------- #
def _output_s3_target() -> Tuple[str, str]:
    bucket = (os.environ.get("FOOTAGE_PREVIEW_S3_BUCKET")
              or os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
    if not bucket:
        raise RuntimeError("set FOOTAGE_PREVIEW_S3_BUCKET or S3_BUCKET_ASSET_STORAGE")
    prefix = (os.environ.get("FOOTAGE_PREVIEW_S3_PREFIX") or "footage_bucket_previews").strip().strip("/")
    return bucket, prefix


def _capture_file_ids(video_path: Path, caption: str) -> Tuple[str, str]:
    """(file_id internal, file_id_public). Each is captured only if its token +
    chat is configured; otherwise returned empty (graceful degradation)."""
    backlog_chat = (os.environ.get("FOOTAGE_PREVIEW_BACKLOG_CHAT_ID")
                    or os.environ.get("MANAGER_CHAT_ID") or "").strip()
    file_id = ""
    file_id_public = ""

    internal_token = (os.environ.get("TG_BOT_TOKEN") or "").strip()
    if internal_token and backlog_chat:
        file_id = capture_telegram_file_id(
            token=internal_token, chat_id=backlog_chat, video_path=video_path, caption=caption
        )
    else:
        log.warning("internal file_id skipped (TG_BOT_TOKEN / backlog chat not set)")

    public_token = (os.environ.get("TG_PREVIEW_SOURCE_BOT_TOKEN") or "").strip()
    public_chat = (os.environ.get("TG_PREVIEW_SOURCE_CHAT_ID") or backlog_chat).strip()
    if public_token and public_chat:
        file_id_public = capture_telegram_file_id(
            token=public_token, chat_id=public_chat, video_path=video_path, caption=caption
        )
    else:
        log.info("public file_id skipped (TG_PREVIEW_SOURCE_BOT_TOKEN / chat not set)")

    return file_id, file_id_public


def build_one_bucket(
    bucket: Bucket,
    *,
    mapped_assets: List[Dict[str, Any]],
    url_by_fn: Dict[str, str],
    args: argparse.Namespace,
) -> bp.PreviewEntry:
    seed = f"{args.seed}:{bucket.bucket_id}"
    candidates = bp.select_bucket_clips(
        bucket, mapped_assets, seed=seed, top_n=max(args.top_n * 3, args.top_n)
    )
    in_s3 = _filter_clips_in_s3(candidates, url_by_fn, check_s3=not args.no_s3_check)
    clips = in_s3[: args.top_n]

    description = bp.build_bucket_description(bucket)
    entry = bp.PreviewEntry(
        bucket_id=bucket.bucket_id,
        label=bucket.label,
        description=description,
        clip_ids=bp.clip_ids_of(clips),
        built_at=bp.now_iso(),
    )

    if len(clips) < args.min_clips:
        entry.status = "thin"
        log.warning(
            "THIN bucket %s: only %d clips in S3 (need >=%d) — marking 'thin', skipping render",
            bucket.bucket_id, len(clips), args.min_clips,
        )
        return entry

    if args.dry_run:
        entry.status = "ok"
        log.info("[dry-run] %s -> %d clips: %s",
                 bucket.bucket_id, len(clips), [c["file_name"] for c in clips])
        return entry

    # 2. AE example montage on the node
    spec = bp.build_montage_spec(bucket, clips)
    render_jsx = bp.render_montage_jsx(spec, _montage_template_text())
    media = bp.montage_media_payload(clips, url_by_file_name=url_by_fn)

    out_bucket, out_prefix = _output_s3_target()
    out_key = f"{out_prefix}/{bucket.bucket_id.replace(':', '__')}.mp4"
    job_id = f"bucketprev_{bucket.bucket_id.replace(':', '__')}"

    output_url = _render_via_node(
        node_url=args.node_url,
        job_id=job_id,
        render_jsx=render_jsx,
        media=media,
        output_s3_bucket=out_bucket,
        output_s3_key=out_key,
        timeout_s=args.render_timeout_s,
        poll_s=args.poll_s,
    )
    entry.s3_url = f"s3://{out_bucket}/{out_key}"
    log.info("rendered %s -> %s", bucket.bucket_id, entry.s3_url)

    # 5. register Telegram file_id(s) from the rendered mp4
    if not args.no_telegram:
        with tempfile.TemporaryDirectory(prefix="bucketprev_") as td:
            local = _download_to_temp(output_url or entry.s3_url, Path(td))
            caption = f"{bucket.label} — {description}\nbucket: {bucket.bucket_id}"
            entry.file_id, entry.file_id_public = _capture_file_ids(local, caption)
    entry.status = "ok"
    return entry


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _select_buckets(catalog: List[Bucket], args: argparse.Namespace) -> List[Bucket]:
    if args.only:
        wanted = set(args.only)
        chosen = [b for b in catalog if b.bucket_id in wanted]
        missing = wanted - {b.bucket_id for b in chosen}
        if missing:
            raise SystemExit(f"unknown bucket_id(s): {sorted(missing)}")
        return chosen
    if args.all:
        out = list(catalog)
    elif args.limit:
        out = catalog[: args.limit]
    else:
        raise SystemExit(
            "refusing a full sweep by default (the base is still growing).\n"
            "Use --only <bucket_id ...>, --limit N, or explicit --all."
        )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build footage bucket previews (precision flow, phase 4)")
    ap.add_argument("--only", nargs="*", default=None, help="specific bucket_id(s) to (re)build")
    ap.add_argument("--limit", type=int, default=0, help="build the first N buckets (catalog order)")
    ap.add_argument("--all", action="store_true", help="build ALL buckets (explicit full sweep)")
    ap.add_argument("--force", action="store_true", help="rebuild even if a usable preview exists")
    ap.add_argument("--seed", default="bucket_preview_v1", help="deterministic clip-selection seed")
    ap.add_argument("--top-n", type=int, default=bp.DEFAULT_TOP_N, help="clips per preview")
    ap.add_argument("--min-clips", type=int, default=bp.DEFAULT_MIN_CLIPS,
                    help="below this a bucket is marked 'thin' and skipped")
    ap.add_argument("--node-url", default=os.environ.get("AE_NODE_URL", "http://127.0.0.1:8000"),
                    help="render node base url")
    ap.add_argument("--render-timeout-s", type=float, default=1800.0)
    ap.add_argument("--poll-s", type=float, default=5.0)
    ap.add_argument("--previews-path", default=bp.DEFAULT_PREVIEWS_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="clip-selection only: no render / no Telegram / no S3 upload")
    ap.add_argument("--no-telegram", action="store_true", help="render + S3, but skip file_id capture")
    ap.add_argument("--no-s3-check", action="store_true",
                    help="skip the S3 existence filter (dev/local inventories)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    catalog = get_bucket_catalog()
    log.info("catalog: %d buckets", len(catalog))
    targets = _select_buckets(catalog, args)
    log.info("targets: %d buckets", len(targets))

    inv_path = _ensure_inventory(_resolve_inventory_path())
    inv = _load_inventory_raw(inv_path)
    mapped_assets = _build_mapped_assets(inv)
    url_by_fn = _url_by_file_name(inv)

    store_path = (ROOT / args.previews_path) if not os.path.isabs(args.previews_path) else Path(args.previews_path)
    store = bp.load_previews_store(store_path)

    built = skipped = thin = failed = 0
    for bucket in targets:
        if not args.force and not args.only and bp.has_preview(store, bucket.bucket_id):
            log.info("skip %s (preview exists)", bucket.bucket_id)
            skipped += 1
            continue
        try:
            entry = build_one_bucket(bucket, mapped_assets=mapped_assets, url_by_fn=url_by_fn, args=args)
        except Exception as e:
            failed += 1
            log.exception("FAILED bucket %s: %r", bucket.bucket_id, e)
            continue
        if not args.dry_run:
            bp.previews_upsert(store, entry)
            bp.save_previews_store(store_path, store)
        if entry.status == "thin":
            thin += 1
        else:
            built += 1

    log.info("done: built=%d thin=%d skipped=%d failed=%d store=%s",
             built, thin, skipped, failed, store_path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
