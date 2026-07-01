#!/usr/bin/env python3
"""Reconcile the footage "sources of truth" and show WHERE clips are lost between
what the Asset UI counts and what the picker can actually pick.

The two axes diverge by design (see FOOTAGE_PIPELINE.md):
  - Asset UI count = a live S3 LISTING under the top-level prefix (e.g.
    `pinterest_collection`). Needs no ffprobe, no tags. This is the big number.
  - Picker pool = static_assets_index_1to1.json (ffprobe-gated) -> inventory
    (valid dims, unique file_name) -> mapped to the footage_tags snapshot by the
    8+ digit clip_id. A clip must survive ALL of these to be pickable.

This tool runs the funnel and reports the drop at each stage with sample keys, so
"2465 tagged in the UI but only N in the pool" becomes an explained, actionable
number instead of a mystery.

FUNNEL (video pool):
  S3 objects (--s3)  ->  static index  ->  valid dims / unique  ->  has clip_id
  ->  tagged (in snapshot)  ->  pickable

It also flags the PREFIX-COLLISION footgun: build_static_assets_index.py (manual)
scans S3_ASSET_PREFIX (narrow, e.g. .../pins2_1to1_...) while activate_footage_base
scans its top-level split (broad, `pinterest_collection`) — both write the SAME
static_assets_index_1to1.json, so whichever ran last decides the pool size.

Usage:
  # offline: reconcile the committed index against a tags snapshot
  python scripts/footage_pool_reconcile.py \
      --index data/static_assets_index_1to1.json \
      --metadata data/footage_tags_snapshot.json [out.json]

  # on the worker (live S3 top of funnel + Postgres tags):
  S3_BUCKET_ASSET_STORAGE=... S3_ASSET_PREFIX=... CREDITS_DB_URL=... \
  python scripts/footage_pool_reconcile.py --s3 [out.json]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlcore.footage_picker import _extract_clip_id  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = "data/footage_pool_reconcile.json"
_DEFAULT_INDEX = _ROOT / "data" / "static_assets_index_1to1.json"
_SAMPLE = 15


# --------------------------------------------------------------------------- #
# Pure funnel (unit tested)
# --------------------------------------------------------------------------- #
def _valid_dims(a: Dict[str, Any]) -> bool:
    try:
        return int(a.get("src_w") or 0) > 0 and int(a.get("src_h") or 0) > 0 and float(a.get("duration_sec") or 0.0) > 0.0
    except Exception:
        return False


def reconcile(
    *,
    index_assets: List[Dict[str, Any]],
    tagged_clip_ids: Set[str],
    s3_file_names: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Run the pool funnel. Returns stage counts + sample dropped file_names.

    tagged_clip_ids: clip_ids present in the footage_tags snapshot (source=video).
    s3_file_names: optional set of file_names seen in the live S3 listing (the
    Asset UI axis). When given, the report shows what S3 has that the index lost.
    """
    stages: List[Dict[str, Any]] = []

    def _stage(name: str, kept: List[str], dropped: List[str]) -> None:
        stages.append({
            "stage": name,
            "kept": len(kept),
            "dropped": len(dropped),
            "dropped_sample": dropped[:_SAMPLE],
        })

    # unique file_names in the index (dedup like the inventory builder)
    seen: Set[str] = set()
    index_names: List[str] = []
    dup_dropped: List[str] = []
    for a in index_assets:
        fn = str(a.get("file_name") or "").strip()
        if not fn:
            continue
        if fn in seen:
            dup_dropped.append(fn)
            continue
        seen.add(fn)
        index_names.append(fn)

    by_name = {str(a.get("file_name") or "").strip(): a for a in index_assets}

    # S3 -> index (only when S3 listing provided)
    s3_only: List[str] = []
    if s3_file_names is not None:
        s3_only = sorted(n for n in s3_file_names if n not in seen)
        _stage("s3_listing -> static_index", sorted(seen), s3_only)

    # index -> valid dims / unique
    valid = [n for n in index_names if _valid_dims(by_name.get(n) or {})]
    invalid = [n for n in index_names if n not in set(valid)] + dup_dropped
    _stage("static_index -> valid_dims+unique", valid, invalid)

    # valid -> has 8+ digit clip_id
    with_id: List[Tuple[str, str]] = []
    no_id: List[str] = []
    for n in valid:
        cid = _extract_clip_id(n)
        if cid:
            with_id.append((n, cid))
        else:
            no_id.append(n)
    _stage("valid -> has_clip_id", [n for n, _ in with_id], no_id)

    # has clip_id -> tagged (in snapshot)
    pickable: List[str] = []
    untagged: List[str] = []
    for n, cid in with_id:
        if cid in tagged_clip_ids:
            pickable.append(n)
        else:
            untagged.append(n)
    _stage("has_clip_id -> tagged(pickable)", pickable, untagged)

    return {
        "totals": {
            "s3_objects": (len(s3_file_names) if s3_file_names is not None else None),
            "index_rows": len(index_assets),
            "index_unique": len(index_names),
            "tagged_clip_ids_in_snapshot": len(tagged_clip_ids),
            "pickable": len(pickable),
        },
        "stages": stages,
    }


def prefix_collision_warning(index_source_root: str) -> Optional[str]:
    """Warn when the index was built from a DIFFERENT prefix than the one the
    activation / Asset UI browse (the narrow-vs-broad footgun)."""
    s3_prefix = (os.environ.get("S3_ASSET_PREFIX") or "").strip().strip("/")
    if not s3_prefix:
        return None
    broad = s3_prefix.split("/", 1)[0]          # activation / asset-ui prefix
    root = str(index_source_root or "").strip().strip("/")
    # index source_root is like "pinterest_collection/pins2_1to1_20260323" or a bucket url tail
    root_tail = root.split("://", 1)[-1]
    if root_tail == broad or root_tail.endswith("/" + broad):
        return None
    if s3_prefix in root_tail:
        return (
            f"index source_root ({root!r}) == the NARROW S3_ASSET_PREFIX ({s3_prefix!r}), "
            f"but activation/Asset-UI browse the BROAD top-level ({broad!r}). "
            "The pool is the narrow subset; the UI count is the broad listing. "
            "Rebuild the index via 'Активировать базу' (broad) or align the prefixes."
        )
    return (
        f"index source_root ({root!r}) does not match the browse prefix ({broad!r}); "
        "verify the index was built from the same S3 location the Asset UI lists."
    )


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def load_index(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return [a for a in obj if isinstance(a, dict)], ""
    assets = obj.get("assets") or obj.get("items") or []
    return [a for a in assets if isinstance(a, dict)], str(obj.get("source_root") or "")


def load_tagged_clip_ids(metadata_paths: List[Path]) -> Set[str]:
    """clip_ids present in the snapshot(s) the picker reads."""
    ids: Set[str] = set()
    for p in metadata_paths:
        obj = json.loads(Path(p).read_text(encoding="utf-8"))
        rows = obj if isinstance(obj, list) else (obj.get("items") or obj.get("assets") or obj.get("videos") or [])
        for r in rows:
            if not isinstance(r, dict):
                continue
            cid = _extract_clip_id(r.get("video_key")) or _extract_clip_id(r.get("video_path")) or _extract_clip_id(r.get("file_name")) or _extract_clip_id(r.get("clip_id"))
            if cid:
                ids.add(cid)
    return ids


def load_tagged_clip_ids_from_pg(db_url: str) -> Set[str]:
    import asyncio

    import asyncpg  # type: ignore

    from mlcore.footage_tags_db import fetch_all_records

    async def _go():
        conn = await asyncpg.connect(dsn=db_url)
        try:
            return await fetch_all_records(conn)
        finally:
            await conn.close()

    recs = asyncio.run(_go())
    ids: Set[str] = set()
    for r in recs:
        cid = str((r or {}).get("clip_id") or "").strip()
        if cid:
            ids.add(cid)
    return ids


def list_s3_file_names(*, bucket: str, prefix: str) -> Set[str]:
    from src.storage.s3 import list_s3_objects

    exts = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
    out: Set[str] = set()
    token = None
    pref = f"{prefix.strip().strip('/')}/" if prefix else ""
    while True:
        page = list_s3_objects(bucket, prefix=pref, continuation_token=token, max_keys=1000, delimiter="")
        for obj in page.get("objects") or []:
            k = str(obj.get("key") or "").strip().lstrip("/")
            if k and not k.endswith("/") and Path(k).suffix.lower() in exts:
                out.add(Path(k).name)
        token = page.get("next_continuation_token")
        if not page.get("is_truncated") or not token:
            break
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _pop(args: List[str], flag: str) -> Optional[str]:
    if flag in args:
        i = args.index(flag)
        v = args[i + 1]
        del args[i : i + 2]
        return v
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
    use_s3 = False
    if "--s3" in args:
        use_s3 = True
        args.remove("--s3")
    index_arg = _pop(args, "--index")
    meta = _pop_multi(args, "--metadata")
    out_path = Path(args[0] if args else _DEFAULT_OUT)

    index_path = Path(index_arg) if index_arg else _DEFAULT_INDEX
    index_assets, source_root = load_index(index_path)

    # tags: --metadata files, else env snapshot(s), else Postgres, else empty
    if meta:
        tagged = load_tagged_clip_ids([Path(p) for p in meta])
        tags_src = f"{len(meta)} snapshot file(s)"
    else:
        raw = (os.environ.get("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON") or "").strip()
        env_paths = []
        if raw:
            try:
                env_paths = [Path(str(p)) for p in json.loads(raw)]
            except Exception:
                env_paths = []
        if env_paths and all(p.exists() for p in env_paths):
            tagged = load_tagged_clip_ids(env_paths)
            tags_src = "env FOOTAGE_STYLE_METADATA_DB_PATHS_JSON"
        elif (os.environ.get("CREDITS_DB_URL") or "").strip():
            tagged = load_tagged_clip_ids_from_pg(os.environ["CREDITS_DB_URL"].strip())
            tags_src = "postgres footage_tags"
        else:
            tagged = set()
            tags_src = "NONE (no --metadata / env / CREDITS_DB_URL)"

    s3_names: Optional[Set[str]] = None
    s3_src = "not scanned"
    if use_s3:
        bucket = (os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
        s3_prefix = (os.environ.get("S3_ASSET_PREFIX") or "").strip().strip("/")
        browse_prefix = (os.environ.get("ASSET_UI_SOURCE_PREFIX") or "").strip().strip("/") or (s3_prefix.split("/", 1)[0] if s3_prefix else "pinterest_collection")
        s3_names = list_s3_file_names(bucket=bucket, prefix=browse_prefix)
        s3_src = f"s3://{bucket}/{browse_prefix}"

    report = reconcile(index_assets=index_assets, tagged_clip_ids=tagged, s3_file_names=s3_names)
    report["sources"] = {
        "index": str(index_path),
        "index_source_root": source_root,
        "tags": tags_src,
        "s3": s3_src,
    }
    warn = prefix_collision_warning(source_root)
    report["prefix_collision_warning"] = warn

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    t = report["totals"]
    print(f"index={report['sources']['index']} (source_root={source_root!r})")
    print(f"tags source: {tags_src} | s3: {s3_src}")
    print(f"\nFUNNEL:")
    if t["s3_objects"] is not None:
        print(f"  s3_objects (Asset UI axis) : {t['s3_objects']}")
    print(f"  index_rows / unique        : {t['index_rows']} / {t['index_unique']}")
    print(f"  tagged clip_ids (snapshot) : {t['tagged_clip_ids_in_snapshot']}")
    print(f"  PICKABLE pool              : {t['pickable']}")
    print(f"\nstage drops:")
    for s in report["stages"]:
        print(f"  {s['stage']:<38} kept={s['kept']:>5} dropped={s['dropped']:>5}")
        if s["dropped_sample"]:
            print(f"      e.g. {', '.join(s['dropped_sample'][:8])}")
    if warn:
        print(f"\n[PREFIX WARNING] {warn}")
    print(f"\nfull report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
