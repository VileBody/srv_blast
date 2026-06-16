"""5th-template JSX subtitle generators — build-side glue.

Two self-contained AE scripts (5th_template/trendy_subtitles.jsx,
5th_template/brat_subtitles.jsx) generate subtitle layers directly in the comp
from raw word-timings. This module:

  1. word_timings_from_transcript() — turns the pipeline's transcript_words
     ({text, t_start, t_end}) into the script's canonical JSON shape
     {"word_timings": [{word, start, end, focus}]}, COMP-RELATIVE (minus the
     render clip start) so words line up with the rendered window.
  2. build_jsx_subtitles_overlay() — loads the chosen script, inlines the JSON
     + target comp name (+ bpm for brat) via $.global, forces non-interactive /
     non-debug, and returns a raw injectable JSX block.

In these modes the normal Python text_layers pipeline is skipped — the injected
script owns the subtitles.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from core.subtitles_mode import (
    SUBTITLES_MODE_BRAT_5TH,
    SUBTITLES_MODE_TRENDY_5TH,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _REPO_ROOT / "5th_template"

_SCRIPT_BY_MODE = {
    SUBTITLES_MODE_TRENDY_5TH: "trendy_subtitles.jsx",
    SUBTITLES_MODE_BRAT_5TH: "brat_subtitles.jsx",
}

# brat uses BPM for the blinker; trendy ignores it.
_MODE_USES_BPM = {SUBTITLES_MODE_BRAT_5TH}

DEFAULT_TARGET_COMP = "Comp 1"


def hex_to_rgb01(hex_str: str) -> list[float] | None:
    """'#RRGGBB' (or 'RRGGBB') → [r, g, b] floats in 0..1, else None."""
    s = str(hex_str or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None


def _w_text(w: Any) -> str:
    if isinstance(w, dict):
        v = w.get("text", w.get("word", w.get("w")))
    else:
        v = getattr(w, "text", None)
    return "" if v is None else str(v)


def _w_start(w: Any) -> float:
    if isinstance(w, dict):
        v = w.get("t_start", w.get("start", w.get("s")))
    else:
        v = getattr(w, "t_start", None)
    return float(v)


def _w_end(w: Any) -> float:
    if isinstance(w, dict):
        v = w.get("t_end", w.get("end", w.get("e")))
    else:
        v = getattr(w, "t_end", None)
    return float(v)


def _w_focus(w: Any) -> bool:
    if isinstance(w, dict):
        return bool(w.get("focus", False))
    return bool(getattr(w, "focus", False))


def word_timings_from_transcript(
    words: list[Any],
    *,
    clip_start: float = 0.0,
    clip_end: float | None = None,
) -> list[dict[str, Any]]:
    """Build COMP-RELATIVE [{word, start, end, focus}] from transcript words.

    clip_start is the render comp's clip-window start (absolute track seconds);
    every timing is shifted by −clip_start so it aligns with the rendered comp.
    Words ending at/under clip_start (fully before the window) are dropped; a
    word straddling the start is clamped to start=0. If clip_end is given
    (absolute), words starting at/after it are dropped and ends are clamped.
    """
    cs = float(clip_start or 0.0)
    win = (float(clip_end) - cs) if clip_end is not None else None
    out: list[dict[str, Any]] = []
    for w in words or []:
        text = _w_text(w).strip()
        if not text:
            continue
        try:
            t0 = _w_start(w) - cs
            t1 = _w_end(w) - cs
        except (TypeError, ValueError):
            continue
        if t1 <= 0.0:  # fully before the clip window
            continue
        if win is not None and t0 >= win:  # fully after the clip window
            continue
        if t0 < 0.0:
            t0 = 0.0
        if win is not None and t1 > win:
            t1 = win
        if t1 <= t0:
            t1 = t0 + 0.05
        out.append({
            "word": text,
            "start": round(t0, 3),
            "end": round(t1, 3),
            "focus": _w_focus(w),
        })
    return out


def splice_voice_phrase(
    word_timings: list[dict[str, Any]],
    *,
    window_start: float,
    window_end: float,
    phrase: str,
    margin: float = 0.08,
) -> list[dict[str, Any]]:
    """Replace the clip words inside [window_start, window_end] with a hook voice
    phrase (F5 «Мысль» / F1 «Звук»), so the voice caption renders in the SAME
    trendy/brat style instead of a cloned Python layer.

    Comp-relative seconds throughout. Clip words overlapping the window (±margin)
    are dropped; the phrase is split into words distributed across the window
    proportional to length. Returns a new sorted list (input not mutated).
    """
    ws, we = float(window_start), float(window_end)
    text = str(phrase or "").strip()
    if we <= ws or not text:
        return list(word_timings)

    lo, hi = ws - float(margin), we + float(margin)
    kept = [
        w for w in (word_timings or [])
        if not (float(w.get("start", 0.0)) < hi and float(w.get("end", 0.0)) > lo)
    ]

    words = text.split()
    if not words:
        return kept

    total = sum(len(x) for x in words) or 1
    span = max(1e-4, we - ws)
    out = list(kept)
    cur = ws
    for i, wd in enumerate(words):
        seg_out = we if i == len(words) - 1 else cur + span * (len(wd) / total)
        out.append({
            "word": wd,
            "start": round(cur, 3),
            "end": round(seg_out, 3),
            "focus": True,
        })
        cur = seg_out

    out.sort(key=lambda w: float(w.get("start", 0.0)))
    return out


def _flip_flag_false(src: str, flag: str) -> str:
    """Force `flag:     true` → `flag:     false` (first CONFIG occurrence)."""
    return re.sub(rf"({flag}\s*:\s*)true", r"\1false", src, count=1)


def build_jsx_subtitles_overlay(
    *,
    mode: str,
    word_timings: list[dict[str, Any]],
    bpm: Optional[float] = None,
    target_comp: str = DEFAULT_TARGET_COMP,
    fill_hex: Optional[str] = None,
) -> str:
    """Return an injectable JSX block: prelude ($.global injects) + the script.

    fill_hex (e.g. '#FF2D55') overrides the subtitle text fill color in the
    trendy/brat script (via $.global.__BLAST_FILL). Raises if mode is not a
    5th-template JSX mode or the script is missing.
    """
    script_name = _SCRIPT_BY_MODE.get(mode)
    if not script_name:
        raise ValueError(f"build_jsx_subtitles_overlay: not a 5th JSX mode: {mode!r}")
    if not word_timings:
        raise ValueError("build_jsx_subtitles_overlay: empty word_timings")

    script_path = _TEMPLATE_DIR / script_name
    body = script_path.read_text(encoding="utf-8")

    # Headless: never pop a file dialog / alert.
    body = _flip_flag_false(body, "INTERACTIVE")
    body = _flip_flag_false(body, "DEBUG")

    payload = json.dumps({"word_timings": word_timings}, ensure_ascii=False)
    target_js = json.dumps(str(target_comp), ensure_ascii=False)

    prelude_lines = [
        "// ── blast inject: word-timings + target comp"
        + (" + bpm" if mode in _MODE_USES_BPM else ""),
        f"$.global.__BLAST_SUBS_JSON = {payload};",
        f"$.global.__BLAST_TARGET_COMP = {target_js};",
    ]
    if mode in _MODE_USES_BPM and bpm is not None and float(bpm) > 0.0:
        prelude_lines.append(f"$.global.__BLAST_BPM = {float(bpm)!r};")
    rgb = hex_to_rgb01(fill_hex) if fill_hex else None
    if rgb is not None:
        prelude_lines.append(f"$.global.__BLAST_FILL = [{rgb[0]!r}, {rgb[1]!r}, {rgb[2]!r}];")
    prelude = "\n".join(prelude_lines)

    return prelude + "\n" + body
