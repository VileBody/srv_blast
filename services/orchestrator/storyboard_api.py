"""I/O around mlcore.storyboard_plan for the web wizard (FX timeline + «Пул»).

Three read-only calls, none of them starts a render:

* cuts         — download the track, run the hook analysis the render runs,
                 return the render's cuts for every pace («реже/авто/чаще»);
* pick         — the render's clip picks for every video of one bucket, with
                 presigned preview links, plus the pinned plan per video;
* alternatives — replacement candidates for one shot.

The picker pool is hydrated from the same Postgres registry as a build
(``tasks._ensure_video_picker_artifacts_from_registry``), so the preview and the
render see one inventory. Collections are not supported yet: their clips are the
folder itself and the storyboard would have nothing to choose.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from mlcore import storyboard_plan as sp

log = logging.getLogger("orchestrator.storyboard")

PREVIEW_URL_TTL_S = int(os.environ.get("STORYBOARD_PREVIEW_URL_TTL_S") or 6 * 3600)


@dataclass
class LoadedBucket:
    ctx: sp.BucketContext
    # file_name -> raw inventory row (file_path, segment_base_sec, media_file_name)
    raw_by_name: Dict[str, Dict[str, Any]]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_s3(url: str) -> tuple[str, str]:
    p = urlparse(str(url or ""))
    if p.scheme != "s3" or not p.netloc or not p.path.strip("/"):
        raise sp.StoryboardPlanError(f"not an s3:// url: {url!r}")
    return p.netloc, p.path.lstrip("/")


def load_bucket(*, theme: str, tags_group: str, seed_key: str) -> LoadedBucket:
    """Hydrate the video picker pool exactly like a build and resolve one slot."""
    if theme == "collection":
        raise sp.StoryboardPlanError("storyboard is not available for collections yet")
    from mlcore.footage_picker import (
        load_footage_style_metadata_rows,
        load_picker_assets_from_inventory,
        map_inventory_assets_with_style_metadata,
        merge_footage_style_metadata_rows,
    )
    from mlcore.gemini_orchestrator import _load_footage_cooldown

    from .tasks import _ensure_video_picker_artifacts_from_registry

    root = _repo_root()
    inv_raw = str(os.environ.get("FOOTAGE_INVENTORY_JSON") or os.environ.get("FOOTAGE_INVENTORY_OUT") or "data/footage_inventory.json").strip()
    snap_raw = str(os.environ.get("FOOTAGE_TAGS_SNAPSHOT_PATH") or "data/footage_tags_snapshot.json").strip()
    _ensure_video_picker_artifacts_from_registry(
        repo_root=root, inventory_path=inv_raw, snapshot_path=snap_raw, cache_key="storyboard",
    )
    inv_path = Path(inv_raw) if Path(inv_raw).is_absolute() else root / inv_raw
    snap_path = Path(snap_raw) if Path(snap_raw).is_absolute() else root / snap_raw
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    raw_by_name = {
        str(a.get("file_name") or ""): a for a in (inv.get("assets") or []) if isinstance(a, dict)
    }
    picker_assets = load_picker_assets_from_inventory(inv)
    index = merge_footage_style_metadata_rows(load_footage_style_metadata_rows(db_paths=[snap_path]))
    mapped, _unmapped = map_inventory_assets_with_style_metadata(
        assets=picker_assets, metadata_index=index, require_metadata=True,
    )
    cooldown = _load_footage_cooldown(f"{theme}:{tags_group}", mapped, logger=log)
    ctx = sp.resolve_bucket(
        theme=theme,
        tags_group=tags_group,
        mapped_assets=mapped,
        seed_key=seed_key,
        total_assets=len(picker_assets),
        cooldown_by_name=cooldown,
    )
    return LoadedBucket(ctx=ctx, raw_by_name=raw_by_name)


def analyze_window(*, audio_s3_url: str, clip_start_abs: float, clip_end_abs: float) -> Any:
    from mlcore.audio_analysis import analyze_focus_clip
    from src.storage.s3 import get_s3_client

    bucket, key = _parse_s3(audio_s3_url)
    with tempfile.TemporaryDirectory(prefix="storyboard_cuts_") as td:
        local = Path(td) / f"audio{Path(key).suffix or '.mp3'}"
        get_s3_client().download_file(bucket, key, str(local))
        return analyze_focus_clip(audio_path=local, clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs)


def presign(file_path: str) -> str:
    from src.storage.s3 import get_s3_client

    bucket, key = _parse_s3(file_path)
    return get_s3_client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=PREVIEW_URL_TTL_S,
    )


class StoryboardService:
    """Thin orchestration; loaders are injectable so the API is testable offline."""

    def __init__(
        self,
        *,
        bucket_loader: Callable[..., LoadedBucket] = load_bucket,
        analyzer: Callable[..., Any] = analyze_window,
        signer: Callable[[str], str] = presign,
    ) -> None:
        self._load = bucket_loader
        self._analyze = analyzer
        self._sign = signer

    def cuts(self, *, audio_s3_url: str, clip_start_abs: float, clip_end_abs: float, user_drop_t: Optional[float]) -> Dict[str, Any]:
        if clip_end_abs <= clip_start_abs:
            raise sp.StoryboardPlanError("clip_end must be after clip_start")
        analysis = self._analyze(audio_s3_url=audio_s3_url, clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs)
        res = sp.compute_cuts(
            analysis, clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, user_drop_t=user_drop_t,
        )
        return {
            "clip_start_abs": res.clip_start_abs,
            "clip_end_abs": res.clip_end_abs,
            "bpm": res.bpm,
            "drop_t": res.drop_t,
            "beats_abs": res.beats_abs,
            "cuts_by_pace": res.cuts_by_pace,
        }

    def _clip_view(self, loaded: LoadedBucket, clip: Any) -> Dict[str, Any]:
        raw = loaded.raw_by_name.get(clip.file_name) or {}
        meta = loaded.ctx.by_name.get(clip.file_name) or {}
        file_path = str(raw.get("file_path") or "")
        # A virtual segment of a long source plays from segment_base_sec.
        offset = float(raw.get("segment_base_sec") or 0.0) + float(clip.source_offset_sec)
        return {
            **clip.model_dump(),
            "preview_url": self._sign(file_path) if file_path.startswith("s3://") else None,
            "preview_offset_sec": round(offset, 3),
            "tags": list(meta.get("meta_theme_tags") or [])[:4],
        }

    def pick(
        self,
        *,
        theme: str,
        tags_group: str,
        clip_start_abs: float,
        clip_end_abs: float,
        switch_points_abs: List[float],
        videos: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not videos:
            raise sp.StoryboardPlanError("pick needs at least one video")
        requests = [
            sp.VideoRequest(
                seed_key=str(v.get("seed_key") or ""),
                pins={int(k): str(n) for k, n in (v.get("pins") or {}).items()},
            )
            for v in videos
        ]
        if any(not r.seed_key for r in requests):
            raise sp.StoryboardPlanError("every video needs a seed_key")
        loaded = self._load(theme=theme, tags_group=tags_group, seed_key=requests[0].seed_key)
        picks = sp.pick_batch(
            loaded.ctx,
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            switch_points_abs=switch_points_abs,
            videos=requests,
        )
        return {
            "videos": [
                {
                    "seed_key": p.seed_key,
                    "clips": [self._clip_view(loaded, c) for c in p.selection.clips],
                    "repeats": p.repeats,
                    "plan": sp.build_plan(
                        clip_start_abs=clip_start_abs,
                        clip_end_abs=clip_end_abs,
                        switch_points_abs=switch_points_abs,
                        selection=p.selection,
                    ),
                }
                for p in picks
            ]
        }

    def alternatives(
        self,
        *,
        theme: str,
        tags_group: str,
        clip_start_abs: float,
        clip_end_abs: float,
        switch_points_abs: List[float],
        interval_idx: int,
        seed_key: str,
        exclude_file_names: List[str],
        limit: int,
    ) -> Dict[str, Any]:
        loaded = self._load(theme=theme, tags_group=tags_group, seed_key=seed_key)
        names = sp.alternatives(
            loaded.ctx,
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            switch_points_abs=switch_points_abs,
            interval_idx=interval_idx,
            seed_key=seed_key,
            exclude_file_names=exclude_file_names,
            limit=limit,
        )
        out = []
        for name in names:
            raw = loaded.raw_by_name.get(name) or {}
            meta = loaded.ctx.by_name.get(name) or {}
            file_path = str(raw.get("file_path") or "")
            out.append({
                "file_name": name,
                "preview_url": self._sign(file_path) if file_path.startswith("s3://") else None,
                "preview_offset_sec": round(float(raw.get("segment_base_sec") or 0.0), 3),
                "duration_sec": float(meta.get("duration_sec") or 0.0),
                "tags": list(meta.get("meta_theme_tags") or [])[:4],
            })
        return {"candidates": out}
