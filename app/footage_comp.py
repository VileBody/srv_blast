from __future__ import annotations

import json
import os
import logging
import re
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.types import KeyframeData, KeyframeEase, LayerBlueprint, PropertyData

LOGGER = logging.getLogger("app.footage_comp")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    val = str(raw).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _footage_shake_position_expression() -> str:
    # Deterministic intro/outro shake on footage transform position.
    return (
        "var intro=0.63;\n"
        "var outro=0.63;\n"
        "var amp=22.0;\n"
        "var freq=3.6;\n"
        "var t=time-inPoint;\n"
        "var dur=Math.max(thisComp.frameDuration,outPoint-inPoint);\n"
        "var base=value;\n"
        "var z=(base.length>2)?base[2]:0;\n"
        "var k=0.0;\n"
        "if (t>=0 && t<intro){\n"
        "  var p=Math.min(1,Math.max(0,t/intro));\n"
        "  k=Math.sin(p*Math.PI)*Math.exp(-2.4*p);\n"
        "} else if (t>dur-outro && t<=dur){\n"
        "  var r=Math.min(1,Math.max(0,(dur-t)/outro));\n"
        "  k=Math.sin(r*Math.PI)*Math.exp(-2.4*r);\n"
        "}\n"
        "var x=amp*k*Math.sin(2*Math.PI*freq*t);\n"
        "var y=amp*0.68*k*Math.cos(2*Math.PI*(freq*0.82)*t);\n"
        "[base[0]+x,base[1]+y,z];"
    )


def _looks_like_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


def _as_pos_float(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    if x <= 0:
        return None
    return x


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


_WIN_BAD_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_WS_RE = re.compile(r"\s+")


def _sanitize_media_file_name(name: str) -> str:
    """
    Normalize media filename to a Windows-safe deterministic variant while
    preserving extension when possible.
    """
    raw = str(name or "")
    s = raw.strip()
    if not s:
        return "media.bin"

    # Windows-forbidden chars and path separators.
    s = _WIN_BAD_CHARS_RE.sub("_", s)

    base, ext = os.path.splitext(s)
    base = _MULTI_WS_RE.sub(" ", base).rstrip(" .")
    ext = _MULTI_WS_RE.sub("", ext).strip()

    if not base:
        base = "media"
    out = f"{base}{ext}"
    out = out.strip().rstrip(".")
    return out or "media.bin"


def _resolve_safe_media_name(
    *,
    original: str,
    used_names: set[str],
    by_original: Dict[str, str],
) -> str:
    # Repeated same source file should keep the same safe name.
    existing = by_original.get(original)
    if existing:
        return existing

    candidate = _sanitize_media_file_name(original)
    resolved = candidate
    if resolved in used_names:
        stem, ext = os.path.splitext(candidate)
        digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:8]
        resolved = f"{stem}__{digest}{ext}"
        idx = 2
        while resolved in used_names:
            resolved = f"{stem}__{digest}_{idx}{ext}"
            idx += 1

    used_names.add(resolved)
    by_original[original] = resolved
    return resolved


def resolve_text_duration_sec(
    *,
    composition_dur: Any = None,
    footage_cfg: Dict[str, Any],
    layers_cfg: List[Dict[str, Any]],
) -> float:
    """
    Resolve factual text/main composition duration.
    Priority:
      1) full_edit_config composition.dur (if valid)
      2) explicit text_dur_hint (if valid)
      3) max out_point from layers[]
      4) fail fast with explicit error
    """
    comp_dur = _as_pos_float(composition_dur)
    if comp_dur is not None:
        return float(comp_dur)

    hint = _as_pos_float(footage_cfg.get("text_dur_hint"))
    if hint is not None:
        LOGGER.warning(
            "comp_duration_fallback used=text_dur_hint value=%s reason=missing_or_invalid_composition_dur",
            float(hint),
        )
        return float(hint)

    max_out = 0.0
    for it in layers_cfg:
        if not isinstance(it, dict):
            continue
        out_point = _as_pos_float(it.get("out_point"))
        if out_point is None:
            continue
        if out_point > max_out:
            max_out = float(out_point)

    if max_out > 0:
        LOGGER.warning(
            "comp_duration_fallback used=max_out_point value=%s reason=missing_or_invalid_composition_dur_and_text_dur_hint",
            float(max_out),
        )
        return float(max_out)

    raise RuntimeError(
        "Unable to resolve composition duration: "
        "missing/invalid full_edit composition.dur, text_dur_hint, and layers[*].out_point"
    )


def _resolve_text_duration_sec(*, footage_cfg: Dict[str, Any], layers_cfg: List[Dict[str, Any]]) -> float:
    # Backward-compatible alias for internal callers/tests.
    return resolve_text_duration_sec(footage_cfg=footage_cfg, layers_cfg=layers_cfg)


# -----------------------------
# Adj16 dump -> effects
# -----------------------------
def _iter_props(props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    stack = list(props or [])
    while stack:
        p = stack.pop(0)
        out.append(p)
        ch = p.get("children") or []
        stack = ch + stack
    return out


def _eases(arr: Any) -> List[KeyframeEase]:
    out: List[KeyframeEase] = []
    for it in (arr or []):
        try:
            out.append(KeyframeEase(speed=float(it.get("speed", 0.0)), influence=float(it.get("influence", 0.0))))
        except Exception:
            pass
    return out


def _key_to_kf(k: Dict[str, Any]) -> KeyframeData:
    return KeyframeData(
        t=float(k["time"]),
        v=k["value"],
        iit=str(k.get("inInterpolationType", "6613")),
        oit=str(k.get("outInterpolationType", "6613")),
        ease_in=_eases(k.get("inTemporalEase")),
        ease_out=_eases(k.get("outTemporalEase")),
    )


def _extract_effects_from_adjustment_dump(dump: Dict[str, Any]) -> Dict[str, Dict[str, PropertyData]]:
    props = dump.get("topProperties") or []
    effect_parades = [
        p for p in _iter_props(props)
        if p.get("matchName") == "ADBE Effect Parade" and (p.get("children") is not None)
    ]
    if not effect_parades:
        return {}

    parade = effect_parades[0]
    effects_out: Dict[str, Dict[str, PropertyData]] = {}

    for eff_i, eff in enumerate((parade.get("children") or [])):
        eff_match = eff.get("matchName")
        if not eff_match:
            continue

        # IMPORTANT:
        # Some layers (notably "Adjustment Layer 16") contain duplicate effect matchNames
        # (e.g. two "ADBE Geometry2" instances: "Transform" and "Transform 2").
        # A plain dict keyed by matchName would overwrite earlier instances and change the look.
        #
        # We keep deterministic order by prefixing with the original effect index.
        # JSX template strips the prefix when calling fxParade.addProperty().
        eff_key = f"{eff_i:04d}:{eff_match}"

        params: Dict[str, PropertyData] = {}
        p_idx = 0

        for p in (eff.get("children") or []):
            pm = p.get("matchName")
            if not pm:
                continue

            # Layer-index dropdowns (PropertyValueType.LAYER_INDEX) are not portable across comps.
            # The dump may refer to a layer number that does not exist in the generated comp,
            # causing AE setValue() errors and visual artifacts (black bars).
            if str(p.get("propertyValueType") or "") == "6421":
                params[f"{p_idx:04d}"] = PropertyData(match_name=pm, value=0)
                p_idx += 1
                continue

            keys = p.get("keys") or []
            if keys:
                kfs = [_key_to_kf(k) for k in keys]
                if any(kf.v is None for kf in kfs):
                    continue
                params[f"{p_idx:04d}"] = PropertyData(match_name=pm, keyframes=kfs)
                p_idx += 1
                continue

            expr = p.get("expression") if p.get("expressionEnabled") else None
            val = p.get("value")
            if val is None and not expr:
                continue

            params[f"{p_idx:04d}"] = PropertyData(match_name=pm, value=val, expression=expr)
            p_idx += 1

        if params:
            effects_out[eff_key] = params

    return effects_out


# -----------------------------
# Adj16 pin-edges warp + recompute ease speed
# -----------------------------
def _warp_time_pin_edges(
    t: float,
    *,
    base_in: float,
    base_out: float,
    new_in: float,
    new_out: float,
    head_keep: float,
    tail_keep: float,
) -> float:
    base_in = float(base_in)
    base_out = float(base_out)
    new_in = float(new_in)
    new_out = float(new_out)
    head_keep = max(0.0, float(head_keep))
    tail_keep = max(0.0, float(tail_keep))

    head = float(t) - base_in
    tail = base_out - float(t)

    if head <= head_keep + 1e-9:
        return new_in + head
    if tail <= tail_keep + 1e-9:
        return new_out - tail

    base_mid_in = base_in + head_keep
    base_mid_out = base_out - tail_keep
    new_mid_in = new_in + head_keep
    new_mid_out = new_out - tail_keep

    denom = (base_mid_out - base_mid_in)
    if denom <= 1e-9:
        return max(new_mid_in, min(new_mid_out, new_in + head))

    rel = (float(t) - base_mid_in) / denom
    return new_mid_in + rel * (new_mid_out - new_mid_in)


def _dv(a: Any, b: Any) -> float:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(b) - float(a))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        s = 0.0
        for i in range(len(a)):
            try:
                d = float(b[i]) - float(a[i])
                s += d * d
            except Exception:
                pass
        return s ** 0.5
    return 0.0


def _pick_influence(k: KeyframeData) -> float:
    for e in (k.ease_in or []) + (k.ease_out or []):
        try:
            return float(e.influence)
        except Exception:
            pass
    return 16.666666667


def _recompute_ease_speed_by_dvdt(kfs: List[KeyframeData]) -> List[KeyframeData]:
    if not kfs:
        return []

    ks = sorted(kfs, key=lambda x: float(x.t))
    out = [KeyframeData(t=float(k.t), v=k.v, iit=k.iit, oit=k.oit, ease_in=[], ease_out=[]) for k in ks]

    for i in range(len(out) - 1):
        a = out[i]
        b = out[i + 1]
        dt = float(b.t) - float(a.t)
        dv = _dv(a.v, b.v)
        sp = 0.0 if dt <= 1e-9 else (dv / dt)
        inf = _pick_influence(ks[i])
        a.ease_out = [KeyframeEase(speed=sp, influence=inf)]
        b.ease_in = [KeyframeEase(speed=sp, influence=inf)]

    return out


_ADJ16_RULES: Dict[str, Dict[str, float]] = {
    "BCC6LensBlur-9961714": {"head": 0.625625625626, "tail": 0.625625625626},
    "ADBE Geometry2-0007": {"head": 0.792459125792, "tail": 0.750750750751},
    "ADBE Geometry2-0002": {"head": 0.125125125125, "tail": 0.0},
    "ADBE Geometry2-0003": {"head": 0.709042375709, "tail": 0.750750750751},
    "S_BlurMotion-0051": {"head": 0.250250250250, "tail": 0.291958625292},
}

def _adj16_amplitude_mult() -> float:
    raw = (os.environ.get("ADJ16_AMPLITUDE_MULT") or "").strip()
    if not raw:
        return 0.85
    try:
        v = float(raw)
    except Exception:
        return 0.85
    return max(0.0, min(1.0, v))


_ADJ16_AMP_MULT = _adj16_amplitude_mult()
_ADJ16_AMP_NEUTRAL: Dict[str, float] = {
    "ADBE Geometry2-0003": 100.0,  # scale-like
    "ADBE Geometry2-0007": 0.0,    # rotation
}

def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_pos_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
    except Exception:
        return float(default)
    return v if v > 0.0 else float(default)


def _env_nonneg_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
    except Exception:
        return float(default)
    return v if v >= 0.0 else float(default)


def _env_overlay_opacity_percent(name: str = "OVERLAY_OPACITY", default: float = 15.0) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        v = float(default)
    else:
        try:
            v = float(raw)
        except Exception as e:
            raise RuntimeError(f"Invalid {name}: {raw!r}") from e
    if v < 0.0 or v > 100.0:
        raise RuntimeError(f"{name} must be in [0..100], got {v}")
    return float(v)


_ADJ16_FIT_TO_COMP1 = _env_bool("ADJ16_FIT_TO_COMP1", True)
_ADJ16_REF_W = _env_pos_float("ADJ16_REF_W", 1080.0)
_ADJ16_REF_H = _env_pos_float("ADJ16_REF_H", 1080.0)


def _looks_like_coord_pair(v: Any, *, ref_h: float) -> bool:
    if not (isinstance(v, list) and len(v) == 2):
        return False
    if not (isinstance(v[0], (int, float)) and isinstance(v[1], (int, float))):
        return False
    # Skip normalized pairs (0..1-ish). Keep comp-space-like values.
    if abs(float(v[0])) <= 2.0 and abs(float(v[1])) <= 2.0:
        return False
    # Heuristic: Y should be in visible comp-space range, not tiny utility values.
    return abs(float(v[1])) >= (float(ref_h) * 0.30)


def _scale_pair(v: List[float], *, sx: float, sy: float) -> List[float]:
    return [float(v[0]) * float(sx), float(v[1]) * float(sy)]


def _resize_effects_adj16_to_comp(
    effects: Dict[str, Dict[str, PropertyData]],
    *,
    comp_w: int,
    comp_h: int,
    ref_w: float,
    ref_h: float,
) -> Tuple[Dict[str, Dict[str, PropertyData]], Dict[str, int]]:
    sx = float(comp_w) / float(ref_w)
    sy = float(comp_h) / float(ref_h)
    if abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9:
        return effects, {"scaled_values": 0, "scaled_keyframes": 0}

    out: Dict[str, Dict[str, PropertyData]] = {}
    scaled_values = 0
    scaled_keyframes = 0
    for eff_k, params in effects.items():
        out_params: Dict[str, PropertyData] = {}
        for pk, pd in params.items():
            val = pd.value
            if _looks_like_coord_pair(val, ref_h=ref_h):
                val = _scale_pair(val, sx=sx, sy=sy)
                scaled_values += 1

            kfs: List[KeyframeData] = []
            for k in (pd.keyframes or []):
                kv = k.v
                if _looks_like_coord_pair(kv, ref_h=ref_h):
                    kv = _scale_pair(kv, sx=sx, sy=sy)
                    scaled_keyframes += 1
                kfs.append(
                    KeyframeData(
                        t=float(k.t),
                        v=kv,
                        iit=k.iit,
                        oit=k.oit,
                        ease_in=list(k.ease_in or []),
                        ease_out=list(k.ease_out or []),
                    )
                )
            out_params[pk] = PropertyData(match_name=pd.match_name, value=val, keyframes=kfs, expression=pd.expression)
        out[eff_k] = out_params
    return out, {"scaled_values": scaled_values, "scaled_keyframes": scaled_keyframes}


def _dampen_adj16_value(match_name: str, value: Any) -> Any:
    neutral = _ADJ16_AMP_NEUTRAL.get(match_name)
    if neutral is None:
        return value
    if not isinstance(value, (int, float)):
        return value

    out = float(neutral) + (float(value) - float(neutral)) * float(_ADJ16_AMP_MULT)
    if match_name == "ADBE Geometry2-0003":
        return max(0.01, out)
    if match_name == "ADBE Geometry2-0007":
        return max(-360.0, min(360.0, out))
    return out


def _warp_propertydata_adj16(
    pd: PropertyData,
    *,
    base_in: float,
    base_out: float,
    seg_in: float,
    seg_out: float,
) -> PropertyData:
    if not pd.keyframes:
        return pd

    rule = _ADJ16_RULES.get(pd.match_name)
    if not rule:
        return pd

    head_keep = float(rule["head"])
    tail_keep = float(rule["tail"])

    warped: List[KeyframeData] = []
    for k in pd.keyframes:
        warped.append(
            KeyframeData(
                t=_warp_time_pin_edges(
                    float(k.t),
                    base_in=base_in,
                    base_out=base_out,
                    new_in=seg_in,
                    new_out=seg_out,
                    head_keep=head_keep,
                    tail_keep=tail_keep,
                ),
                v=k.v,
                iit=k.iit,
                oit=k.oit,
                ease_in=list(k.ease_in or []),
                ease_out=list(k.ease_out or []),
            )
        )

    # Soften aggressive transition spikes on scale/rotation while preserving timing shape.
    for k in warped:
        k.v = _dampen_adj16_value(pd.match_name, k.v)

    warped = _recompute_ease_speed_by_dvdt(warped)
    return PropertyData(match_name=pd.match_name, keyframes=warped, expression=pd.expression)


def _warp_effects_adj16_pin_edges(
    effects: Dict[str, Dict[str, PropertyData]],
    *,
    base_in: float,
    base_out: float,
    seg_in: float,
    seg_out: float,
) -> Dict[str, Dict[str, PropertyData]]:
    out: Dict[str, Dict[str, PropertyData]] = {}
    for eff_k, params in effects.items():
        out_params: Dict[str, PropertyData] = {}
        for pk, pd in params.items():
            out_params[pk] = _warp_propertydata_adj16(pd, base_in=base_in, base_out=base_out, seg_in=seg_in, seg_out=seg_out)
        out[eff_k] = out_params
    return out


# -----------------------------
# Footage fit: COVER (scale-to-fill)
# -----------------------------
def _compute_cover_transform(
    comp_w: int,
    comp_h: int,
    src_w: int,
    src_h: int,
) -> Tuple[List[float], List[float], List[float]]:
    """
    COVER (scale-to-fill) with a small overscan to prevent black edges when
    adjustment layers apply blur/warp/motion/rotation (sampling outside frame).

    Example:
      src 720x1280 into comp 1080x1080
      pure cover = 150%
      with overscan 1.0166666667 -> 152.5% (matches your reference dump)
    """
    # Small overscan factor (tune if needed)
    OVERSCAN: float = 1.0166666667

    s_cover = max(float(comp_w) / float(src_w), float(comp_h) / float(src_h))
    s = s_cover * float(OVERSCAN)

    scale = [s * 100.0, s * 100.0, 100.0]
    anchor = [src_w / 2.0, src_h / 2.0, 0.0]
    pos = [comp_w / 2.0, comp_h / 2.0, 0.0]
    return anchor, pos, scale



# -----------------------------
# Builders
# -----------------------------
def _footage_bp(
    *,
    it: Dict[str, Any],
    z_index: int,
    comp_w: int,
    comp_h: int,
    apply_shake: bool = True,
) -> LayerBlueprint:
    # audio OFF hard
    audio_enabled = False

    bp = LayerBlueprint(
        name=str(it["name"]),
        type="footage",
        in_point=float(it["in_point"]),
        out_point=float(it["out_point"]),
        z_index=int(z_index),
    )
    bp.text_data["layer_meta"] = {
        "comp_name_target": str(it.get("target_comp") or "Comp 1"),
        "startTime": float(it["start_time"]),
        "enabled": bool(it.get("enabled", True)),
        "audioEnabled": bool(audio_enabled),
        "motionBlur": False,
        "collapseTransformation": False,
        "blendingModeCode": "5212",
    }

    file_name = str(it.get("_safe_file_name") or it["file_name"])
    raw_path = str(it.get("file_path") or "")
    if _looks_like_url(raw_path):
        # RELocatable: keep remote_url, clear file_path for JSX (will be resolved by assets_resolve.json on Win)
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": "", "remote_url": raw_path}
    else:
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": raw_path}

    # Always fill the target comp (cover), regardless of incoming fit_mode.
    # This prevents black bars when source aspect differs from 9:16.
    fit_mode = "cover"
    sw = it.get("src_w")
    sh = it.get("src_h")
    if sw is not None and sh is not None:
        sw_i = int(sw)
        sh_i = int(sh)
        anchor, pos, scale = _compute_cover_transform(comp_w, comp_h, sw_i, sh_i)
        bp.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=anchor)
        shake_on = bool(apply_shake) and _env_bool("FOOTAGE_SHAKE_ENABLED", True)
        if shake_on:
            bp.props["tf_position"] = PropertyData(
                "ADBE Position",
                value=pos,
                expression=_footage_shake_position_expression(),
            )
        else:
            bp.props["tf_position"] = PropertyData("ADBE Position", value=pos)
        bp.props["tf_scale"] = PropertyData("ADBE Scale", value=scale)
        bp.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=0)
        bp.props["tf_opacity"] = PropertyData("ADBE Opacity", value=100)

    return bp


def _overlay_bp(
    *,
    it: Dict[str, Any],
    z_index: int,
    comp_w: int,
    comp_h: int,
    opacity_percent: float,
) -> LayerBlueprint:
    bp = _footage_bp(
        it=it,
        z_index=int(z_index),
        comp_w=comp_w,
        comp_h=comp_h,
        apply_shake=False,
    )

    # Force robust "cover" on overlay using actual imported footage dimensions.
    # This avoids reliance on inventory metadata that may be missing/wrong for S3 prefix overlays.
    bp.props["tf_anchor"] = PropertyData(
        "ADBE Anchor Point",
        value=[0.0, 0.0, 0.0],
        expression=(
            "var sw=(thisLayer.source&&thisLayer.source.width)?thisLayer.source.width:0;\n"
            "var sh=(thisLayer.source&&thisLayer.source.height)?thisLayer.source.height:0;\n"
            "[sw/2,sh/2,0];"
        ),
    )
    bp.props["tf_position"] = PropertyData(
        "ADBE Position",
        value=[float(comp_w) / 2.0, float(comp_h) / 2.0, 0.0],
        expression="[thisComp.width/2,thisComp.height/2,0];",
    )
    bp.props["tf_scale"] = PropertyData(
        "ADBE Scale",
        value=[100.0, 100.0, 100.0],
        expression=(
            "var sw=(thisLayer.source&&thisLayer.source.width)?thisLayer.source.width:0;\n"
            "var sh=(thisLayer.source&&thisLayer.source.height)?thisLayer.source.height:0;\n"
            "if (sw<=0 || sh<=0) value;\n"
            "else { var s=Math.max(thisComp.width/sw,thisComp.height/sh)*100; [s,s,100]; }"
        ),
    )
    bp.props["tf_opacity"] = PropertyData("ADBE Opacity", value=float(opacity_percent))
    bp.text_data["layer_meta"]["isOverlay"] = True
    try:
        dur_hint = _as_pos_float(it.get("duration_sec"))
    except Exception:
        dur_hint = None
    if dur_hint is not None:
        bp.text_data["layer_meta"]["overlayDurationSec"] = float(dur_hint)
    # Overlays should always blend additively against footage/text stack.
    bp.text_data["layer_meta"]["blendingModeCode"] = "screen"
    if bool(it.get("tile_in_ae")):
        bp.text_data["layer_meta"]["overlayTileInAe"] = True
        max_rep = it.get("tile_max_repeats")
        try:
            max_rep_i = int(max_rep)
        except Exception:
            max_rep_i = 100
        if max_rep_i <= 0:
            max_rep_i = 100
        bp.text_data["layer_meta"]["overlayTileMaxRepeats"] = int(max_rep_i)
    return bp


def _adjustment_bp(
    *,
    it: Dict[str, Any],
    z_index: int,
    effects: Dict[str, Dict[str, PropertyData]],
    comp_w: int,
    comp_h: int,
) -> LayerBlueprint:
    bp = LayerBlueprint(
        name=str(it["name"]),
        type="adjustment",
        in_point=float(it["in_point"]),
        out_point=float(it["out_point"]),
        z_index=int(z_index),
        adjustment_layer=True,
    )
    bp.text_data["layer_meta"] = {
        "comp_name_target": str(it.get("target_comp") or "Comp 1"),
        "startTime": float(it["start_time"]),
        "enabled": bool(it.get("enabled", True)),
        "adjustmentLayer": True,
        "motionBlur": False,
        "collapseTransformation": False,
        "blendingModeCode": "5212",
    }
    bp.effects = effects
    bp.source_rect = {"t": float(it["in_point"]), "left": 0, "top": 0, "width": float(comp_w), "height": float(comp_h)}
    return bp


def _audio_only_bp(*, it: Dict[str, Any], z_index: int) -> LayerBlueprint:
    bp = LayerBlueprint(
        name=str(it["name"]),
        type="footage",
        in_point=float(it["in_point"]),
        out_point=float(it["out_point"]),
        z_index=int(z_index),
    )
    bp.text_data["layer_meta"] = {
        "comp_name_target": str(it.get("target_comp") or "Comp 1"),
        "startTime": float(it["start_time"]),
        "enabled": bool(it.get("enabled", False)),
        "audioEnabled": bool(it.get("audio_enabled", True)),
        "motionBlur": False,
        "collapseTransformation": False,
        "blendingModeCode": "5212",
    }

    file_name = str(it.get("_safe_file_name") or it["file_name"])
    raw_path = str(it.get("file_path") or "")
    if _looks_like_url(raw_path):
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": "", "remote_url": raw_path}
    else:
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": raw_path}

    min_db = _as_float(os.environ.get("AUDIO_FADE_MIN_DB"))
    if min_db is None:
        min_db = -48.0
    bp.text_data["audio_envelope"] = {
        "fade_in_s": _env_nonneg_float("AUDIO_FADE_IN_S", 0.5),
        "fade_out_s": _env_nonneg_float("AUDIO_FADE_OUT_S", 0.5),
        "min_db": float(min_db),
    }

    return bp


# -----------------------------
# Public
# -----------------------------
def build_footage_layers(
    *,
    repo_root: Path,
    footage_cfg: Dict[str, Any],
    main_comp_name: str,
    text_comp_name: str,
    composition_dur: Any = None,
    precomp_z_index: int = 100,
    precomp_placement: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Desired final stack (top -> bottom):
      TEXT precomp
      overlays (optional)
      audio (optional, video off)
      Adjustments
      Footages
    """
    comp_w = int(footage_cfg.get("main_comp_w", 1080))
    comp_h = int(footage_cfg.get("main_comp_h", 1960))

    layers_cfg = list(footage_cfg.get("layers") or [])
    used_media_names: set[str] = set()
    safe_name_by_original: Dict[str, str] = {}
    for it in layers_cfg:
        if not isinstance(it, dict):
            continue
        if str(it.get("type")) not in {"footage", "overlay", "audio_only"}:
            continue
        original_name = str(it.get("file_name") or "").strip()
        if not original_name:
            continue
        it["_safe_file_name"] = _resolve_safe_media_name(
            original=original_name,
            used_names=used_media_names,
            by_original=safe_name_by_original,
        )

    text_dur_sec = resolve_text_duration_sec(
        composition_dur=composition_dur,
        footage_cfg=footage_cfg,
        layers_cfg=layers_cfg,
    )

    # --- base effects preset for Adj16 ---
    adj_preset = footage_cfg.get("adjustment_preset") or {}
    mode = str(adj_preset.get("time_warp_mode") or "pin_edges_v1")
    dump_file = str(adj_preset.get("dump_file") or "")
    dump_path = (repo_root / dump_file).resolve() if dump_file else None

    base_in, base_out, base_effects = 0.0, 1.0, {}
    if dump_path and dump_path.exists():
        dump = json.loads(dump_path.read_text(encoding="utf-8"))
        base_in = float(dump["meta"]["inPoint"])
        base_out = float(dump["meta"]["outPoint"])
        base_effects = _extract_effects_from_adjustment_dump(dump)
        if base_effects and _ADJ16_FIT_TO_COMP1:
            # Prefer the reference adjustment layer size from the dump itself (sourceRectAtInPoint),
            # fallback to env-based defaults if missing. This avoids hardcoding 1080x1080.
            ref_w = float(_ADJ16_REF_W)
            ref_h = float(_ADJ16_REF_H)
            try:
                sr = (dump.get("meta") or {}).get("sourceRectAtInPoint") or {}
                if isinstance(sr, dict):
                    rw = sr.get("width")
                    rh = sr.get("height")
                    if rw is not None and float(rw) > 0:
                        ref_w = float(rw)
                    if rh is not None and float(rh) > 0:
                        ref_h = float(rh)
            except Exception:
                pass

            base_effects, resize_stats = _resize_effects_adj16_to_comp(
                base_effects,
                comp_w=comp_w,
                comp_h=comp_h,
                ref_w=ref_w,
                ref_h=ref_h,
            )
            LOGGER.info(
                "adj16_resize_applied ref=%sx%s comp=%sx%s scaled_values=%s scaled_keyframes=%s",
                ref_w,
                ref_h,
                comp_w,
                comp_h,
                resize_stats["scaled_values"],
                resize_stats["scaled_keyframes"],
            )

    out: List[LayerBlueprint] = []

    # (1) TEXT precomp
    pre = LayerBlueprint(
        name=text_comp_name,
        type="precomp",
        in_point=0.0,
        out_point=float(text_dur_sec),
        z_index=1,
        comp_id=None,
    )
    pre.text_data["layer_meta"] = {
        "comp_name_target": main_comp_name,
        "startTime": 0.0,
        "enabled": True,
        "motionBlur": False,
        "collapseTransformation": False,
        "blendingModeCode": "5212",
    }
    pre.text_data["precomp_source"] = {"comp_name": text_comp_name}

    if precomp_placement:
        if precomp_placement.get("anchor") is not None:
            pre.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=precomp_placement["anchor"])
        if precomp_placement.get("position") is not None:
            pre.props["tf_position"] = PropertyData("ADBE Position", value=precomp_placement["position"])
        if precomp_placement.get("scale") is not None:
            pre.props["tf_scale"] = PropertyData("ADBE Scale", value=precomp_placement["scale"])
        if precomp_placement.get("rotationZ") is not None:
            pre.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=precomp_placement["rotationZ"])
        if precomp_placement.get("opacity") is not None:
            pre.props["tf_opacity"] = PropertyData("ADBE Opacity", value=precomp_placement["opacity"])

    out.append(pre)

    # (2) Overlay footage (between text and regular footage stack)
    overlay_items = [it for it in layers_cfg if str(it.get("type")) == "overlay"]
    if overlay_items:
        overlay_opacity = _env_overlay_opacity_percent()
        z_overlay = 5
        for it in overlay_items:
            out.append(
                _overlay_bp(
                    it=it,
                    z_index=z_overlay,
                    comp_w=comp_w,
                    comp_h=comp_h,
                    opacity_percent=overlay_opacity,
                )
            )
            z_overlay += 1

    # (3) Audio-only
    for it in layers_cfg:
        if str(it.get("type")) == "audio_only":
            out.append(_audio_only_bp(it=it, z_index=2))

    # (4) Adjustments
    adj_items = [it for it in layers_cfg if str(it.get("type")) == "adjustment"]
    z_adj = 10
    for it in adj_items:
        seg_in = float(it["in_point"])
        seg_out = float(it["out_point"])
        if mode == "pin_edges_v1":
            effects = _warp_effects_adj16_pin_edges(base_effects, base_in=base_in, base_out=base_out, seg_in=seg_in, seg_out=seg_out)
        else:
            effects = base_effects

        out.append(_adjustment_bp(it=it, z_index=z_adj, effects=effects, comp_w=comp_w, comp_h=comp_h))
        z_adj += 1

    # (5) Footages
    footage_items = [it for it in layers_cfg if str(it.get("type")) == "footage"]
    base_z_foot = 100
    n = len(footage_items)
    for i, it in enumerate(footage_items):
        z = base_z_foot + (n - 1 - i)
        out.append(_footage_bp(it=it, z_index=z, comp_w=comp_w, comp_h=comp_h))

    return [asdict(x) for x in sorted(out, key=lambda b: int(b.z_index))]
