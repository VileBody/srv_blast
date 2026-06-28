"""Builder for F3 «Эффект» overlay JSX blocks (mirror of f4_motion.overlay).

`build_overlay_jsx(...)` returns a self-contained ExtendScript snippet that
applies the chosen hook + transition + extra (+ sound + logo) on top of
`MAIN_COMP` (= "Comp 1"). The snippet is injected verbatim into the render
template (raw, not tojson) at the f3 hook point — after addFlashOnCuts(),
before project.save().

Design (prod form, NOT the dev run_job.jsx harness):
  * We do NOT ship a folder + manifest.json + job.json to the render node.
  * Instead we BUNDLE: read manifest.json (effect->script + sound + branding)
    and the chosen child .jsx files, then emit ONE block where run_job's glue
    (cut detection, drop sync, sound, logo) is reimplemented here and each
    child script is inlined verbatim inside its own IIFE (so its top-level
    `var`/`function` declarations stay local and don't collide via hoisting).
  * Params are passed to each child through the global `$.global.__BLAST`,
    exactly like run_job.jsx — child scripts already merge it into CONFIG.

Asset delivery = S3 media[] (like footage): the build worker picks a concrete
file per pool and passes its job-relative media path here (e.g.
"media/audio/camera_flash_01.wav"). The block resolves it to an absolute path
via the template-scope var `__APP_DIR`. Missing asset => that piece is skipped
(zero impact), never a hard fail.

No f3 selection => caller passes nothing => `_build_f3_overlay_js` returns ""
=> template injects empty string => zero impact on regular jobs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_F3_DIR = Path(__file__).resolve().parent
_MANIFEST_PATH = _F3_DIR / "manifest.json"

# place ref layer inside MAIN_COMP (the "Текст" precomp layer). Effects go below it.
_PLACE_REF = "Текст"

# Wired effect ids per group (mirror manifest.json). Used for request/env
# validation (orchestrator, tasks) and as the source for the bot's 3-step UI.
F3_HOOKS = ("hook_light", "shutter_effect", "flash_slow_shutter")
F3_TRANSITIONS = (
    "snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts", "layer_shake",
)
# NOTE: pixel_grain / warm_map removed from the selectable pool — they import a
# .aep at runtime (needs_aep) which isn't shipped to the render node and whose
# $.fileName path breaks once the script is inlined into render_full.jsx. The
# .jsx + manifest entries are kept for a future S3-delivery integration.
F3_EXTRAS = (
    "xerox", "analog_glitch", "neon_extract", "old_camera",
)


def _load_manifest() -> Dict[str, Any]:
    obj = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("effects"), list):
        raise RuntimeError(f"invalid f3 manifest: {_MANIFEST_PATH}")
    return obj


def _eff_by_id(manifest: Dict[str, Any], eff_id: str) -> Optional[Dict[str, Any]]:
    for e in manifest.get("effects", []):
        if isinstance(e, dict) and str(e.get("id")) == eff_id:
            return e
    return None


def _read_script(rel_script: str) -> str:
    p = (_F3_DIR / rel_script).resolve()
    if _F3_DIR not in p.parents:
        raise RuntimeError(f"f3 script escapes pipeline dir: {rel_script}")
    if not p.exists():
        raise FileNotFoundError(f"f3 script missing: {p}")
    return p.read_text(encoding="utf-8")


def _js(value: Any) -> str:
    """Serialize a Python value as a JS literal (json is valid ExtendScript)."""
    return json.dumps(value, ensure_ascii=False)


# JS prelude: helpers ported from run_job.jsx, prefixed __f3_ to avoid clashes
# with the render template globals. Operates on MAIN_COMP (template scope).
_JS_PRELUDE = r"""
  function __f3_findLayer(c, n){ for (var i=1;i<=c.numLayers;i++) if (c.layer(i).name===n) return c.layer(i); return null; }
  function __f3_detectCuts(comp){
    var cuts=[], i;
    for (i=1;i<=comp.numLayers;i++){ var L=comp.layer(i);
      var isF = (L.source && (L.source instanceof FootageItem) && L.hasVideo && !L.adjustmentLayer);
      if (isF) cuts.push(L.inPoint); }
    cuts.sort(function(a,b){return a-b;});
    var out=[], fr=comp.frameDuration;
    for (var k=0;k<cuts.length;k++){ if (!out.length || Math.abs(cuts[k]-out[out.length-1])>fr) out.push(cuts[k]); }
    return out;
  }
  function __f3_contentEnd(comp){ var wa=comp.workAreaStart+comp.workAreaDuration; return (wa>0 && wa<=comp.duration)?wa:comp.duration; }
  function __f3_hookDur(comp, drop, cuts, base, extend){
    if (!extend) return base;
    var endT=__f3_contentEnd(comp);
    if (extend==="to_end") return Math.max(base, endT-drop);
    var m=String(extend).match(/^after_drop:(\d+)$/);
    if (m){ var n=parseInt(m[1],10), after=[], fr=comp.frameDuration, i;
      for (i=0;i<cuts.length;i++){ if (cuts[i]>drop+fr) after.push(cuts[i]); }
      if (after.length>=n) return Math.max(base, after[n-1]-drop);
      return Math.max(base, endT-drop); }
    return base;
  }
"""


def build_overlay_jsx(
    *,
    hook: Optional[str] = None,
    transition: Optional[str] = None,
    extra: Optional[str] = None,
    extra_full: bool = False,
    hook_extend: Optional[str] = None,
    drop_time: float,
    assets: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the injectable F3 JSX block. Empty selection => "".

    assets (all optional, job-relative media paths under media/...):
      hook_sound, transition_sound, extra_sound, logo
    """
    if not (hook or transition or extra):
        return ""

    manifest = _load_manifest()
    assets = assets or {}

    try:
        drop = float(drop_time)
    except Exception as e:
        raise ValueError(f"invalid drop_time={drop_time!r}") from e
    if drop < 0.0:
        raise ValueError(f"drop_time must be >= 0 (got {drop!r})")

    # ---- resolve chosen effects against the manifest (no-fallback) ----
    h_eff = _eff_by_id(manifest, hook) if hook else None
    t_eff = _eff_by_id(manifest, transition) if transition else None
    e_eff = _eff_by_id(manifest, extra) if extra else None
    if hook and not h_eff:
        raise ValueError(f"unknown f3 hook id: {hook!r}")
    if transition and not t_eff:
        raise ValueError(f"unknown f3 transition id: {transition!r}")
    if extra and not e_eff:
        raise ValueError(f"unknown f3 extra id: {extra!r}")

    default_style = (manifest.get("branding") or {}).get("default_style", "stamp_flash")

    parts: list[str] = []
    parts.append("/* ===== F3 «Эффект» overlay (injected by build worker) ===== */")
    parts.append("(function(){")
    parts.append('  if (typeof MAIN_COMP === "undefined" || !MAIN_COMP) { return; }')
    parts.append("  var __f3_comp = MAIN_COMP;")
    parts.append("  var __f3_name = __f3_comp.name;")
    parts.append(f"  var __f3_drop = {_js(drop)};")
    parts.append(f'  var __f3_place = "below:{_PLACE_REF}";')
    parts.append(_JS_PRELUDE)
    parts.append("  var __f3_cuts = __f3_detectCuts(__f3_comp);")
    parts.append("  var __f3_used = [];")  # cut-times already given a sound (dedup)

    # reusable SFX runner (inlines sound.jsx once, callable many times)
    sound_src = _read_script("sound/sound.jsx")
    parts.append("  function __f3_sfx(p){ $.global.__BLAST = p; (function(){")
    parts.append(sound_src)
    parts.append("  })(); $.global.__BLAST = null; }")

    def _asset_path_js(slot: str) -> Optional[str]:
        rel = assets.get(slot)
        if not rel:
            return None
        rel = str(rel).strip().strip("/")
        if not rel:
            return None
        # absolute path on node = __APP_DIR + "/" + relpath
        return f'(String(__APP_DIR || "") + "/" + {_js(rel)})'

    # ---------------- HOOK ----------------
    if h_eff:
        base_dur = float(h_eff.get("default_duration") or 0.5)
        extend_js = _js(hook_extend) if (h_eff.get("extendable") and hook_extend) else "null"
        parts.append("  /* -- HOOK -- */")
        parts.append(f"  var __f3_hookDurV = __f3_hookDur(__f3_comp, __f3_drop, __f3_cuts, {_js(base_dur)}, {extend_js});")
        parts.append("  $.global.__BLAST = { targetCompName: __f3_name, dropTime: __f3_drop, duration: __f3_hookDurV, place: __f3_place, cuts: __f3_cuts };")
        parts.append("  (function(){")
        parts.append(_read_script(h_eff["script"]))
        parts.append("  })(); $.global.__BLAST = null;")

        # hook sound = the only drop sound (lightning / camera flash)
        snd = h_eff.get("sound") or {}
        sound_path_js = _asset_path_js("hook_sound")
        if sound_path_js:
            impact = snd.get("impact_at")
            impact_js = _js(float(impact)) if impact is not None else "null"
            parts.append(f"  __f3_sfx({{ targetCompName: __f3_name, dropTime: __f3_drop, soundFile: {sound_path_js}, impactAt: {impact_js} }});")

        # logo stamp (branding:true OR built_in)
        branding = h_eff.get("branding")
        logo_path_js = _asset_path_js("logo")
        if branding in (True, "built_in") and logo_path_js:
            style = h_eff.get("branding_style") or default_style
            parts.append("  $.global.__BLAST = { targetCompName: __f3_name, dropTime: __f3_drop, logoPath: " + logo_path_js + ", style: " + _js(style) + " };")
            parts.append("  (function(){")
            parts.append(_read_script(manifest["branding"]["script"]))
            parts.append("  })(); $.global.__BLAST = null;")

    # ---------------- TRANSITION ----------------
    if t_eff:
        t_dur = float(t_eff.get("default_duration") or 0.067)
        parts.append("  /* -- TRANSITION -- */")
        parts.append(f"  $.global.__BLAST = {{ targetCompName: __f3_name, dropTime: __f3_drop, duration: {_js(t_dur)}, place: __f3_place, cuts: __f3_cuts }};")
        parts.append("  (function(){")
        parts.append(_read_script(t_eff["script"]))
        parts.append("  })(); $.global.__BLAST = null;")
        # transition sound: pre-drop cuts only, one per cut, dedup
        ts_path_js = _asset_path_js("transition_sound")
        if ts_path_js:
            parts.append(_cut_sounds_js(ts_path_js))

    # ---------------- EXTRA (grade) ----------------
    # Default: pre-drop only (0..drop). extra_full=True → null duration = whole
    # comp (the same "no-drop" path the script already handles), so the stylize
    # (e.g. xerox) runs over the ENTIRE video to bump uniqueness.
    if e_eff:
        _extra_dur_js = "null" if extra_full else "(__f3_drop>0?__f3_drop:null)"
        parts.append("  /* -- EXTRA -- */")
        parts.append(
            "  $.global.__BLAST = { targetCompName: __f3_name, dropTime: __f3_drop, "
            f"startTime: 0, duration: {_extra_dur_js}, place: __f3_place, cuts: __f3_cuts }};"
        )
        parts.append("  (function(){")
        parts.append(_read_script(e_eff["script"]))
        parts.append("  })(); $.global.__BLAST = null;")
        es_path_js = _asset_path_js("extra_sound")
        if es_path_js:
            parts.append(_cut_sounds_js(es_path_js))

    parts.append("})();")
    return "\n".join(parts)


def _cut_sounds_js(sound_path_js: str) -> str:
    """JS that plays `sound_path_js` on each cut strictly before the drop,
    one per cut, skipping cuts already sounded (dedup transition+extra)."""
    return (
        "  (function(){ var fr=__f3_comp.frameDuration, i, u;\n"
        "    for (i=0;i<__f3_cuts.length;i++){ var ct=__f3_cuts[i];\n"
        "      if (ct >= __f3_drop - fr) continue;\n"
        "      var dup=false; for (u=0;u<__f3_used.length;u++){ if (Math.abs(__f3_used[u]-ct)<=fr){ dup=true; break; } }\n"
        "      if (dup) continue;\n"
        "      __f3_sfx({ targetCompName: __f3_name, dropTime: ct, soundFile: " + sound_path_js + ", impactAt: null, maxDuration: 1.5 });\n"
        "      __f3_used.push(ct);\n"
        "    } })();"
    )
