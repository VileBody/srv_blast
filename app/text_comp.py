from __future__ import annotations

import math
import os
from typing import Any, Dict, List

from app.orchestrator import ProjectOrchestrator


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


def _preflight_clamp_text_layers(
    layers: List[Dict[str, Any]],
    *,
    fps: float,
    strict: bool,
) -> None:
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
            if strict:
                raise ValueError(f"Preflight: out<=in in layer {l.get('name')!r}: {in_p}..{out_p}")
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


def build_text_layers(*, full_edit_config: Dict[str, Any], text_comp_name: str, mine_comp_name: str) -> List[Dict[str, Any]]:
    orch = ProjectOrchestrator(full_edit_config)
    orch.build()

    layers: List[Dict[str, Any]] = list(orch.final_stack)

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
    _preflight_clamp_text_layers(layers, fps=fps, strict=strict)

    return layers
