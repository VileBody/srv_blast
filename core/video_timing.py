from __future__ import annotations

import math
from typing import Literal

AE_FPS: float = 23.9759979248047


def normalize_fps(raw: object, *, default: float = AE_FPS) -> float:
    try:
        fps = float(raw)
    except Exception:
        return float(default)
    if not math.isfinite(fps) or fps <= 0.0:
        return float(default)
    return float(fps)


def frame_duration_s(fps: float = AE_FPS) -> float:
    resolved = normalize_fps(fps)
    return 1.0 / float(resolved)


def frame_epsilon_s(fps: float = AE_FPS) -> float:
    return frame_duration_s(fps) / 10.0


def frames_to_seconds(frames: float, *, fps: float = AE_FPS) -> float:
    return float(frames) * frame_duration_s(fps)


def snap_seconds_to_frame(
    value: float,
    *,
    fps: float = AE_FPS,
    mode: Literal["nearest", "floor", "ceil"] = "nearest",
) -> float:
    resolved_fps = normalize_fps(fps)
    frame_pos = float(value) * float(resolved_fps)
    if mode == "floor":
        frame_idx = math.floor(frame_pos + 1e-9)
    elif mode == "ceil":
        frame_idx = math.ceil(frame_pos - 1e-9)
    else:
        frame_idx = round(frame_pos)
    return float(frame_idx) / float(resolved_fps)
