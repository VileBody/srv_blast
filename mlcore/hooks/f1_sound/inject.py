"""F1 «Звук» audio injection — user-uploaded sound in the pre-drop window.

Adds ONE audio footage layer carrying the user's sound (resolved by the AE node
from a remote URL, like the F5 TTS wav). Placement formula (per product spec):

    in_point  = F1_LEAD_PAD_SEC                 (0.5s after clip start)
    out_point = drop_time − F1_TAIL_PAD_SEC     (0.5s before the drop)

so the sound sits entirely before the hook with a small breath on both sides.
No subtitle, no ducking — the sound plays as-is, AE trims the source to the
layer window. Requires drop_time > LEAD+TAIL (else the window is non-positive).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Same z-band as F5 audio: between track-audio (z=2) and video (z=100+).
F1_AUDIO_Z_INDEX = 5

# Breathing pads around the pre-drop sound (seconds).
F1_LEAD_PAD_SEC = 0.5
F1_TAIL_PAD_SEC = 0.5

DEFAULT_AUDIO_COMP = "Comp 1"

F1_AUDIO_ENVELOPE = {
    "fade_in_s": 0.05,
    "fade_out_s": 0.10,
    "min_db": -48.0,
}


def f1_audio_window(drop_time: float) -> tuple[float, float]:
    """(in_point, out_point) comp-relative seconds for the pre-drop sound."""
    in_sec = F1_LEAD_PAD_SEC
    out_sec = float(drop_time) - F1_TAIL_PAD_SEC
    return in_sec, out_sec


def inject_f1_audio(
    footage_layers: list[dict[str, Any]],
    *,
    sound_url: str,
    drop_time: float,
    target_comp_name: str = DEFAULT_AUDIO_COMP,
) -> list[dict[str, Any]]:
    """Append the user's pre-drop sound as an audio layer. Pure (no mutation)."""
    sound_url = str(sound_url or "").strip()
    if not sound_url:
        raise ValueError("f1 audio: sound_url is empty")
    in_sec, out_sec = f1_audio_window(drop_time)
    if not (out_sec > in_sec):
        raise ValueError(
            f"f1 audio: non-positive window (drop_time={drop_time}, "
            f"need drop_time > {F1_LEAD_PAD_SEC + F1_TAIL_PAD_SEC})"
        )

    # Derive a stable file_name from the URL path (AE uses it as the footage name).
    file_name = Path(sound_url.split("?", 1)[0].rstrip("/")).name or "f1_sound.mp3"

    new_layer: dict[str, Any] = {
        "name": "f1_hook_sound",
        "type": "footage",
        "in_point": float(in_sec),
        "out_point": float(out_sec),
        "z_index": F1_AUDIO_Z_INDEX,
        "text": "",
        "adjustment_layer": False,
        "comp_id": None,
        "comp_name": None,
        "source_rect": {},
        "props": {},
        "effects": {},
        "style_instructions": [],
        "text_data": {
            "layer_meta": {
                "comp_name_target": target_comp_name,
                "startTime": float(in_sec),
                "enabled": True,
                "audioEnabled": True,
                "motionBlur": False,
                "collapseTransformation": False,
                "blendingModeCode": "5212",
            },
            "source_footage": {
                "file_name": file_name,
                "file_path": "",
                "remote_url": sound_url,
            },
            "audio_envelope": dict(F1_AUDIO_ENVELOPE),
        },
    }

    logger.info(
        "f1.inject audio_layer name=%s in=%.3f out=%.3f url=%s",
        new_layer["name"], in_sec, out_sec, sound_url[:80],
    )
    return list(footage_layers) + [new_layer]
