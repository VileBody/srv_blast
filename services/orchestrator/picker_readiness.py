#!/usr/bin/env python3
"""Fail-closed picker readiness — a deterministic, read-only dry-run over the
durable Postgres registry + tags snapshot.

WHY: `docker compose up -d worker-build` attaches a new worker to the user-facing
`build` queue the instant it starts. Every past footage outage (empty registry
replacement, node-local JSON caches drifting from Postgres, new inventory paired
with legacy packaged metadata) became visible only AFTER the new code was already
consuming user jobs. This module is the gate that runs on a CANDIDATE container
BEFORE the queues are attached, so a broken pool fails the deploy instead of the
user's render.

CONTRACT — this check MUST NOT:
  * rebuild the S3 index or call S3 at all,
  * prune anything,
  * mutate footage_assets / footage_tags (it issues SELECTs only — not even
    init_schema, whose CREATE TABLE IF NOT EXISTS is still DDL),
  * activate a base,
  * call an LLM,
  * render real video.

It reads Postgres, materializes throwaway inventory artifacts in a TEMP dir
(never the production cache paths), and replays the exact production mapping +
picker code paths against them.

Exit codes: 0 = PASS (safe to attach queues), 1 = FAIL (keep old containers).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import platform
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

POOL_VIDEO = "video"
POOL_PHOTO = "photo"

# A pool this far below its recorded baseline is treated as a regression, not a
# legitimate edit. Mirrors the activation-side registry shrink guard so deploy and
# activation fail on the same shape of damage.
DEFAULT_MIN_RETAIN_RATIO = 0.80
DEFAULT_SHRINK_GUARD_MIN_BASELINE = 100

# Absolute floors. A healthy video pool is ~2.4k clips; photo is a younger pool.
DEFAULT_MIN_VIDEO_POOL_PICKABLE = 500
DEFAULT_MIN_PHOTO_POOL_PICKABLE = 50
DEFAULT_MIN_BUCKET_CANDIDATES = 5

# Reference buckets are the canary: broad, always-populated contracts. If these
# return nothing, the pool/mapping is broken regardless of what the counts say.
DEFAULT_VIDEO_REFERENCE_BUCKETS: Tuple[str, ...] = ("visual:urban_solitude_dark",)
# One STRICT photo bucket — it carries PHOTO_REQUIRE_GROUPS anchors on top of the
# footage rule, so it also proves the photo-only gate still admits real stills.
DEFAULT_PHOTO_REFERENCE_BUCKETS: Tuple[str, ...] = ("visual:urban_solitude_dark",)

# A short synthetic timeline (seconds). Deliberately tiny: this proves the
# interval picker can cover cuts from the pool, not that a real track works.
DEFAULT_TIMELINE: Tuple[Tuple[float, float], ...] = ((0.0, 2.0), (2.0, 4.0), (4.0, 6.0))


@dataclass
class PoolReadiness:
    """Diagnostics for one pool. `ok` is the gate; the rest is what you page on."""

    pool: str
    ok: bool = False
    registry_rows: int = 0
    snapshot_rows: int = 0
    mapped_assets: int = 0
    unmapped_assets: int = 0
    pool_pickable: int = 0
    buckets: Dict[str, int] = field(default_factory=dict)
    timeline_covered: Optional[bool] = None
    failures: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fail(report: PoolReadiness, message: str) -> PoolReadiness:
    report.failures.append(message)
    report.ok = False
    return report


def _shrink_failure(
    *,
    label: str,
    current: int,
    baseline: int,
    min_retain_ratio: float,
    guard_min_baseline: int,
) -> Optional[str]:
    """Same shape as mlcore.footage_assets_db.validate_registry_replacement, but
    applied to a deploy candidate rather than a scan: baseline is what the pool
    looked like when it was known good."""
    base = max(0, int(baseline))
    cur = max(0, int(current))
    if base < max(0, int(guard_min_baseline)) or base <= 0:
        return None
    required = int(math.ceil(base * float(min_retain_ratio)))
    if cur >= required:
        return None
    shrink_ratio = 1.0 - (cur / base) if base else 1.0
    return (
        f"{label}_shrink_guard: {label}={cur} baseline={base} "
        f"min_required={required} min_retain_ratio={float(min_retain_ratio):.3f} "
        f"shrink_ratio={shrink_ratio:.3f}"
    )


@contextlib.contextmanager
def _no_s3_preflight() -> Iterator[None]:
    """Force the inventory build to stay offline.

    In MODE=prod the inventory builder defaults to a STRICT S3 preflight (one HEAD
    per asset). Readiness is a cheap dry-run over Postgres and must never depend on
    S3 reachability — object existence is the index build's job, not this gate's.
    """
    prev = os.environ.get("FOOTAGE_S3_PREFLIGHT_MODE")
    os.environ["FOOTAGE_S3_PREFLIGHT_MODE"] = "off"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("FOOTAGE_S3_PREFLIGHT_MODE", None)
        else:
            os.environ["FOOTAGE_S3_PREFLIGHT_MODE"] = prev


def _materialize_inventory(
    *,
    records: List[Dict[str, Any]],
    media_type: str,
    workdir: Path,
) -> List[Dict[str, Any]]:
    """Registry rows -> picker assets, entirely inside `workdir`.

    Mirrors the production hydration (tasks.py) but writes throwaway files, so a
    readiness run can never leave a half-built cache behind on the node.
    """
    from mlcore.footage_assets_db import index_row_from_record
    from footage_config import build_inventory_and_bundle
    from mlcore.footage_picker import load_picker_assets_from_inventory

    index_obj = {
        "version": f"{media_type}-registry-v1",
        "media_type": media_type,
        "assets_count": len(records),
        "assets": [index_row_from_record(r) for r in records if isinstance(r, dict)],
    }
    index_path = workdir / f"{media_type}_index.json"
    inv_path = workdir / f"{media_type}_inventory.json"
    bundle_path = workdir / f"{media_type}_bundle.json"
    index_path.write_text(json.dumps(index_obj, ensure_ascii=False), encoding="utf-8")

    with _no_s3_preflight():
        build_inventory_and_bundle(
            repo_root=_ROOT,
            footage_dir=Path(os.environ.get("FOOTAGE_DIR", str(_ROOT / "footage"))),
            static_assets_index_path=index_path,
            inventory_out_path=inv_path,
            bundle_out_path=bundle_path,
            media_type=media_type,
        )
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    return load_picker_assets_from_inventory(inv)


def _bucket_candidates(
    *,
    bucket_id: str,
    mapped_assets: List[Dict[str, Any]],
    media_type: str,
) -> int:
    """Candidates a visual contract yields, via the production picker gate."""
    from mlcore import footage_picker as fp
    from mlcore.footage_bucket_previews import _raw_pick_from_bucket
    from mlcore.footage_visual_catalog import load_visual_catalog

    contract = next(
        (c for c in load_visual_catalog() if c.bucket_id == bucket_id), None
    )
    if contract is None:
        raise RuntimeError(f"unknown reference bucket: {bucket_id}")
    raw_pick = _raw_pick_from_bucket(contract)
    pool = fp._build_raw_pool(raw_pick, mapped_assets, media_type=media_type)
    return len(pool)


def _timeline_covered(
    *,
    bucket_id: str,
    mapped_assets: List[Dict[str, Any]],
    media_type: str,
    timeline: Sequence[Tuple[float, float]],
) -> bool:
    """Can the pool cover a short synthetic cut list?

    Photos are stills (no duration), so this is a video-only invariant: it is the
    check that would have caught "No footage asset can cover interval" before the
    worker started serving jobs.
    """
    from mlcore import footage_picker as fp
    from mlcore.footage_bucket_previews import _raw_pick_from_bucket
    from mlcore.footage_visual_catalog import load_visual_catalog

    contract = next((c for c in load_visual_catalog() if c.bucket_id == bucket_id), None)
    if contract is None:
        raise RuntimeError(f"unknown reference bucket: {bucket_id}")
    pool = fp._build_raw_pool(
        _raw_pick_from_bucket(contract), mapped_assets, media_type=media_type
    )
    if not pool:
        return False
    for (start, end) in timeline:
        need = max(0.0, float(end) - float(start))
        if not any(float(it.get("duration_sec") or 0.0) >= need for it in pool):
            return False
    return True


def evaluate_pool(
    *,
    pool: str,
    records: List[Dict[str, Any]],
    snapshot_rows: List[Dict[str, Any]],
    pickable_count: int,
    reference_buckets: Sequence[str],
    min_pool_pickable: int,
    min_bucket_candidates: int = DEFAULT_MIN_BUCKET_CANDIDATES,
    baseline: Optional[Dict[str, int]] = None,
    timeline: Optional[Sequence[Tuple[float, float]]] = DEFAULT_TIMELINE,
    check_timeline: bool = True,
    min_retain_ratio: float = DEFAULT_MIN_RETAIN_RATIO,
    guard_min_baseline: int = DEFAULT_SHRINK_GUARD_MIN_BASELINE,
) -> PoolReadiness:
    """Pure readiness verdict for ONE pool over already-fetched rows.

    Pure on purpose: no Postgres, no network, no ambient env. The deploy gate and
    the tests exercise exactly this function, so a test that says "mapped_assets=0
    fails" is testing the code that actually runs on the node.
    """
    report = PoolReadiness(pool=str(pool))
    report.registry_rows = len(records or [])
    report.snapshot_rows = len(snapshot_rows or [])
    report.pool_pickable = max(0, int(pickable_count))

    if report.registry_rows <= 0:
        return _fail(report, f"{pool}_registry_empty: registry_rows=0")
    if report.snapshot_rows <= 0:
        return _fail(report, f"{pool}_snapshot_empty: snapshot_rows=0")

    with tempfile.TemporaryDirectory(prefix=f"readiness_{pool}_") as tmp:
        workdir = Path(tmp)
        try:
            picker_assets = _materialize_inventory(
                records=list(records), media_type=pool, workdir=workdir
            )
        except Exception as exc:
            return _fail(report, f"{pool}_inventory_build_failed: {exc!r}")

        from mlcore.footage_picker import (
            load_footage_style_metadata_rows,
            map_inventory_assets_with_style_metadata,
            merge_footage_style_metadata_rows,
        )

        # Production reads the snapshot back from disk, which is where clip_ids get
        # extracted from video_key and tags normalized. Round-trip through a temp
        # file so readiness maps exactly the way the build job does — a shortcut
        # here would test a mapping that never runs in prod.
        snap_path = workdir / f"{pool}_tags_snapshot.json"
        snap_path.write_text(
            json.dumps(list(snapshot_rows), ensure_ascii=False), encoding="utf-8"
        )
        try:
            metadata_rows = load_footage_style_metadata_rows(db_paths=[snap_path])
        except Exception as exc:
            return _fail(report, f"{pool}_snapshot_load_failed: {exc!r}")
        metadata_index = merge_footage_style_metadata_rows(metadata_rows)
        mapped, unmapped = map_inventory_assets_with_style_metadata(
            assets=picker_assets, metadata_index=metadata_index
        )
        report.mapped_assets = len(mapped)
        report.unmapped_assets = len(unmapped)

        if not mapped:
            return _fail(
                report,
                f"{pool}_mapped_assets_zero: inventory_assets={len(picker_assets)} "
                f"snapshot_rows={report.snapshot_rows} metadata_rows={len(metadata_index)} — "
                "inventory and tags snapshot do not resolve to the same clip ids",
            )

        if report.pool_pickable < int(min_pool_pickable):
            _fail(
                report,
                f"{pool}_pool_pickable_below_floor: pool_pickable={report.pool_pickable} "
                f"min={int(min_pool_pickable)}",
            )

        base = dict(baseline or {})
        for label, current in (
            ("registry_rows", report.registry_rows),
            ("snapshot_rows", report.snapshot_rows),
            ("pool_pickable", report.pool_pickable),
        ):
            if label not in base:
                continue
            msg = _shrink_failure(
                label=f"{pool}_{label}",
                current=current,
                baseline=int(base[label]),
                min_retain_ratio=min_retain_ratio,
                guard_min_baseline=guard_min_baseline,
            )
            if msg:
                _fail(report, msg)

        for bucket_id in reference_buckets:
            try:
                n = _bucket_candidates(
                    bucket_id=bucket_id, mapped_assets=mapped, media_type=pool
                )
            except Exception as exc:
                _fail(report, f"{pool}_bucket_eval_failed bucket={bucket_id}: {exc!r}")
                continue
            report.buckets[bucket_id] = n
            if n < int(min_bucket_candidates):
                _fail(
                    report,
                    f"{pool}_bucket_starved bucket={bucket_id} candidates={n} "
                    f"min={int(min_bucket_candidates)}",
                )

        if check_timeline and timeline and reference_buckets:
            try:
                covered = _timeline_covered(
                    bucket_id=reference_buckets[0],
                    mapped_assets=mapped,
                    media_type=pool,
                    timeline=timeline,
                )
            except Exception as exc:
                covered = False
                _fail(report, f"{pool}_timeline_eval_failed: {exc!r}")
            report.timeline_covered = covered
            if not covered:
                _fail(
                    report,
                    f"{pool}_timeline_uncovered: reference bucket cannot cover "
                    f"{len(list(timeline))} intervals",
                )

    report.ok = not report.failures
    return report


async def load_pool_from_postgres(dsn: str, *, source: str) -> Dict[str, Any]:
    """SELECT-only read of one pool. No init_schema, no writes."""
    import asyncpg  # type: ignore

    from mlcore.footage_assets_db import count_pickable, fetch_all_assets
    from mlcore.footage_tags_db import build_snapshot, filter_snapshot_to_pool

    conn = await asyncpg.connect(dsn=dsn)
    try:
        records = await fetch_all_assets(conn, source=source)
        snapshot_rows = await build_snapshot(conn, source=source)
        pool_ids = {str(r.get("clip_id") or "") for r in records}
        pickable = await count_pickable(conn, source=source)
        return {
            "records": records,
            "snapshot_rows": filter_snapshot_to_pool(snapshot_rows, pool_ids),
            "pickable": int(pickable),
        }
    finally:
        await conn.close()


def check_pool(
    *,
    dsn: str,
    pool: str,
    baseline: Optional[Dict[str, int]] = None,
    **kwargs: Any,
) -> PoolReadiness:
    """Read one pool from Postgres and evaluate it."""
    data = asyncio.run(load_pool_from_postgres(dsn, source=pool))
    return evaluate_pool(
        pool=pool,
        records=data["records"],
        snapshot_rows=data["snapshot_rows"],
        pickable_count=data["pickable"],
        baseline=baseline,
        **kwargs,
    )


def _load_baseline(path: Optional[str]) -> Dict[str, Dict[str, int]]:
    raw = (path or os.environ.get("READINESS_BASELINE_JSON") or "").strip()
    if not raw:
        return {}
    p = Path(raw)
    if not p.exists():
        raise RuntimeError(f"readiness baseline file not found: {p}")
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"readiness baseline must be a JSON object: {p}")
    return {
        str(k): {str(kk): int(vv) for kk, vv in (v or {}).items()}
        for k, v in obj.items()
        if isinstance(v, dict)
    }


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return int(default)
    return int(raw)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fail-closed picker readiness dry-run.")
    ap.add_argument(
        "--pools",
        default="video,photo",
        help="Comma-separated pools to check (default: video,photo).",
    )
    ap.add_argument("--dsn", default="", help="Postgres DSN (default: CREDITS_DB_URL).")
    ap.add_argument("--baseline", default="", help="Path to a baseline counts JSON.")
    ap.add_argument(
        "--photo-required",
        action="store_true",
        help="Fail the gate when the photo pool is not ready (default: photo is "
             "reported but does not block, since the photo flow is behind a flag).",
    )
    ap.add_argument("--json-out", default="", help="Write the full report JSON here.")
    args = ap.parse_args(argv)

    dsn = (args.dsn or os.environ.get("CREDITS_DB_URL") or "").strip()
    if not dsn:
        print(
            json.dumps(
                {"ok": False, "error": "readiness_requires_postgres_dsn (CREDITS_DB_URL)"},
                ensure_ascii=False,
            )
        )
        return 1

    baseline = _load_baseline(args.baseline)
    pools = [p.strip() for p in str(args.pools).split(",") if p.strip()]
    reports: List[PoolReadiness] = []

    for pool in pools:
        if pool == POOL_VIDEO:
            rep = check_pool(
                dsn=dsn,
                pool=POOL_VIDEO,
                baseline=baseline.get(POOL_VIDEO),
                reference_buckets=DEFAULT_VIDEO_REFERENCE_BUCKETS,
                min_pool_pickable=_env_int(
                    "READINESS_MIN_VIDEO_POOL_PICKABLE", DEFAULT_MIN_VIDEO_POOL_PICKABLE
                ),
                min_bucket_candidates=_env_int(
                    "READINESS_MIN_BUCKET_CANDIDATES", DEFAULT_MIN_BUCKET_CANDIDATES
                ),
                check_timeline=True,
            )
        elif pool == POOL_PHOTO:
            # Stills have no duration -> the interval invariant is video-only.
            rep = check_pool(
                dsn=dsn,
                pool=POOL_PHOTO,
                baseline=baseline.get(POOL_PHOTO),
                reference_buckets=DEFAULT_PHOTO_REFERENCE_BUCKETS,
                min_pool_pickable=_env_int(
                    "READINESS_MIN_PHOTO_POOL_PICKABLE", DEFAULT_MIN_PHOTO_POOL_PICKABLE
                ),
                min_bucket_candidates=_env_int(
                    "READINESS_MIN_BUCKET_CANDIDATES", DEFAULT_MIN_BUCKET_CANDIDATES
                ),
                check_timeline=False,
            )
        else:
            print(json.dumps({"ok": False, "error": f"unknown pool: {pool}"}))
            return 1
        reports.append(rep)

    by_pool = {r.pool: r.as_dict() for r in reports}
    blocking = [
        r for r in reports if r.pool == POOL_VIDEO or (args.photo_required and r.pool == POOL_PHOTO)
    ]
    ok = all(r.ok for r in blocking)
    out = {
        "ok": ok,
        "node": os.environ.get("ORCHESTRATOR_NODE_NAME") or platform.node(),
        "revision": os.environ.get("GIT_REVISION") or os.environ.get("BLAST_IMAGE_TAG", ""),
        "photo_required": bool(args.photo_required),
        "pools": by_pool,
    }
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(payload, encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
