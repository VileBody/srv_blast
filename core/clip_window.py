from __future__ import annotations

import os


DEFAULT_CLIP_WINDOW_MIN_SECONDS = 13.0
DEFAULT_CLIP_WINDOW_MAX_SECONDS = 30.0


def _read_env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid {name}={raw!r}; expected float") from e


def _fmt_sec(v: float) -> str:
    x = float(v)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}"


CLIP_WINDOW_MIN_SECONDS = _read_env_float("CLIP_WINDOW_MIN_SECONDS", DEFAULT_CLIP_WINDOW_MIN_SECONDS)
CLIP_WINDOW_MAX_SECONDS = _read_env_float("CLIP_WINDOW_MAX_SECONDS", DEFAULT_CLIP_WINDOW_MAX_SECONDS)

if CLIP_WINDOW_MIN_SECONDS <= 0.0:
    raise RuntimeError(f"CLIP_WINDOW_MIN_SECONDS must be > 0, got {CLIP_WINDOW_MIN_SECONDS!r}")
if CLIP_WINDOW_MAX_SECONDS <= CLIP_WINDOW_MIN_SECONDS:
    raise RuntimeError(
        f"CLIP_WINDOW_MAX_SECONDS must be > CLIP_WINDOW_MIN_SECONDS "
        f"(got {CLIP_WINDOW_MAX_SECONDS!r} <= {CLIP_WINDOW_MIN_SECONDS!r})"
    )

CLIP_WINDOW_MIN_LABEL = _fmt_sec(CLIP_WINDOW_MIN_SECONDS)
CLIP_WINDOW_MAX_LABEL = _fmt_sec(CLIP_WINDOW_MAX_SECONDS)
CLIP_WINDOW_RANGE_LABEL = f"{CLIP_WINDOW_MIN_LABEL}..{CLIP_WINDOW_MAX_LABEL}"
CLIP_WINDOW_RANGE_S_LABEL = f"{CLIP_WINDOW_RANGE_LABEL}s"
