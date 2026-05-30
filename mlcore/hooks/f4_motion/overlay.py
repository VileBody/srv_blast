"""Builder for F4 motion-hook overlay JSX blocks.

`build_overlay_jsx(device, bpm)` returns a self-contained ExtendScript snippet
that builds the chosen device's overlay layers on top of `MAIN_COMP`. The
snippet is injected verbatim into the render template (raw, not tojson).

Each device template lives in `devices/<device>.jsx` with two substitution
tokens:
  __F4_BPM__   -> measured BPM (drives in-tempo keyframes; NOT layer length)
  __F4_DEVICE__ -> device id (for logging only)

LEAD_BY_DEVICE is the per-template "cover layer" duration in seconds (the
outPoint of the black cover solid in the source script). It is the amount the
bot subtracts from the hook to find the reframed clip_start. It is a FIXED
per-template constant — NOT bpm-scaled (layer length does not depend on bpm;
only the keyframes inside shapes are reflowed to the beat).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict

_DEVICES_DIR = Path(__file__).resolve().parent / "devices"

# BPM the device keyframes were authored under. The injectable JSX reflows its
# internal timings by refBpm/bpm; the bot reframes the clip window by the SAME
# factor (lead_eff = LEAD_BY_DEVICE * F4_REF_BPM / bpm) so the cover-layer end
# lands exactly on the drop at any tempo. Keep in sync with CONFIG.refBpm in
# the device .jsx files.
F4_REF_BPM = 128.0

# Cover-layer outPoint (seconds) taken from each source script's black solid.
# swipe/tap/holdfinger: 4.304s ; pinch: 4.204s ; head: 4.004s.
LEAD_BY_DEVICE: Dict[str, float] = {
    "swipe": 4.3043043043043,
    "tap": 4.3043043043043,
    "holdfinger": 4.3043043043043,
    "pinch": 4.2042042042042,
    "head": 4.004004004004,
}

# Devices wired into the pipeline. A device is "ready" once its
# devices/<device>.jsx injectable template exists.
F4_DEVICES = ("swipe", "tap", "pinch", "holdfinger", "head")


def build_overlay_jsx(*, device: str, bpm: float) -> str:
    """Return the injectable JSX block for `device` with `bpm` baked in.

    No-fallback: unknown device or invalid bpm raises. The caller (build worker)
    must only pass devices it intends to render.
    """
    dev = str(device or "").strip().lower()
    if dev not in LEAD_BY_DEVICE:
        raise ValueError(
            f"unknown F4 device {device!r}; known={sorted(LEAD_BY_DEVICE)}"
        )
    if dev not in F4_DEVICES:
        raise ValueError(
            f"F4 device {dev!r} is not wired yet; available={list(F4_DEVICES)}"
        )

    b = float(bpm)
    if not math.isfinite(b) or b <= 0.0:
        raise ValueError(f"invalid bpm for F4 overlay: {bpm!r}")

    tmpl_path = _DEVICES_DIR / f"{dev}.jsx"
    if not tmpl_path.exists():
        raise FileNotFoundError(f"F4 device template missing: {tmpl_path}")

    text = tmpl_path.read_text(encoding="utf-8")
    if "__F4_BPM__" not in text:
        raise RuntimeError(f"F4 device template {tmpl_path} missing __F4_BPM__ token")

    # bpm is embedded as a numeric literal; round to 3 decimals for stability.
    text = text.replace("__F4_BPM__", repr(round(b, 3)))
    text = text.replace("__F4_DEVICE__", dev)
    return text
