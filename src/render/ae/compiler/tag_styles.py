from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.core.config.style_loader import get_tag_pack

log = logging.getLogger(__name__)


def _get_tag_id(layer: Dict[str, Any]) -> Optional[str]:
    for k in ("tagId", "tag", "textTag", "fxTag"):
        v = layer.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _get_tag_plan(layer: Dict[str, Any]) -> Dict[str, Any]:
    for k in ("tagPlan", "timing", "timingPlan", "textTiming"):
        v = layer.get(k)
        if isinstance(v, dict):
            return v
    return {}


def _cleanup_tag_fields(layer: Dict[str, Any]) -> None:
    for k in (
        "tagId",
        "tag",
        "textTag",
        "fxTag",
        "tagPlan",
        "timing",
        "timingPlan",
        "textTiming",
        "tagRole",
    ):
        layer.pop(k, None)


def _select_role_template(tag_pack: Dict[str, Any], role: str) -> Dict[str, Any]:
    """
    Поддерживаем несколько вариантов структуры tag pack:
      - {"layers": {"text": {...}, "adjustment": {...}}}
      - {"text": {...}, "adjustment": {...}}
      - {"textLayer": {...}, "adjustmentLayer": {...}}
    """
    if not isinstance(tag_pack, dict) or not tag_pack:
        return {}

    layers = tag_pack.get("layers")
    if isinstance(layers, dict) and role in layers and isinstance(layers[role], dict):
        return layers[role]

    if role in tag_pack and isinstance(tag_pack[role], dict):
        return tag_pack[role]

    if role == "text":
        for k in ("textLayer", "text_layer", "text_template"):
            v = tag_pack.get(k)
            if isinstance(v, dict):
                return v

    if role == "adjustment":
        for k in ("adjustmentLayer", "adjustment_layer", "adj_template"):
            v = tag_pack.get(k)
            if isinstance(v, dict):
                return v

    return {}


def ensure_tag_adjustment_layers(items: List[Dict[str, Any]], tags_catalog: Dict[str, Any]) -> int:
    """
    Автодобавляем adjustment-слой сразу над каждым text-слоем с tagId,
    если рядом нет уже adjustment со столь же окном.
    """
    changed = 0
    if not items:
        return 0

    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        if not isinstance(layers, list) or not layers:
            continue

        out_layers: List[Dict[str, Any]] = []
        i = 0
        while i < len(layers):
            layer = layers[i]
            out_layers.append(layer)
            i += 1

            if not isinstance(layer, dict):
                continue
            if (layer.get("type") or "").lower() != "text":
                continue

            tag_id = _get_tag_id(layer)
            if not tag_id:
                continue

            in_p = layer.get("inPoint")
            out_p = layer.get("outPoint")
            if in_p is None or out_p is None:
                continue

            # If next layer is already adjustment with same window - skip
            nxt = layers[i] if i < len(layers) else None
            if isinstance(nxt, dict) and (nxt.get("type") or "").lower() == "adjustment":
                if abs(float(nxt.get("inPoint", -999)) - float(in_p)) < 1e-6 and abs(
                    float(nxt.get("outPoint", -999)) - float(out_p)
                ) < 1e-6:
                    continue

            # Create adjustment shell (tag styles will bake effects later)
            adj: Dict[str, Any] = {
                "type": "adjustment",
                "name": f"TagFX:{tag_id}",
                "startTime": float(layer.get("startTime", in_p)),
                "inPoint": float(in_p),
                "outPoint": float(out_p),
                "enabled": True,
                "tagId": tag_id,
                "tagRole": "adjustment",
            }

            tag_plan = _get_tag_plan(layer)
            if tag_plan:
                adj["tagPlan"] = tag_plan

            out_layers.append(adj)
            changed += 1

        it["layers"] = out_layers

    return changed


def _extract_word_starts_norm(
    tag_plan: Dict[str, Any],
    *,
    layer_in: float,
    layer_out: float,
    fps: float,
    global_start_sec: Optional[float] = None,
) -> Optional[List[float]]:
    dur = float(layer_out - layer_in)
    if dur <= 1e-9:
        return None

    words = tag_plan.get("words")
    if not isinstance(words, list) or not words:
        return None

    # Case A: list of dicts with timings
    starts_abs: List[float] = []
    has_timings = False
    for w in words:
        if isinstance(w, dict) and "start_sec" in w:
            try:
                starts_abs.append(float(w["start_sec"]))
                has_timings = True
            except Exception:
                continue

    # Case B: list of strings only -> evenly distribute
    if not has_timings:
        n = len(words)
        if n <= 0:
            return None
        return [min(1.0, max(0.0, (i / max(1, n - 1)))) for i in range(n)]

    # Heuristic: if timings look global (big numbers), shift by global_start_sec
    if global_start_sec is not None and starts_abs:
        mx = max(starts_abs)
        if mx > layer_out + 1.0 and global_start_sec > 0:
            starts_abs = [s - float(global_start_sec) for s in starts_abs]

    starts_norm = [(s - layer_in) / dur for s in starts_abs]
    starts_norm = [min(1.0, max(0.0, t)) for t in starts_norm]
    starts_norm = sorted(set(starts_norm))
    return starts_norm or None


def _detect_pair_dt_norm(keys: List[Dict[str, Any]], *, fps: float, layer_dur: float) -> Tuple[bool, float]:
    """
    Определяем, похожи ли ключи на пары (0-1,2-3,...),
    и оцениваем dt в нормализованных t единицах.
    """
    if not keys or len(keys) < 2:
        frame_dt = (1.0 / max(1e-6, fps)) / max(1e-6, layer_dur)
        return False, frame_dt

    ts: List[float] = []
    for k in keys:
        if isinstance(k, dict) and "t" in k:
            try:
                ts.append(float(k["t"]))
            except Exception:
                ts.append(0.0)
        else:
            ts.append(0.0)

    pair = (len(keys) % 2 == 0)
    diffs: List[float] = []
    if pair:
        for i in range(0, len(ts), 2):
            if i + 1 < len(ts):
                d = ts[i + 1] - ts[i]
                if d > 0:
                    diffs.append(d)

    frame_dt = (1.0 / max(1e-6, fps)) / max(1e-6, layer_dur)
    if not diffs:
        return pair, frame_dt

    diffs.sort()
    med = diffs[len(diffs) // 2]
    # if median delta is small, treat as "one frame-ish"
    if med <= 0.1:
        return True, med
    return False, frame_dt


def _retime_value_data(
    value_data: Dict[str, Any],
    *,
    word_starts_norm: Optional[List[float]],
    fps: float,
    layer_dur: float,
) -> Dict[str, Any]:
    if not word_starts_norm:
        return value_data

    keys = value_data.get("keys")
    if not isinstance(keys, list) or not keys:
        return value_data

    # normalize keys count
    key_dicts: List[Dict[str, Any]] = [k for k in keys if isinstance(k, dict)]
    if not key_dicts:
        return value_data

    is_pair, dt = _detect_pair_dt_norm(key_dicts, fps=fps, layer_dur=layer_dur)
    n_keys = len(key_dicts)
    slots = (n_keys // 2) if (is_pair and n_keys >= 2) else n_keys
    if slots <= 0:
        return value_data

    # map slots -> word indices
    w = word_starts_norm
    if not w:
        return value_data

    def pick_word_t(slot_i: int) -> float:
        if len(w) == 1:
            return w[0]
        if slots == 1:
            return w[0]
        idx = int(round(slot_i * (len(w) - 1) / float(slots - 1)))
        idx = max(0, min(len(w) - 1, idx))
        return w[idx]

    new_keys: List[Dict[str, Any]] = []
    prev_t = 0.0
    if is_pair:
        for si in range(slots):
            base = pick_word_t(si)
            t0 = max(prev_t, base)
            t1 = min(1.0, max(t0, t0 + dt))
            prev_t = t1

            k0 = copy.deepcopy(key_dicts[2 * si])
            k0.pop("time", None)
            k0["t"] = t0
            new_keys.append(k0)

            if 2 * si + 1 < n_keys:
                k1 = copy.deepcopy(key_dicts[2 * si + 1])
                k1.pop("time", None)
                k1["t"] = t1
                new_keys.append(k1)
    else:
        for i in range(n_keys):
            base = pick_word_t(i if slots == n_keys else int(round(i * (slots - 1) / max(1, n_keys - 1))))
            t0 = max(prev_t, base)
            prev_t = t0
            k0 = copy.deepcopy(key_dicts[i])
            k0.pop("time", None)
            k0["t"] = t0
            new_keys.append(k0)

    out = dict(value_data)
    out["keys"] = new_keys
    return out


def _retime_recursive(
    obj: Any,
    *,
    word_starts_norm: Optional[List[float]],
    fps: float,
    layer_dur: float,
) -> Any:
    if isinstance(obj, dict):
        # valueData shape
        if "keys" in obj and isinstance(obj.get("keys"), list):
            return _retime_value_data(obj, word_starts_norm=word_starts_norm, fps=fps, layer_dur=layer_dur)
        return {k: _retime_recursive(v, word_starts_norm=word_starts_norm, fps=fps, layer_dur=layer_dur) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_retime_recursive(v, word_starts_norm=word_starts_norm, fps=fps, layer_dur=layer_dur) for v in obj]
    return obj


def apply_tag_styles(
    items: List[Dict[str, Any]],
    tags_catalog: Dict[str, Any],
    *,
    fps: float,
    global_start_sec: Optional[float] = None,
) -> int:
    """
    Для слоёв с tagId подмешиваем шаблон из tag pack:
      - text: textDocument/transform/effects/textAnimators/threeDLayer
      - adjustment: transform/effects
    И ретаймим все keyframes по words (если words есть).
    """
    changed = 0
    if not items:
        return 0

    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        if not isinstance(layers, list) or not layers:
            continue

        for layer in layers:
            if not isinstance(layer, dict):
                continue

            tag_id = _get_tag_id(layer)
            if not tag_id:
                continue

            ltype = (layer.get("type") or "").lower()
            role = "adjustment" if ltype == "adjustment" else ("text" if ltype == "text" else "")
            if not role:
                continue

            tag_pack = get_tag_pack(tag_id)
            if not tag_pack:
                log.warning("[tag_styles] Tag pack not found for tagId=%r (layer=%r)", tag_id, layer.get("name"))
                continue

            tpl = _select_role_template(tag_pack, role)
            if not tpl:
                log.warning("[tag_styles] No template for role=%s tagId=%r", role, tag_id)
                continue

            # Retime keyframes
            layer_in = float(layer.get("inPoint", layer.get("startTime", 0.0)) or 0.0)
            layer_out = float(layer.get("outPoint", layer_in) or layer_in)
            layer_dur = float(layer_out - layer_in)
            if layer_dur <= 1e-9:
                layer_dur = 1e-6

            tag_plan = _get_tag_plan(layer)
            word_starts_norm = _extract_word_starts_norm(
                tag_plan,
                layer_in=layer_in,
                layer_out=layer_out,
                fps=fps,
                global_start_sec=global_start_sec,
            )

            baked_tpl = _retime_recursive(copy.deepcopy(tpl), word_starts_norm=word_starts_norm, fps=fps, layer_dur=layer_dur)

            # Apply template to layer
            if role == "text":
                # threeDLayer
                if "threeDLayer" in baked_tpl:
                    layer["threeDLayer"] = bool(baked_tpl["threeDLayer"])
                elif "threeD" in baked_tpl:
                    layer["threeDLayer"] = bool(baked_tpl["threeD"])

                # merge textDocument (keep real text)
                if isinstance(baked_tpl.get("textDocument"), dict):
                    real_text = None
                    if isinstance(layer.get("textDocument"), dict):
                        real_text = layer["textDocument"].get("text")
                    layer.setdefault("textDocument", {})
                    for k, v in baked_tpl["textDocument"].items():
                        layer["textDocument"].setdefault(k, v)
                    if real_text is not None:
                        layer["textDocument"]["text"] = real_text

                # transform (defaults)
                if isinstance(baked_tpl.get("transform"), dict):
                    layer.setdefault("transform", {})
                    for k, v in baked_tpl["transform"].items():
                        layer["transform"].setdefault(k, v)

                # textAnimators (override)
                if isinstance(baked_tpl.get("textAnimators"), list):
                    layer["textAnimators"] = baked_tpl["textAnimators"]

                # effects (prepend tag effects)
                tpl_fx = baked_tpl.get("effects") or baked_tpl.get("effectStack") or []
                if isinstance(tpl_fx, list) and tpl_fx:
                    existing = layer.get("effects") or []
                    if not isinstance(existing, list):
                        existing = []
                    layer["effects"] = list(tpl_fx) + list(existing)

                # prevent legacy combo baking from stacking on top
                for k in ("textFxComboId", "text_fx_combo_id", "textFxOverrides", "text_fx_overrides"):
                    layer.pop(k, None)

                changed += 1

            elif role == "adjustment":
                if isinstance(baked_tpl.get("transform"), dict):
                    layer.setdefault("transform", {})
                    for k, v in baked_tpl["transform"].items():
                        layer["transform"].setdefault(k, v)

                tpl_fx = baked_tpl.get("effects") or baked_tpl.get("effectStack") or []
                if isinstance(tpl_fx, list) and tpl_fx:
                    existing = layer.get("effects") or []
                    if not isinstance(existing, list):
                        existing = []
                    layer["effects"] = list(tpl_fx) + list(existing)

                changed += 1

            _cleanup_tag_fields(layer)

    return changed
