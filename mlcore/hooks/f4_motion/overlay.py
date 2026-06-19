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

import json
import math
from pathlib import Path
from typing import Dict, Optional

_DEVICES_DIR = Path(__file__).resolve().parent / "devices"
# F3 lightning (hook_light) reused for the explicit drop flash — single source.
_F3_DIR = Path(__file__).resolve().parent.parent / "f3_effect"
_F3_HOOK_LIGHT_SCRIPT = "hooks/rebuild_light.jsx"
_PLACE_REF = "Текст"

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

# Devices whose JSX uses a FIXED time scale (TS = CONFIG.timeScale = 1.0) instead
# of reflowing by refBpm/bpm. For them the cover length is the literal LEAD (not
# bpm-scaled), so the effective lead used for the clip reframe + TOFF must NOT be
# bpm-scaled either — otherwise cover-end misses the drop at bpm != refBpm. Keep
# in sync with the `var TS = CONFIG.timeScale` devices.
F4_FIXED_LEAD_DEVICES = frozenset({"tap", "holdfinger"})


def effective_lead(device: str, bpm: float) -> float:
    """Lead (seconds) the clip is reframed back from the drop for `device`.

    Fixed-lead (TS=1) devices use the literal LEAD; the rest scale by refBpm/bpm
    to match their JSX time reflow. Single source of truth for both the bot
    reframe (clip_start = drop - lead) and the overlay TOFF below.
    """
    lead = float(LEAD_BY_DEVICE[device])
    if device in F4_FIXED_LEAD_DEVICES:
        return lead
    b = float(bpm)
    if b > 0.0:
        return lead * (F4_REF_BPM / b)
    return lead

# Devices wired into the pipeline. A device is "ready" once its
# devices/<device>.jsx injectable template exists.
F4_DEVICES = ("swipe", "tap", "pinch", "holdfinger", "head")


def _read_f3_hook_light() -> str:
    p = (_F3_DIR / _F3_HOOK_LIGHT_SCRIPT).resolve()
    if not p.exists():
        raise FileNotFoundError(f"f3 hook_light script missing: {p}")
    return p.read_text(encoding="utf-8")


def build_overlay_jsx(*, device: str, bpm: float, drop_time: Optional[float] = None) -> str:
    """Return the injectable JSX block for `device` with `bpm` baked in.

    No-fallback: unknown device or invalid bpm raises. The caller (build worker)
    must only pass devices it intends to render.

    drop_time (comp-relative seconds): when provided, an explicit F3 lightning
    (hook_light) is fired on the drop on top of the device overlay — a clear,
    device-independent flash so the drop always reads (the device's own subtle
    minimax flash stays too). None → no extra lightning.
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

    # Drop-anchor offset (TOFF) added inside the device's t(): shifts ALL
    # t()-based timings so the cover-layer end (t(LEAD)) lands on the ACTUAL
    # drop, even if stage2 trimmed the render window away from the bot's
    # reframed clip_start. When the reframe is exact, drop_time ≈ t(LEAD) →
    # offset ≈ 0 → no-op (common case untouched). No drop → 0.
    toff = 0.0
    if drop_time is not None and float(drop_time) > 0.0:
        # Use the SAME effective lead the bot reframed with (fixed for TS=1
        # devices, bpm-scaled otherwise) so cover-end == drop at any tempo.
        toff = float(drop_time) - effective_lead(dev, b)
    if "__F4_TOFF__" not in text:
        raise RuntimeError(f"F4 device template {tmpl_path} missing __F4_TOFF__ token")
    text = text.replace("__F4_TOFF__", repr(round(toff, 4)))

    # Tag the device's bait layers (black cover + hold/release text + finger) so a
    # late template step can raise them back above the JSX subtitles, which are
    # injected AFTER this overlay and otherwise land on top (AE adds new layers at
    # index 1). The device script builds its layers at the top; everything added
    # between the pre-count snapshot and here is a device layer → comment-mark it.
    # The drop lightning below is intentionally left UNmarked (it stays placed
    # `below:Текст`). Keep "__F4_OVERLAY__" in sync with the raise step in the
    # render template.
    text = (
        '(function(){ if (typeof MAIN_COMP !== "undefined" && MAIN_COMP) {'
        ' $.global.__F4_PRE_COUNT = MAIN_COMP.numLayers; } })();\n'
        + text + "\n"
        + '(function(){ if (typeof MAIN_COMP === "undefined" || !MAIN_COMP) { return; }'
        ' var pre = $.global.__F4_PRE_COUNT || 0; var added = MAIN_COMP.numLayers - pre;'
        ' for (var i = 1; i <= added; i++) { try { MAIN_COMP.layer(i).comment = "__F4_OVERLAY__"; } catch (e) {} } })();\n'
    )

    # Explicit lightning on the drop (reuses F3 rebuild_light.jsx).
    if drop_time is not None and float(drop_time) > 0.0:
        drop = float(drop_time)
        parts = [text]
        parts.append("/* == F4 drop lightning (F3 hook_light) == */")
        parts.append("(function(){")
        parts.append('  if (typeof MAIN_COMP === "undefined" || !MAIN_COMP) { return; }')
        parts.append(
            "  $.global.__BLAST = { targetCompName: MAIN_COMP.name, dropTime: "
            + json.dumps(drop) + ', place: "below:' + _PLACE_REF + '", cuts: [] };'
        )
        parts.append("  (function(){")
        parts.append(_read_f3_hook_light())
        parts.append("  })(); $.global.__BLAST = null;")
        parts.append("})();")
        text = "\n".join(parts)

    return text
