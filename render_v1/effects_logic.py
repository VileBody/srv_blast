"""effects_logic.py

Semantic adjustment-layer styles:
- library lives in config/styles/effects_library.json
- composition.json: adjustment layers reference styles via `effectStyleId` + optional `effectOverrides`
- assembler converts style+overrides to AE `effects` list consumable by render_templates/job_template.jsx (applyEffects)

Important: right now we intentionally treat *values* as preset-locked.
The model is expected to mostly tweak timing (t/time) of keyframes, not intensities.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_DISALLOWED_EFFECT_MATCH_PREFIXES: tuple[str, ...] = (
    "ADBE CurvesCustom",
)


def _is_disallowed_effect_match_name(match_name: str) -> bool:
    raw = str(match_name or "").strip()
    if not raw:
        return False
    for prefix in _DISALLOWED_EFFECT_MATCH_PREFIXES:
        if raw == prefix or raw.startswith(prefix):
            return True
    return False


def _deep_merge(a: Any, b: Any) -> Any:
    """Deep-merge b into a (dict-recursive). For lists/scalars, b overwrites a."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return b


def build_semantic_prompt_catalog(effects_library: Dict[str, Any], *, include_defaults: bool = True) -> Dict[str, Any]:
    """Return a compact-ish catalog to embed into the LLM prompt."""
    presets = effects_library.get("effectPresets") or {}
    styles = effects_library.get("semanticStyles") or {}

    # Compact presets
    presets_out: Dict[str, Any] = {}
    for pid, p in presets.items():
        tree = (p.get("propertyTree") or {})
        presets_out[pid] = {
            "description": p.get("description", ""),
            "matchName": tree.get("matchName", ""),
            "exposedParams": [
                {"key": ep.get("key"), "matchNamePath": ep.get("matchNamePath"), "meaning": ep.get("meaning", "")}
                for ep in (p.get("exposedParams") or [])
            ],
        }

    styles_out: Dict[str, Any] = {}
    for sid, s in styles.items():
        style_entry: Dict[str, Any] = {
            "meaning": s.get("meaning", ""),
            "applicableTo": s.get("applicableTo", []),
            "effects": [
                {
                    "id": e.get("id"),
                    "presetId": e.get("presetId"),
                    "enabled": bool(e.get("enabled", True)),
                }
                for e in (s.get("effects") or [])
            ],
            "mainAnimParams": s.get("mainAnimParams") or [],
        }
        if include_defaults:
            style_entry["defaultOverrides"] = s.get("defaultOverrides") or {}
        styles_out[sid] = style_entry

    return {
        "libraryVersion": effects_library.get("libraryVersion"),
        "semanticStyles": styles_out,
        "effectPresets": presets_out,
    }


def resolve_effect_stack(
    effect_style_id: str,
    effect_overrides: Optional[Dict[str, Any]],
    effects_library: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return a list of effect instances (id/presetId/enabled/overrides)."""
    styles = effects_library.get("semanticStyles") or {}
    style = styles.get(effect_style_id)
    if not style:
        raise ValueError(f"Unknown effectStyleId: {effect_style_id}")

    stack: List[Dict[str, Any]] = []
    defaults_by_instance: Dict[str, Any] = style.get("defaultOverrides") or {}
    overrides_by_instance: Dict[str, Any] = effect_overrides or {}

    for eff in (style.get("effects") or []):
        inst_id = eff.get("id")
        preset_id = eff.get("presetId")
        if not inst_id or not preset_id:
            continue

        merged = _deep_merge(defaults_by_instance.get(inst_id, {}), overrides_by_instance.get(inst_id, {}))
        stack.append({
            "instanceId": inst_id,
            "presetId": preset_id,
            "enabled": bool(eff.get("enabled", True)),
            "overrides": merged,
        })

    return stack


def _convert_key_time(key: Dict[str, Any], layer_in: float, layer_out: float) -> float:
    if "time" in key:
        return float(key["time"])
    if "t" in key:
        dur = float(layer_out - layer_in)
        return float(layer_in) + float(key["t"]) * dur
    raise ValueError("Keyframe must have either 'time' or normalized 't'")


def _convert_value_data(value_data: Any, layer_in: float, layer_out: float) -> Any:
    """Convert a valueData block, mapping normalized key times -> absolute times."""
    if isinstance(value_data, dict) and "keys" in value_data and isinstance(value_data["keys"], list):
        keys_out = []
        for k in value_data["keys"]:
            if not isinstance(k, dict):
                continue
            k2 = dict(k)
            k2["time"] = _convert_key_time(k, layer_in, layer_out)
            # keep t if you want debugging; optional:
            # k2.pop("t", None)
            keys_out.append(k2)
        out = dict(value_data)
        out["keys"] = keys_out
        return out
    return value_data


def stack_to_ae_effects_conf(
    stack: List[Dict[str, Any]],
    effects_library: Dict[str, Any],
    *,
    layer_in: float,
    layer_out: float,
) -> List[Dict[str, Any]]:
    """Convert resolved stack -> AE `effects` list (matchName + params dict).

    Output format is compatible with render_templates/job_template.jsx: applyEffects(layer, config.effects)
    """
    presets = effects_library.get("effectPresets") or {}

    out: List[Dict[str, Any]] = []
    for inst in stack:
        if not inst.get("enabled", True):
            continue
        preset = presets.get(inst.get("presetId"))
        if not preset:
            continue

        tree = preset.get("propertyTree") or {}
        match_name = tree.get("matchName")
        if not match_name:
            continue
        if _is_disallowed_effect_match_name(str(match_name)):
            continue

        exposed = preset.get("exposedParams") or []
        key_to_path = {e.get("key"): e.get("matchNamePath") for e in exposed if e.get("key") and e.get("matchNamePath")}

        params_in = inst.get("overrides") or {}
        params_out: Dict[str, Any] = {}

        for key, v in params_in.items():
            if key not in key_to_path:
                # unknown param for this preset -> ignore (keeps system strict)
                continue
            path = key_to_path[key]
            params_out[path] = _convert_value_data(v, layer_in, layer_out)

        out.append({
            "matchName": match_name,
            "params": params_out,
        })

    return out


# Backwards-friendly alias
build_effects_prompt_catalog = build_semantic_prompt_catalog
