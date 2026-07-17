# app/photo_comp.py
# -*- coding: utf-8 -*-
"""Photo flow (4:3) payload builder.

Separate from the footage path (footage_comp.py) — the photo flow renders its own
standalone 1920×1440 composition (founder's cover-fit + scale-anim + flash JSX,
ported into templates/photo_template.j2). It does NOT touch the footage template.

build_photo_payload turns a list of picked photos (file_name + remote S3 url)
into:
  - footage_layers: minimal blueprints carrying source_footage{file_name,
    remote_url} so the SAME render manifest (render_manifest.collect_media_urls_
    from_render_payload) downloads the photos into media/video/<file_name> on the
    node — identical media contract to footage.
  - photo_job: the layout spec the photo JSX reads (comp dims, fps, per-photo
    segments, style grade, transition, cover/scale/flash constants).

Pure / no I/O so it is unit-testable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.video_timing import AE_FPS

# Comp geometry for the photo flow: a standalone horizontal 4:3 render (the
# founder's reference comp), independent of the vertical footage main comp.
PHOTO_COMP_W = 1920
PHOTO_COMP_H = 1440

# Default per-photo on-screen length: 36 frames @ 23.976fps ≈ 1.5015s (matches
# the founder's reference timing). Configurable per job.
DEFAULT_SEGMENT_FRAMES = 36

# Scale-animation constants (in scale POINTS, not pixels) — see the founder's
# build_photos.jsx: base cover scale → +grow over the clip → +punch shootout in
# the final punch_frames frames.
PHOTO_ANIM = {
    "grow": 10,
    "punch": 20,
    "punch_frames": 4,
    "overscan": 1.002,
    "ease": 33.33,
    # Flash adjustment (Brightness & Contrast): peak → 0 → 0 → peak.
    "flash_amount": 30,
    "flash_in_frames": 6,
    "flash_out_frames": 8,
}

# Allowed stylization grades + transitions (kept in sync with the schema literals
# in services/orchestrator/schemas.py — phase 3). "none" = founder's plain look.
PHOTO_STYLES = ("none", "warm", "cold", "vintage", "bw", "vhs", "night_vision")
PHOTO_TRANSITIONS = ("flash", "none", "slide", "zoom", "whip")


def _photo_layer_blueprint(*, file_name: str, remote_url: str, z_index: int) -> Dict[str, Any]:
    """Minimal blueprint whose only job is to make the render manifest download
    the photo into media/video/<file_name>. The photo JSX does the real layout
    from photo_job, so this carries no transform/keyframes."""
    return {
        "name": file_name,
        "type": "video",  # render_manifest routes non-audio source_footage to media/video/
        "z_index": int(z_index),
        "text_data": {
            "source_footage": {
                "file_name": file_name,
                "remote_url": str(remote_url or ""),
                "file_path": str(remote_url or ""),
            },
        },
    }

def _audio_layer_blueprint(*, file_name: str, locator: str) -> Dict[str, Any]:
    """Declare the already-downloaded main track for manifest naming + JSX."""
    return {
        "name": "MAIN_AUDIO",
        "type": "audio",
        "z_index": 10000,
        "text_data": {
            "layer_meta": {"audioEnabled": True},
            "source_footage": {
                "file_name": file_name,
                "remote_url": "",
                "file_path": locator,
            },
        },
    }



def build_photo_segments(
    photos: List[Dict[str, Any]],
    *,
    fps: float = AE_FPS,
    segment_frames: int = DEFAULT_SEGMENT_FRAMES,
) -> List[Dict[str, Any]]:
    """Sequential [in, out, file_name] segments — one slot per picked photo, each
    segment_frames long, back-to-back (out of one == in of next)."""
    fps = float(fps)
    if fps <= 0:
        raise ValueError("fps must be positive")
    if segment_frames <= 0:
        raise ValueError("segment_frames must be positive")
    seg_dur = segment_frames / fps
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(photos):
        fn = str(p.get("file_name") or "").strip()
        if not fn:
            raise RuntimeError(f"photo #{i} has no file_name")
        t_in = round(i * seg_dur, 6)
        t_out = round((i + 1) * seg_dur, 6)
        out.append({"in": t_in, "out": t_out, "file_name": fn})
    return out


def extract_photos_and_segments_from_footage_cfg(
    footage_cfg: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """From a stage2 footage_config (picker output for the photo pool) derive:
      - photos:   unique [{file_name, remote_url}] for the render manifest
      - segments: [{in, out, file_name}] from each footage layer's interval

    Reusing stage2's interval timing keeps the photo render aligned to the track
    (instead of fixed-length slots). Only type=='footage' layers are photos; the
    audio_only layer and overlays are ignored.
    """
    layers = footage_cfg.get("layers") if isinstance(footage_cfg, dict) else None
    if not isinstance(layers, list):
        raise RuntimeError("footage_config has no layers[] for photo extraction")
    photos: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []
    seen: set = set()
    for it in layers:
        if not isinstance(it, dict) or str(it.get("type")) != "footage":
            continue
        fn = str(it.get("file_name") or "").strip()
        remote = str(it.get("file_path") or it.get("remote_url") or "").strip()
        if not fn:
            continue
        try:
            t_in = float(it["in_point"])
            t_out = float(it["out_point"])
        except (KeyError, TypeError, ValueError):
            continue
        if t_out <= t_in:
            continue
        segments.append({"in": round(t_in, 6), "out": round(t_out, 6), "file_name": fn})
        if fn not in seen:
            seen.add(fn)
            photos.append({"file_name": fn, "remote_url": remote})
    if not segments:
        raise RuntimeError("no usable photo layers in footage_config (need type=footage with in/out)")
    return photos, segments


def build_photo_payload(
    photos: List[Dict[str, Any]],
    *,
    style: str = "none",
    transition: str = "flash",
    fps: float = AE_FPS,
    segment_frames: int = DEFAULT_SEGMENT_FRAMES,
    comp_w: int = PHOTO_COMP_W,
    comp_h: int = PHOTO_COMP_H,
    anim: Optional[Dict[str, Any]] = None,
    segments: Optional[List[Dict[str, Any]]] = None,
    audio_file_name: str = "",
    audio_locator: str = "",
) -> Dict[str, Any]:
    """Build the full photo render payload (footage_layers + photo_job).

    photos: [{file_name, remote_url}] in display order (the picker's photo pool).
    segments: optional explicit [{in,out,file_name}] (e.g. from stage2 timing);
    when omitted, sequential fixed-length slots are generated. Raises on empty
    input or invalid style/transition (No Fallback Policy) rather than rendering
    silently empty.
    """
    if not photos:
        raise RuntimeError("build_photo_payload: no photos provided")
    style = (str(style or "none").strip().lower() or "none")
    transition = (str(transition or "flash").strip().lower() or "flash")
    if style not in PHOTO_STYLES:
        raise RuntimeError(f"unknown photo style {style!r} (allowed: {PHOTO_STYLES})")
    if transition not in PHOTO_TRANSITIONS:
        raise RuntimeError(f"unknown photo transition {transition!r} (allowed: {PHOTO_TRANSITIONS})")

    if segments is not None:
        if not segments:
            raise RuntimeError("build_photo_payload: explicit segments are empty")
        segments = [
            {"in": float(s["in"]), "out": float(s["out"]), "file_name": str(s["file_name"])}
            for s in segments
        ]
    else:
        segments = build_photo_segments(photos, fps=fps, segment_frames=segment_frames)

    audio_name = str(audio_file_name or "").strip()
    locator = str(audio_locator or "").strip()
    if audio_name:
        if "/" in audio_name or "\\" in audio_name:
            raise RuntimeError(f"photo audio_file_name must be a basename, got {audio_name!r}")
        locator = locator or f"media/audio/{audio_name}"
        if not locator.startswith("media/audio/") or ".." in locator:
            raise RuntimeError(f"photo audio locator must stay under media/audio, got {locator!r}")
    elif locator:
        raise RuntimeError("photo audio locator requires audio_file_name")

    footage_layers: List[Dict[str, Any]] = []
    if audio_name:
        footage_layers.append(_audio_layer_blueprint(file_name=audio_name, locator=locator))
    seen: set = set()
    z = 100
    for p in photos:
        fn = str(p.get("file_name") or "").strip()
        remote = str(p.get("remote_url") or p.get("file_path") or "").strip()
        if not remote:
            raise RuntimeError(f"photo {fn!r} has no remote_url/file_path (render node needs s3/http)")
        if fn in seen:
            continue
        seen.add(fn)
        footage_layers.append(_photo_layer_blueprint(file_name=fn, remote_url=remote, z_index=z))
        z += 1

    photo_job = {
        "comp_w": int(comp_w),
        "comp_h": int(comp_h),
        "fps": float(fps),
        "style": style,
        "transition": transition,
        "config": dict(anim or PHOTO_ANIM),
        "segments": segments,
        "audio": ({"file_name": audio_name, "locator": locator} if audio_name else None),
    }

    return {
        "project": {"mainCompName": "Photo Render", "mediaType": "photo"},
        # entry_comp is read by the render dispatch (render_manifest) so the node
        # renders the photo comp instead of the footage "Main Render".
        "entry_comp": "Photo Render",
        "photo_job": photo_job,
        "footage_layers": footage_layers,
        "text_layers": [],
    }
