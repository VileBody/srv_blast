"""Footage BUCKET PREVIEW generation — pure/core helpers (precision flow, phase 4).

A bucket preview = a short 1080x1920 example reel built from ~5 representative
clips of a (theme, tags_group) bucket, plus a short RU description, registered
(S3 url + Telegram file_id) so the bot can show it in the vibe shortlist.

This module holds the DETERMINISTIC, side-effect-free pieces so they are unit
testable without S3 / AE / Telegram:
  - select_bucket_clips(): reuse the production picker scoring (_build_raw_pool)
    to pick the top-N representative clips for a bucket, deterministic by seed.
  - build_bucket_description(): RU one-liner from label + priority tags (no LLM).
  - build_montage_spec() / render_montage_jsx() / montage_media_payload(): turn a
    selection into the AE example-montage inputs (JSX text + media[] for the node).
  - the footage_bucket_previews.json store (load / upsert / save).

The live I/O (S3 existence check, render-node dispatch, Telegram file_id capture)
lives in scripts/build_bucket_previews.py.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from mlcore import footage_picker as fp
from mlcore.footage_bucket_catalog import Bucket

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
SELECTION_SCORE_KEY = fp._SELECTION_RANK_SCORE_KEY

DEFAULT_TOP_N = 5            # clips per preview
DEFAULT_MIN_CLIPS = 3       # below this a bucket is "thin" (nothing to show well)
SECONDS_PER_CLIP = 1.5
COMP_W = 1080
COMP_H = 1920
COMP_FPS = 23.976

PREVIEWS_STORE_VERSION = 1
DEFAULT_PREVIEWS_PATH = "data/footage_bucket_previews.json"

_MOOD_RU = {"minor": "минор", "major": "мажор"}

LABEL_FONT = "Point-Regular"  # AE PostScript name (Point family is installed on the node)


def display_label(label: str) -> str:
    """Tidy a bucket label for on-screen display: slash -> comma."""
    import re
    return re.sub(r"\s*/\s*", ", ", str(label or "")).strip()


def dedup_buckets_by_label(buckets: List["Bucket"]) -> List["Bucket"]:
    """Keep one bucket per unique display label (first in order). Two buckets can
    share a tags_group label across themes (e.g. lonely_paths under heartbreak and
    betrayal); the shortlist should not show the same vibe name twice."""
    seen: set = set()
    out: List["Bucket"] = []
    for b in buckets:
        key = display_label(b.label).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


# --------------------------------------------------------------------------- #
# Clip selection (reuses the production picker scoring)
# --------------------------------------------------------------------------- #
def _raw_pick_from_bucket(bucket: Bucket) -> SimpleNamespace:
    """Duck-typed raw_pick for fp._build_raw_pool.

    _build_raw_pool only reads raw_pick.theme / .tags_group / .filters.{
    priority_theme_tags, exclude, exclude_tags, require_people, color_priority}.
    We feed the bucket's tags directly so scoring is identical to the prod
    tag-only (vibe-flow) path: pure overlap with priority_theme_tags (+0.5 color).
    """
    filters = SimpleNamespace(
        priority_theme_tags=list(bucket.priority_tags),
        exclude=[],
        exclude_tags=list(bucket.exclude_tags),
        require_people=None,
        color_priority=list(bucket.color or []),
    )
    return SimpleNamespace(theme=bucket.theme, tags_group=bucket.tags_group, filters=filters)


def _clip_sort_key(item: Dict[str, Any], *, seed: str):
    """Score desc, then a deterministic seeded hash tiebreak, then file_name."""
    fn = str(item.get("file_name") or "")
    score = float(item.get(SELECTION_SCORE_KEY) or 0.0)
    h = hashlib.sha256(f"{seed}:{fn}".encode("utf-8")).hexdigest()
    return (-score, h, fn)


def select_bucket_clips(
    bucket: Bucket,
    mapped_assets: List[Dict[str, Any]],
    *,
    seed: str,
    top_n: int = DEFAULT_TOP_N,
    media_type: str = "video",
) -> List[Dict[str, Any]]:
    """Top-N representative clips for a bucket, deterministic by seed.

    `mapped_assets` are inventory assets enriched with style metadata
    (meta_theme_tags / meta_color_tone / meta_people_type) via
    footage_picker.map_inventory_assets_with_style_metadata().

    media_type must match the pool being previewed. For photo buckets it has to be
    "photo" so the picker applies the photo-only anchors/exclusions
    (PHOTO_REQUIRE_GROUPS / PHOTO_EXCLUDE_TERMS) — otherwise a preview would draw
    stills the real photo flow would never pick (e.g. beach silhouettes for the
    digital-silhouette bucket). Passed explicitly rather than read from BG_MODE so
    an offline preview build does not depend on the ambient env.
    """
    raw_pick = _raw_pick_from_bucket(bucket)
    pool = fp._build_raw_pool(raw_pick, mapped_assets, media_type=media_type)
    pool.sort(key=lambda it: _clip_sort_key(it, seed=seed))
    n = max(0, int(top_n))
    return pool[:n]


def clip_ids_of(clips: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for c in clips:
        cid = c.get("clip_id") or fp._extract_clip_id(c.get("file_name"))
        if cid:
            out.append(str(cid))
    return out


# --------------------------------------------------------------------------- #
# Description (no LLM — label + priority tags is enough for MVP)
# --------------------------------------------------------------------------- #
def build_bucket_description(bucket: Bucket, *, max_tags: int = 4) -> str:
    label = str(bucket.label or bucket.tags_group or bucket.bucket_id).strip()
    tags = ", ".join([t for t in bucket.priority_tags[:max_tags] if t])
    mood = _MOOD_RU.get(bucket.mood, "")
    base = f"{label}: {tags}" if tags else label
    return f"{base} ({mood})" if mood else base


# --------------------------------------------------------------------------- #
# AE example-montage inputs
# --------------------------------------------------------------------------- #
def build_montage_spec(
    bucket: Bucket,
    clips: List[Dict[str, Any]],
    *,
    seconds_per_clip: float = SECONDS_PER_CLIP,
    width: int = COMP_W,
    height: int = COMP_H,
    fps: float = COMP_FPS,
    comp_name: str = "Bucket Preview",
    with_label: bool = True,
) -> Dict[str, Any]:
    """The MONTAGE object injected into the montage JSX template."""
    return {
        "comp_name": comp_name,
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
        "seconds_per_clip": float(seconds_per_clip),
        "label": display_label(bucket.label) if with_label else "",
        "label_font": LABEL_FONT,
        "clips": [
            {"file_name": str(c["file_name"]), "relpath": f"media/video/{c['file_name']}"}
            for c in clips
        ],
    }


def render_montage_jsx(spec: Dict[str, Any], template_text: str) -> str:
    """Inject `var MONTAGE = {...};` into the montage template at its marker."""
    marker = "/*__MONTAGE_DATA__*/"
    if marker not in template_text:
        raise RuntimeError("montage template missing /*__MONTAGE_DATA__*/ marker")
    blob = "var MONTAGE = " + json.dumps(spec, ensure_ascii=False) + ";"
    return template_text.replace(marker, blob)


def montage_media_payload(
    clips: List[Dict[str, Any]],
    *,
    url_by_file_name: Dict[str, str],
) -> List[Dict[str, str]]:
    """media[] for the render node: {url, relpath} per clip.

    url_by_file_name maps file_name -> remote (s3://... / http[s]) source.
    Raises if any selected clip has no resolved remote url (mirrors the prod
    rule: we only ever render clips that actually exist remotely).
    """
    out: List[Dict[str, str]] = []
    for c in clips:
        fn = str(c["file_name"])
        url = str(url_by_file_name.get(fn) or "").strip()
        if not url:
            raise RuntimeError(f"no remote url resolved for clip: {fn!r}")
        out.append({"url": url, "relpath": f"media/video/{fn}"})
    return out


# --------------------------------------------------------------------------- #
# Previews store (data/footage_bucket_previews.json)
# --------------------------------------------------------------------------- #
@dataclass
class PreviewEntry:
    bucket_id: str
    label: str = ""
    description: str = ""
    s3_url: str = ""
    file_id: str = ""
    file_id_public: str = ""
    clip_ids: List[str] = field(default_factory=list)
    status: str = "ok"          # ok | thin | error
    built_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_store() -> Dict[str, Any]:
    return {"version": PREVIEWS_STORE_VERSION, "previews": {}}


def load_previews_store(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return empty_store()
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"previews store root must be object: {p}")
    previews = obj.get("previews")
    if not isinstance(previews, dict):
        raise RuntimeError(f"previews store missing 'previews' object: {p}")
    obj.setdefault("version", PREVIEWS_STORE_VERSION)
    return obj


def previews_upsert(store: Dict[str, Any], entry: PreviewEntry) -> Dict[str, Any]:
    bid = str(entry.bucket_id or "").strip()
    if not bid:
        raise ValueError("PreviewEntry.bucket_id must be non-empty")
    store.setdefault("previews", {})[bid] = entry.to_dict()
    return store


def has_preview(store: Dict[str, Any], bucket_id: str, *, require_ok: bool = True) -> bool:
    """True when a usable preview already exists (idempotency check).

    require_ok: a 'thin'/'error' marker does NOT count as a usable preview, so a
    re-run still attempts those (until they succeed or are forced).
    """
    e = (store.get("previews") or {}).get(str(bucket_id).strip())
    if not isinstance(e, dict):
        return False
    if require_ok and str(e.get("status") or "ok") != "ok":
        return False
    return bool(str(e.get("file_id") or "").strip() or str(e.get("s3_url") or "").strip())


def save_previews_store(path: str | Path, store: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # sort previews by bucket_id for a stable, reviewable diff
    previews = store.get("previews") or {}
    ordered = {k: previews[k] for k in sorted(previews.keys())}
    out = {"version": int(store.get("version") or PREVIEWS_STORE_VERSION), "previews": ordered}
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
