"""Builder for F2 «Объект» packaged-combo overlay JSX.

`build_overlay_jsx(...)` returns a self-contained ExtendScript snippet injected
into the render template at the f2 hook point (alongside f3/f4 overlays). The
combo wires three independent mechanisms in one block:

  1. PRE-DROP cuts   → chosen shape script (one of 5) anchored so its FX lands
                       on each cut (`startTime = cut - T_FX_OFFSET`).
  2. DROP            → F3 `hook_light` (rebuild_light.jsx) fired with dropTime.
  3. POST-DROP cuts  → seeded-random pick from the full F3 transition pool;
                       each transition is invoked ONCE with its assigned cut
                       subset (one transition can own multiple cuts).

Design choices (locked in by product req):
  * User picks ONLY the shape (5 options). hook_light and post-drop random
    are forced — F2 is a packaged combo, not a 3-step picker like F3.
  * Random pool = all 6 F3 transitions. Layer_shake is per-clip (not per-cut)
    and ignores its `cuts` param — if randomly assigned to any post-drop cut,
    it's invoked ONCE globally on the comp. The other 5 honor `cuts`.
  * Seeding: env F2_SEED (orchestrator passes job seed for reproducibility);
    omitted → derived from job id. Same input → same shape sequence.
  * Shape position = dump-default (`layerPosDefault` per shape script) — no
    `shapeCenter` override.

No f2 selection → caller passes nothing → `_build_f2_overlay_js` returns ""
→ template injects empty string → zero impact on regular jobs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

_F2_DIR = Path(__file__).resolve().parent
_F3_DIR = _F2_DIR.parent / "f3_effect"

# place ref layer inside MAIN_COMP (the "Текст" precomp layer). Effects go below it.
_PLACE_REF = "Текст"

# Wired shape ids (= filenames under shapes/). Mirror to the bot picker.
F2_SHAPES = ("rhomb", "square", "star1", "star2", "elipse")

# Post-drop random transition pool = all 6 F3 transitions.
# Mirror to mlcore.hooks.f3_effect.overlay.F3_TRANSITIONS — keep in sync.
F2_POST_DROP_TRANSITION_POOL = (
    "snap_wipe",
    "minimax",
    "invert_flash",
    "extract_flash",
    "flash_on_cuts",
    "layer_shake",
)

# F3 transition script paths (relative to f3_effect/). Single source of truth =
# f3_effect/manifest.json. We hardcode the same paths here to avoid loading the
# manifest just to resolve names — F3_TRANSITIONS list is small and frozen.
_F3_TRANSITION_SCRIPTS = {
    "snap_wipe": "transitions/snap_wipe.jsx",
    "minimax": "transitions/minimax.jsx",
    "invert_flash": "transitions/invert_flash.jsx",
    "extract_flash": "transitions/extract_flash.jsx",
    "flash_on_cuts": "transitions/flash_on_cuts.jsx",
    "layer_shake": "transitions/layer_shake.jsx",
}
_F3_HOOK_LIGHT_SCRIPT = "hooks/rebuild_light.jsx"

# Anchor offset inside the shape script: FX (snap wipe + minimax flash) fires
# at `tBase + T_FX_OFFSET`. To land FX ON a cut, the orchestrator sets
# `startTime = cut - T_FX_OFFSET`. Keep this in sync with the shape jsx files.
_SHAPE_T_FX_OFFSET = 0.43376710043377

# layer_shake is per-clip (ignores `cuts`); if randomly assigned to any
# post-drop cut, invoke it ONCE globally instead of grouped-by-cuts.
_GLOBAL_TRANSITIONS = frozenset({"layer_shake"})


def _read_shape_script(shape: str) -> str:
    if shape not in F2_SHAPES:
        raise ValueError(f"unknown F2 shape {shape!r}; allowed={list(F2_SHAPES)}")
    p = (_F2_DIR / "shapes" / f"{shape}.jsx").resolve()
    if _F2_DIR not in p.parents:
        raise RuntimeError(f"f2 shape escapes pipeline dir: {p}")
    if not p.exists():
        raise FileNotFoundError(f"f2 shape script missing: {p}")
    return p.read_text(encoding="utf-8")


def _read_f3_script(rel: str) -> str:
    p = (_F3_DIR / rel).resolve()
    if _F3_DIR not in p.parents:
        raise RuntimeError(f"f3 script escapes f3 dir: {p}")
    if not p.exists():
        raise FileNotFoundError(f"f3 script missing: {p}")
    return p.read_text(encoding="utf-8")


def _js(value: Any) -> str:
    """Serialize a Python value as a JS literal (json is valid ExtendScript)."""
    return json.dumps(value, ensure_ascii=False)


# JS prelude for F2: cut detection + seeded RNG. __f2_-prefixed to avoid name
# clashes with the f3 prelude (both can in theory be injected, though in the
# current bot flow they are mutually exclusive hook categories).
_JS_PRELUDE = r"""
  function __f2_findLayer(c, n){ for (var i=1;i<=c.numLayers;i++) if (c.layer(i).name===n) return c.layer(i); return null; }
  function __f2_detectCuts(comp){
    var cuts=[], i;
    for (i=1;i<=comp.numLayers;i++){ var L=comp.layer(i);
      var isF = (L.source && (L.source instanceof FootageItem) && L.hasVideo && !L.adjustmentLayer);
      if (isF) cuts.push(L.inPoint); }
    cuts.sort(function(a,b){return a-b;});
    var out=[], fr=comp.frameDuration;
    for (var k=0;k<cuts.length;k++){ if (!out.length || Math.abs(cuts[k]-out[out.length-1])>fr) out.push(cuts[k]); }
    return out;
  }
  // ES3-safe 32-bit imul: AE/ExtendScript has no Math.imul (it's ES3), so the
  // mulberry32 below must not rely on it — otherwise the whole overlay block
  // throws "Math.imul is not defined" and silently fails to inject in headless
  // aerender (observed: F5 combo dropped, no hook_light / no post-drop FX).
  function __f2_imul(a, b){
    a = a >>> 0; b = b >>> 0;
    var ah = (a >>> 16) & 0xffff, al = a & 0xffff;
    var bh = (b >>> 16) & 0xffff, bl = b & 0xffff;
    return ((al * bl) + ((((ah * bl + al * bh) & 0xffff) << 16) >>> 0)) | 0;
  }
  // mulberry32 seeded PRNG — deterministic per (job seed, cut count).
  function __f2_rng(seed){
    var t = (seed >>> 0) || 1;
    return function(){
      t = (t + 0x6D2B79F5) >>> 0;
      var x = t;
      x = __f2_imul(x ^ (x >>> 15), x | 1);
      x ^= x + __f2_imul(x ^ (x >>> 7), x | 61);
      return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
    };
  }
"""


def _shape_fill_rgba(hex_str: str) -> Optional[list[float]]:
    """'#RRGGBB' → [r, g, b, 1.0] floats 0..1 (AE shape fill is RGBA)."""
    s = str(hex_str or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)] + [1.0]
    except ValueError:
        return None


def build_overlay_jsx(
    *,
    shape: Optional[str] = None,
    drop_time: float,
    seed: int,
    post_drop_pool: tuple[str, ...] = F2_POST_DROP_TRANSITION_POOL,
    shape_fill_hex: Optional[str] = None,
) -> str:
    """Return the injectable F2 combo JSX block.

    Args:
        shape: one of F2_SHAPES, or None to SKIP the pre-drop shape phase
            (used by F1 «Звук», whose pre-drop region is the user's audio, not a
            visual transition — the drop hook_light + post-drop random remain).
        drop_time: COMP-relative drop seconds (same convention as f3.drop_time).
        seed: 32-bit-ish int for the post-drop random assignment PRNG.
        post_drop_pool: pool of F3 transition ids to randomize from (default:
            all 6). Empty pool → post-drop section is a no-op.
    """
    if shape is not None and shape not in F2_SHAPES:
        raise ValueError(f"unknown F2 shape {shape!r}; allowed={list(F2_SHAPES)}")
    try:
        drop = float(drop_time)
    except Exception as e:
        raise ValueError(f"invalid drop_time={drop_time!r}") from e
    if not (drop > 0.0):
        raise ValueError(f"drop_time must be > 0 (got {drop!r})")

    seed_int = int(seed) & 0xFFFFFFFF

    pool = tuple(str(t).strip().lower() for t in post_drop_pool if str(t).strip())
    unknown = [t for t in pool if t not in _F3_TRANSITION_SCRIPTS]
    if unknown:
        raise ValueError(f"unknown F2 post-drop transitions: {unknown!r}")

    # ---- assemble JS ----
    parts: list[str] = []
    parts.append("/* ===== F2 «Объект» combo overlay (injected by build worker) ===== */")
    parts.append("(function(){")
    parts.append('  if (typeof MAIN_COMP === "undefined" || !MAIN_COMP) { return; }')
    parts.append("  var __f2_comp = MAIN_COMP;")
    parts.append("  var __f2_name = __f2_comp.name;")
    parts.append(f"  var __f2_drop = {_js(drop)};")
    parts.append(f'  var __f2_place = "below:{_PLACE_REF}";')
    parts.append(_JS_PRELUDE)
    parts.append("  var __f2_cuts = __f2_detectCuts(__f2_comp);")
    parts.append("  var __f2_fr = __f2_comp.frameDuration;")
    parts.append("  var __f2_pre = [], __f2_post = [], __f2_i;")
    parts.append("  for (__f2_i=0; __f2_i<__f2_cuts.length; __f2_i++){")
    parts.append("    var __f2_ct = __f2_cuts[__f2_i];")
    parts.append("    if (__f2_ct < __f2_drop - __f2_fr) __f2_pre.push(__f2_ct);")
    parts.append("    else if (__f2_ct > __f2_drop + __f2_fr) __f2_post.push(__f2_ct);")
    parts.append("  }")

    # ---------------- (1) PRE-DROP: chosen shape on the FIRST footage only ----------------
    # Rule: the object plays ONCE — on the first renderable pre-drop cut. Dense
    # pre-drop cutting otherwise stamps the shape on every clip → cluttered.
    # Skipped entirely when shape is None (F1 «Звук» reuse — no pre-drop visual).
    if shape is not None:
        shape_src = _read_shape_script(shape)
        # Custom shape color (F2 «Объект» customization): override the SHAPE.fill
        # RGBA in the shape body. None → keep the script's default.
        rgba = _shape_fill_rgba(shape_fill_hex) if shape_fill_hex else None
        if rgba is not None:
            shape_src = re.sub(
                r"fill:\s*\[[^\]]*\]",
                f"fill: [{rgba[0]!r}, {rgba[1]!r}, {rgba[2]!r}, 1]",
                shape_src,
                count=1,
            )
        parts.append("  /* -- (1) PRE-DROP shape transition (first footage only) -- */")
        parts.append(f"  var __f2_t_fx_offset = {_js(_SHAPE_T_FX_OFFSET)};")
        parts.append("  var __f2_first = -1;")
        parts.append("  for (__f2_i=0; __f2_i<__f2_pre.length; __f2_i++){")
        parts.append("    if ((__f2_pre[__f2_i] - __f2_t_fx_offset) >= 0){ __f2_first = __f2_i; break; }")
        parts.append("  }")
        parts.append("  if (__f2_first >= 0){")
        parts.append("    var __f2_startT = __f2_pre[__f2_first] - __f2_t_fx_offset;")
        parts.append("    $.global.__BLAST = { targetCompName: __f2_name, placeRef: " + _js(_PLACE_REF) + ", startTime: __f2_startT };")
        parts.append("    (function(){")
        parts.append(shape_src)
        parts.append("    })(); $.global.__BLAST = null;")
        parts.append("  }")

    # ---------------- (2) DROP: F3 hook_light ----------------
    hook_light_src = _read_f3_script(_F3_HOOK_LIGHT_SCRIPT)
    parts.append("  /* -- (2) DROP: F3 hook_light -- */")
    parts.append("  $.global.__BLAST = { targetCompName: __f2_name, dropTime: __f2_drop, place: __f2_place, cuts: __f2_cuts };")
    parts.append("  (function(){")
    parts.append(hook_light_src)
    parts.append("  })(); $.global.__BLAST = null;")

    # ---------------- (3) POST-DROP: seeded-random transition per cut ----------------
    parts.append("  /* -- (3) POST-DROP: seeded-random transition per cut -- */")
    parts.append(f"  var __f2_seed = {_js(seed_int)};")
    parts.append(f"  var __f2_pool = {_js(list(pool))};")
    parts.append("  if (__f2_pool.length > 0 && __f2_post.length > 0){")
    parts.append("    var __f2_rand = __f2_rng(__f2_seed);")
    parts.append("    var __f2_groups = {};")  # tid -> [cuts]
    parts.append("    for (__f2_i=0; __f2_i<__f2_post.length; __f2_i++){")
    parts.append("      var __f2_tid = __f2_pool[Math.floor(__f2_rand() * __f2_pool.length)];")
    parts.append("      if (!__f2_groups[__f2_tid]) __f2_groups[__f2_tid] = [];")
    parts.append("      __f2_groups[__f2_tid].push(__f2_post[__f2_i]);")
    parts.append("    }")
    # Emit one if-branch per transition id: matches the static set of scripts
    # we have. JS picks the branches whose group is non-empty.
    parts.append('    var __f2_global_set = ' + _js(sorted(_GLOBAL_TRANSITIONS)) + ';')
    for tid, rel in _F3_TRANSITION_SCRIPTS.items():
        if tid not in pool:
            continue  # not in pool → no chance of being assigned
        src = _read_f3_script(rel)
        is_global = tid in _GLOBAL_TRANSITIONS
        parts.append(f"    if (__f2_groups[{_js(tid)}] && __f2_groups[{_js(tid)}].length > 0){{")
        if is_global:
            # per-clip transitions (layer_shake): ignore the cuts assignment, invoke once globally.
            parts.append("      $.global.__BLAST = { targetCompName: __f2_name, dropTime: __f2_drop, place: __f2_place, cuts: __f2_cuts };")
        else:
            # per-cut transitions: pass only this group's cuts subset.
            parts.append(f"      $.global.__BLAST = {{ targetCompName: __f2_name, dropTime: __f2_drop, place: __f2_place, cuts: __f2_groups[{_js(tid)}] }};")
        parts.append("      (function(){")
        parts.append(src)
        parts.append("      })(); $.global.__BLAST = null;")
        parts.append("    }")
    parts.append("  }")

    parts.append("})();")
    return "\n".join(parts)
