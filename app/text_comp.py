from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List

from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS, normalize_subtitles_mode
from app.orchestrator import ProjectOrchestrator
from app.text_flow_renderer import TextFlowRendererFactory
from mlcore.models.subtitles_flow import SubtitleFlowPlan


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _normalize_layer_dict(l: Dict[str, Any], *, text_comp_name: str, mine_comp_name: str) -> None:
    td = l.get("text_data") or {}
    meta = td.get("layer_meta") or {}
    td["layer_meta"] = meta
    l["text_data"] = td

    meta.setdefault("comp_name_target", text_comp_name)

    # legacy mine-inner routing
    if meta.get("comp_id_target") == 88:
        meta["comp_name_target"] = mine_comp_name

    # new by-name video -> precomp
    if l.get("type") == "video":
        comp_name = l.get("comp_name")
        if isinstance(comp_name, str) and comp_name.strip():
            l["type"] = "precomp"
            td["precomp_source"] = {"comp_name": comp_name}
            meta["comp_name_target"] = text_comp_name

    # legacy by-id video -> precomp (both Mine layers)
    if l.get("type") == "video" and int(l.get("comp_id") or 0) == 88:
        l["type"] = "precomp"
        l["comp_name"] = mine_comp_name
        td["precomp_source"] = {"comp_name": mine_comp_name}
        meta["comp_name_target"] = text_comp_name


def _iter_property_dicts(layer: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    props = layer.get("props")
    if isinstance(props, dict):
        for pd in props.values():
            if isinstance(pd, dict):
                out.append(pd)
    effects = layer.get("effects")
    if isinstance(effects, dict):
        for params in effects.values():
            if isinstance(params, dict):
                for pd in params.values():
                    if isinstance(pd, dict):
                        out.append(pd)
    return out


def _is_glitch_peak_zero_window_layer(layer: Dict[str, Any]) -> bool:
    return str(layer.get("name") or "").strip().lower() == "glitch_peak_prefix"


def _find_mine_inner_text_layer(layers: List[Dict[str, Any]], *, mine_comp_name: str) -> Dict[str, Any] | None:
    target = str(mine_comp_name or "").strip()
    for l in layers:
        if not isinstance(l, dict):
            continue
        if str(l.get("type") or "") != "text":
            continue
        if str(l.get("name") or "").strip().lower() != "mine":
            continue
        td = l.get("text_data")
        if not isinstance(td, dict):
            continue
        meta = td.get("layer_meta")
        if not isinstance(meta, dict):
            continue
        comp_target = str(meta.get("comp_name_target") or "").strip()
        comp_id_target = int(meta.get("comp_id_target") or 0)
        if (target and comp_target == target) or comp_id_target == 88:
            return l
    return None


def _merge_glitch_peak_into_mine(
    *,
    layers: List[Dict[str, Any]],
    glitch_layer: Dict[str, Any],
    mine_comp_name: str,
) -> None:
    mine_layer = _find_mine_inner_text_layer(layers, mine_comp_name=mine_comp_name)
    if isinstance(mine_layer, dict):
        peak_text = str(glitch_layer.get("text") or "").strip()
        mine_text = str(mine_layer.get("text") or "").strip()
        if peak_text and mine_text:
            merged = f"{peak_text}\r{mine_text}"
        else:
            merged = peak_text or mine_text
        mine_layer["text"] = merged

        td = mine_layer.get("text_data")
        if not isinstance(td, dict):
            td = {}
            mine_layer["text_data"] = td
        td["char_styles_ungrouped"] = [
            {"i": i, "font": "Point-ExtraBold", "fontSize": 100}
            for i in range(len(merged))
        ]

    glitch_layer["text"] = ""
    gtd = glitch_layer.get("text_data")
    if not isinstance(gtd, dict):
        gtd = {}
        glitch_layer["text_data"] = gtd
    gmeta = gtd.get("layer_meta")
    if not isinstance(gmeta, dict):
        gmeta = {}
        gtd["layer_meta"] = gmeta
    gmeta["enabled"] = False
    gtd["char_styles_ungrouped"] = []


def _preflight_clamp_text_layers(
    layers: List[Dict[str, Any]],
    *,
    fps: float,
    strict: bool,
    mine_comp_name: str,
) -> None:
    log = logging.getLogger("app.text_comp")
    if fps <= 0:
        fps = 23.9759979248047
    dt = 1.0 / float(fps)
    eps = dt / 10.0

    for l in layers:
        if not isinstance(l, dict):
            continue
        tpe = str(l.get("type") or "")
        if tpe not in {"text", "adjustment"}:
            continue

        try:
            in_p = float(l.get("in_point"))
            out_p = float(l.get("out_point"))
        except Exception:
            if strict:
                raise ValueError(f"Preflight: invalid in/out in layer {l.get('name')!r}")
            continue
        if out_p <= in_p:
            if _is_glitch_peak_zero_window_layer(l):
                _merge_glitch_peak_into_mine(
                    layers=layers,
                    glitch_layer=l,
                    mine_comp_name=mine_comp_name,
                )
            elif tpe == "adjustment" and abs(out_p - in_p) <= eps:
                # Some dumped adjustment layers can collapse to a zero-length seam after
                # rounding/clamping; keep them as-is and let AE evaluate the layer boundary.
                log.warning("preflight_allow_zero_adjustment layer=%r t=%.6f", l.get("name"), in_p)
            elif strict:
                raise ValueError(f"Preflight: out<=in in layer {l.get('name')!r}: {in_p}..{out_p}")
            else:
                out_p = in_p + dt
                l["out_point"] = out_p

        max_t = out_p - dt
        if max_t < in_p:
            max_t = in_p

        for pd in _iter_property_dicts(l):
            kfs = pd.get("keyframes")
            if not isinstance(kfs, list) or not kfs:
                continue

            cleaned: List[Dict[str, Any]] = []
            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                t = kf.get("t")
                try:
                    tf = float(t)
                except Exception:
                    if strict:
                        raise ValueError(f"Preflight: non-float keyframe time in layer {l.get('name')!r}")
                    continue
                if not math.isfinite(tf):
                    if strict:
                        raise ValueError(f"Preflight: non-finite keyframe time in layer {l.get('name')!r}")
                    continue
                if tf < in_p:
                    tf = in_p
                if tf > max_t:
                    tf = max_t
                nkf = dict(kf)
                nkf["t"] = tf
                cleaned.append(nkf)

            cleaned.sort(key=lambda x: float(x.get("t", 0.0)))
            prev_t = in_p - eps
            for nkf in cleaned:
                t0 = float(nkf["t"])
                if t0 <= prev_t:
                    t0 = prev_t + eps
                if t0 > max_t:
                    t0 = max_t
                nkf["t"] = t0
                prev_t = t0

            pd["keyframes"] = cleaned


def _apply_text_time_shift(layers: List[Dict[str, Any]], *, shift_s: float) -> None:
    shift = float(shift_s)
    if abs(shift) <= 1e-12:
        return

    for l in layers:
        if not isinstance(l, dict):
            continue
        if str(l.get("type") or "") != "text":
            continue

        try:
            in_p = float(l.get("in_point"))
            out_p = float(l.get("out_point"))
        except Exception:
            continue

        l["in_point"] = in_p - shift
        l["out_point"] = out_p - shift

        for pd in _iter_property_dicts(l):
            kfs = pd.get("keyframes")
            if not isinstance(kfs, list):
                continue
            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                try:
                    kf["t"] = float(kf.get("t")) - shift
                except Exception:
                    continue


def _resolve_subtitles_mode(full_edit_config: Dict[str, Any]) -> str:
    raw = ""
    if isinstance(full_edit_config, dict):
        raw = str(full_edit_config.get("subtitles_mode") or "").strip()
    return normalize_subtitles_mode(raw, default=SUBTITLES_MODE_LEGACY_BLOCKS)


def _require_flow_plan(full_edit_config: Dict[str, Any], *, mode: str) -> SubtitleFlowPlan:
    obj = full_edit_config.get("subtitle_flow_plan")
    if not isinstance(obj, dict):
        raise RuntimeError(
            f"subtitles_mode={mode!r} requires subtitle_flow_plan object in full_edit_config"
        )
    flow = SubtitleFlowPlan.model_validate(obj)
    if str(flow.mode) != str(mode):
        raise RuntimeError(
            f"subtitle_flow_plan.mode mismatch: {flow.mode!r} != {mode!r}"
        )
    return flow


def build_text_layers(*, full_edit_config: Dict[str, Any], text_comp_name: str, mine_comp_name: str) -> List[Dict[str, Any]]:
    mode = _resolve_subtitles_mode(full_edit_config)
    if mode == SUBTITLES_MODE_LEGACY_BLOCKS:
        orch = ProjectOrchestrator(full_edit_config)
        orch.build()
        layers: List[Dict[str, Any]] = list(orch.final_stack)
    else:
        flow = _require_flow_plan(full_edit_config, mode=mode)
        flow_renderer = TextFlowRendererFactory.create(mode)
        layers = flow_renderer.render(
            flow_plan=flow,
            text_comp_name=text_comp_name,
        )

    for l in layers:
        # precomp node: нормализуем ВНУТРЕННИЕ слои тоже (на будущее, и чтобы было железобетонно)
        if isinstance(l, dict) and l.get("type") == "precomp" and isinstance(l.get("comp"), dict):
            inner = l.get("layers")
            if isinstance(inner, list):
                for it in inner:
                    if isinstance(it, dict):
                        _normalize_layer_dict(it, text_comp_name=text_comp_name, mine_comp_name=mine_comp_name)
            continue

        if isinstance(l, dict):
            _normalize_layer_dict(l, text_comp_name=text_comp_name, mine_comp_name=mine_comp_name)

    comp = full_edit_config.get("composition") if isinstance(full_edit_config, dict) else {}
    fps_raw = comp.get("fps", 23.9759979248047) if isinstance(comp, dict) else 23.9759979248047
    try:
        fps = float(fps_raw)
    except Exception:
        fps = 23.9759979248047
    shift_s = _env_float("TEXT_LAYER_TIME_SHIFT_S", 0.3)
    _apply_text_time_shift(layers, shift_s=shift_s)
    strict = (os.environ.get("TEXT_PREFLIGHT_STRICT", "1").strip() != "0")
    _preflight_clamp_text_layers(
        layers,
        fps=fps,
        strict=strict,
        mine_comp_name=mine_comp_name,
    )

    return layers
