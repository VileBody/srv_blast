from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.types import KeyframeData, KeyframeEase, LayerBlueprint, PropertyData


def _looks_like_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


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

    for eff in (parade.get("children") or []):
        eff_match = eff.get("matchName")
        if not eff_match:
            continue

        params: Dict[str, PropertyData] = {}
        p_idx = 0

        for p in (eff.get("children") or []):
            pm = p.get("matchName")
            if not pm:
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
            effects_out[eff_match] = params

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

    file_name = str(it["file_name"])
    raw_path = str(it.get("file_path") or "")
    if _looks_like_url(raw_path):
        # RELocatable: keep remote_url, clear file_path for JSX (will be resolved by assets_resolve.json on Win)
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": "", "remote_url": raw_path}
    else:
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": raw_path}

    # cover mode
    fit_mode = str(it.get("fit_mode") or "cover")
    sw = it.get("src_w")
    sh = it.get("src_h")
    if fit_mode == "cover" and sw is not None and sh is not None:
        sw_i = int(sw)
        sh_i = int(sh)
        anchor, pos, scale = _compute_cover_transform(comp_w, comp_h, sw_i, sh_i)
        bp.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=anchor)
        bp.props["tf_position"] = PropertyData("ADBE Position", value=pos)
        bp.props["tf_scale"] = PropertyData("ADBE Scale", value=scale)
        bp.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=0)
        bp.props["tf_opacity"] = PropertyData("ADBE Opacity", value=100)

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

    file_name = str(it["file_name"])
    raw_path = str(it.get("file_path") or "")
    if _looks_like_url(raw_path):
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": "", "remote_url": raw_path}
    else:
        bp.text_data["source_footage"] = {"file_name": file_name, "file_path": raw_path}

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
    precomp_z_index: int = 100,
    precomp_placement: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Desired final stack (top -> bottom):
      audio (optional)
      TEXT precomp
      Adjustments
      Footages
    """
    comp_w = int(footage_cfg.get("main_comp_w", 1080))
    comp_h = int(footage_cfg.get("main_comp_h", 1080))

    layers_cfg = list(footage_cfg.get("layers") or [])

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

    out: List[LayerBlueprint] = []

    # (1) TEXT precomp
    pre = LayerBlueprint(
        name=text_comp_name,
        type="precomp",
        in_point=0.0,
        out_point=float(footage_cfg.get("text_dur_hint", 18.4351017684351)),
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

    # (2) Audio-only
    for it in layers_cfg:
        if str(it.get("type")) == "audio_only":
            out.append(_audio_only_bp(it=it, z_index=2))

    # (3) Adjustments
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

    # (4) Footages
    footage_items = [it for it in layers_cfg if str(it.get("type")) == "footage"]
    base_z_foot = 100
    n = len(footage_items)
    for i, it in enumerate(footage_items):
        z = base_z_foot + (n - 1 - i)
        out.append(_footage_bp(it=it, z_index=z, comp_w=comp_w, comp_h=comp_h))

    return [asdict(x) for x in sorted(out, key=lambda b: int(b.z_index))]
