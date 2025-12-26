"""render_v1/text_fx_logic.py

Text FX combos = (effect stack attached to *text layers*) + (text animators).

Goals:
- Keep manager-picked values baked in presets.
- Allow LLM (or any planner) to override only timing/keyframes and a small set of safe knobs.
- Produce a JSON that job_template.jsx can apply with ExtendScript.

This is intentionally separate from:
- config/styles/effects_library.json (semantic adjustment-layer styles)
- render_v1/effects_logic.py (resolves adjustment-layer stacks)

Layer-side integration options:
1) Per-layer fields (easiest for LLM):
   layer.textFxComboId + layer.textFxOverrides

2) Root plan (good for multi-pass / separate LLM step):
   composition.textFxPlan = { defaultCompId, layers: [...] }
   Each entry targets a layer (by name/text/timing) and assigns combo + overrides.

Assembler expands combo → layer.effects + layer.textAnimators + transform.keyframes.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


def _deep_merge(a: Any, b: Any) -> Any:
    """Deep-merge b into a (dict/list/scalar). Returns new object."""
    if a is None:
        return copy.deepcopy(b)
    if b is None:
        return copy.deepcopy(a)

    if isinstance(a, dict) and isinstance(b, dict):
        out = copy.deepcopy(a)
        for k, v in b.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out

    if isinstance(a, list) and isinstance(b, list):
        # by default, override the list (safer than concat for deterministic presets)
        return copy.deepcopy(b)

    return copy.deepcopy(b)


def _layer_text(layer: Dict[str, Any]) -> str:
    td = layer.get("textDocument") or {}
    if isinstance(td, dict) and isinstance(td.get("text"), str):
        return td["text"]
    v = layer.get("content") or layer.get("text") or ""
    return str(v) if v is not None else ""


def _float_eq(a: Any, b: Any, eps: float = 1e-3) -> bool:
    try:
        return abs(float(a) - float(b)) <= eps
    except Exception:
        return False


def _find_comp(items: List[Dict[str, Any]], comp_id: str) -> Optional[Dict[str, Any]]:
    for it in items:
        if (it.get("type") or "").lower() == "comp" and it.get("id") == comp_id:
            return it
    return None


def _find_text_layer(
    layers: List[Dict[str, Any]],
    *,
    layer_name: Optional[str] = None,
    layer_text: Optional[str] = None,
    in_point: Optional[float] = None,
    out_point: Optional[float] = None,
    layer_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    # 1) direct index (supports both 0-based and 1-based)
    if layer_index is not None:
        try:
            idx = int(layer_index)
            if 0 <= idx < len(layers):
                return layers[idx]
            if 1 <= idx <= len(layers):
                return layers[idx - 1]
        except Exception:
            pass

    # 2) by name
    if layer_name:
        for l in layers:
            if (l.get("type") or "") != "text":
                continue
            if l.get("name") == layer_name:
                if in_point is not None and not _float_eq(l.get("inPoint"), in_point):
                    continue
                if out_point is not None and not _float_eq(l.get("outPoint"), out_point):
                    continue
                return l

    # 3) by text (+ optional timing)
    if layer_text:
        for l in layers:
            if (l.get("type") or "") != "text":
                continue
            if _layer_text(l) != layer_text:
                continue
            if in_point is not None and not _float_eq(l.get("inPoint"), in_point):
                continue
            if out_point is not None and not _float_eq(l.get("outPoint"), out_point):
                continue
            return l

    return None


def _convert_keys_to_abs_time(
    keys: List[Dict[str, Any]],
    *,
    layer_in: float,
    layer_out: float,
) -> List[Dict[str, Any]]:
    """Convert keys with dt (sec from layer_in) to absolute time.

    Supported key formats (per key):
      - {"time": <abs_sec>, "value": ...}  -> kept
      - {"t": <0..1>, "value": ...}        -> kept (job_template.jsx resolves t)
      - {"dt": <sec>, "value": ...}        -> converted to {"time": layer_in + dt, ...}
    """
    out: List[Dict[str, Any]] = []
    for k in keys or []:
        if not isinstance(k, dict):
            continue
        if "value" not in k:
            continue

        if k.get("time") is not None:
            out.append({"time": float(k["time"]), "value": k["value"]})
            continue

        if k.get("t") is not None:
            out.append({"t": float(k["t"]), "value": k["value"]})
            continue

        if k.get("dt") is not None:
            try:
                t_abs = float(layer_in) + float(k["dt"])
            except Exception:
                continue
            # clamp slightly to layer window (keeps AE from creating weird negative-time keys)
            if t_abs < layer_in:
                t_abs = layer_in
            if t_abs > layer_out:
                t_abs = layer_out
            out.append({"time": t_abs, "value": k["value"]})
            continue

    return out


def expand_text_fx_on_layer(
    layer: Dict[str, Any],
    *,
    combo_id: str,
    text_fx_library: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    """Expands one combo onto a text layer (in-place).

    Adds/merges:
      - layer.effects      (effect stack on the text layer itself)
      - layer.textAnimators
      - layer.transform.opacity keyframes (if overrides request it)
    """
    if (layer.get("type") or "").lower() != "text":
        return

    combos = (text_fx_library or {}).get("combos") or {}
    if not isinstance(combos, dict):
        combos = {}

    combo = combos.get(combo_id)
    if not combo and (text_fx_library or {}).get("defaultComboId") in combos:
        combo = combos.get(text_fx_library.get("defaultComboId"))

    if not isinstance(combo, dict):
        return

    combo_apply = combo.get("apply") if isinstance(combo, dict) else None
    combo_text = combo_apply.get("textLayer") if isinstance(combo_apply, dict) else None

    # 1) effect stack (attach to text layer)
    combo_fx = combo.get("effectStack") or []
    if isinstance(combo_fx, list) and combo_fx:
        existing = layer.get("effects")
        if not isinstance(existing, list):
            existing = []
        existing_mn = set()
        for e in existing:
            if isinstance(e, dict) and e.get("matchName"):
                existing_mn.add(e.get("matchName"))

        for e in combo_fx:
            if not isinstance(e, dict):
                continue
            mn = e.get("matchName")
            if mn and mn in existing_mn:
                continue
            existing.append(copy.deepcopy(e))

        layer["effects"] = existing

    # 2) text animators (preset)
    combo_anim = combo_text.get("textAnimators") if isinstance(combo_text, dict) else None
    if not isinstance(combo_anim, list):
        combo_anim = combo.get("textAnimators") or []
    if isinstance(combo_anim, list) and combo_anim:
        # overwrite (preset decides the structure)
        layer["textAnimators"] = copy.deepcopy(combo_anim)

    # 3) apply overrides (non-destructive / narrow)
    if not overrides:
        return

    # 3.1) text override
    if isinstance(overrides.get("text"), str):
        layer.setdefault("textDocument", {})
        if isinstance(layer["textDocument"], dict):
            layer["textDocument"]["text"] = overrides["text"]

    # timing override (rare, but allow)
    timing = overrides.get("timing") or {}
    if isinstance(timing, dict):
        for k in ("startTime", "inPoint", "outPoint"):
            if k in timing:
                layer[k] = timing[k]

    layer_in = float(layer.get("inPoint") or layer.get("startTime") or 0.0)
    layer_out = float(layer.get("outPoint") or layer_in)

    # 3.2) transform opacity keys
    op_override = overrides.get("opacity") or overrides.get("transformOpacity") or {}
    if isinstance(op_override, dict) and isinstance(op_override.get("keys"), list):
        keys_abs = _convert_keys_to_abs_time(op_override["keys"], layer_in=layer_in, layer_out=layer_out)
        if keys_abs:
            layer.setdefault("transform", {})
            if isinstance(layer["transform"], dict):
                layer["transform"]["opacity"] = {"keys": keys_abs}

    # 3.3) text animator keys: target = animator + selector + propertyMatchName
    ta = overrides.get("textAnimatorKeys") or {}
    if isinstance(ta, dict) and isinstance(ta.get("keys"), list):
        anim_name = ta.get("animatorName") or "Animator 1"
        sel_name = ta.get("selectorName") or "Range Selector 1"
        prop_mn = ta.get("propertyMatchName") or "ADBE Text Percent Start"

        keys_abs = _convert_keys_to_abs_time(ta["keys"], layer_in=layer_in, layer_out=layer_out)
        if keys_abs:
            _apply_text_animator_keys(
                layer,
                animator_name=str(anim_name),
                selector_name=str(sel_name),
                property_match_name=str(prop_mn),
                keys_value_data={"keys": keys_abs},
            )


def _apply_text_animator_keys(
    layer: Dict[str, Any],
    *,
    animator_name: str,
    selector_name: str,
    property_match_name: str,
    keys_value_data: Dict[str, Any],
) -> None:
    animators = layer.get("textAnimators")
    if not isinstance(animators, list) or not animators:
        return

    # find animator
    animator = None
    for a in animators:
        if not isinstance(a, dict):
            continue
        if a.get("name") == animator_name:
            animator = a
            break
    if animator is None:
        animator = animators[0] if isinstance(animators[0], dict) else None
    if not isinstance(animator, dict):
        return

    selectors = animator.get("selectors")
    if not isinstance(selectors, list) or not selectors:
        return

    selector = None
    for s in selectors:
        if not isinstance(s, dict):
            continue
        if s.get("name") == selector_name:
            selector = s
            break
    if selector is None:
        selector = selectors[0] if isinstance(selectors[0], dict) else None
    if not isinstance(selector, dict):
        return

    props = selector.get("properties")
    if not isinstance(props, dict):
        props = {}
        selector["properties"] = props

    props[property_match_name] = keys_value_data


def apply_text_fx_from_layer_fields(
    items: List[Dict[str, Any]],
    *,
    text_fx_library: Dict[str, Any],
    cleanup: bool = True,
) -> int:
    """Expands text fx for layers that already carry `textFxComboId` / `textFxOverrides`.

    Returns number of layers modified.
    """
    count = 0
    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        if not isinstance(layers, list):
            continue
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if (layer.get("type") or "").lower() != "text":
                continue

            combo_id = layer.get("textFxComboId") or layer.get("text_fx_combo_id")
            if not combo_id:
                continue

            overrides = layer.get("textFxOverrides") or layer.get("text_fx_overrides") or {}
            if overrides is None:
                overrides = {}

            expand_text_fx_on_layer(
                layer,
                combo_id=str(combo_id),
                text_fx_library=text_fx_library,
                overrides=overrides if isinstance(overrides, dict) else None,
            )
            count += 1

            if cleanup:
                for k in (
                    "textFxComboId",
                    "text_fx_combo_id",
                    "textFxOverrides",
                    "text_fx_overrides",
                ):
                    if k in layer:
                        layer.pop(k, None)

    return count


def apply_text_fx_plan(
    items: List[Dict[str, Any]],
    *,
    plan: Dict[str, Any],
    text_fx_library: Dict[str, Any],
    cleanup: bool = False,
) -> int:
    """Applies a root-level plan {defaultCompId, layers:[...]}.

    Each entry:
      {
        "compId": "comp_text",
        "layerName": "...",      (or layerText + inPoint/outPoint, or layerIndex)
        "layerText": "...",
        "inPoint": 0.0,
        "outPoint": 1.0,
        "comboId": "TXT_DEFAULT_REVEAL",
        "overrides": { ... same as expand_text_fx_on_layer ... }
      }
    """
    if not isinstance(plan, dict):
        return 0

    default_comp_id = plan.get("defaultCompId") or "comp_text"
    entries = plan.get("layers") or []
    if not isinstance(entries, list):
        return 0

    applied = 0

    for e in entries:
        if not isinstance(e, dict):
            continue

        comp_id = e.get("compId") or default_comp_id
        if not comp_id:
            continue

        comp = _find_comp(items, str(comp_id))
        if not comp:
            continue

        layers = comp.get("layers") or []
        if not isinstance(layers, list):
            continue

        combo_id = e.get("comboId") or e.get("textFxComboId")
        if not combo_id:
            continue

        target_layer = _find_text_layer(
            layers,
            layer_name=e.get("layerName"),
            layer_text=e.get("layerText"),
            in_point=e.get("inPoint"),
            out_point=e.get("outPoint"),
            layer_index=e.get("layerIndex"),
        )
        if not target_layer:
            continue

        overrides = e.get("overrides") or {}

        expand_text_fx_on_layer(
            target_layer,
            combo_id=str(combo_id),
            text_fx_library=text_fx_library,
            overrides=overrides if isinstance(overrides, dict) else None,
        )
        applied += 1

    if cleanup and "textFxPlan" in plan:
        plan.pop("textFxPlan", None)

    return applied
