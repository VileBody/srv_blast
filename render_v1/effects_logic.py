from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple


def _deep_merge(base: Any, override: Any) -> Any:
    """
    Recursively merge override into base.
    - dict + dict => deep merge
    - list/primitive => override wins
    """
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return copy.deepcopy(override)


def _get_style_id(layer_spec: Dict[str, Any]) -> Optional[str]:
    """
    Backward/forward compatible style-id lookup.

    Supported:
      - layer_spec["effectStyleId"]
      - layer_spec["styleId"]
      - layer_spec["semantic"]["styleId"]
      - layer_spec["semantic"]["effectStyleId"]
    """
    if isinstance(layer_spec.get("effectStyleId"), str):
        return layer_spec["effectStyleId"]
    if isinstance(layer_spec.get("styleId"), str):
        return layer_spec["styleId"]
    sem = layer_spec.get("semantic")
    if isinstance(sem, dict):
        if isinstance(sem.get("styleId"), str):
            return sem["styleId"]
        if isinstance(sem.get("effectStyleId"), str):
            return sem["effectStyleId"]
    return None


def _preset_param_keys(effects_library: Dict[str, Any], preset_id: Optional[str]) -> List[str]:
    if not preset_id:
        return []
    presets = effects_library.get("effectPresets") or {}
    preset = presets.get(preset_id) if isinstance(presets, dict) else None
    if not isinstance(preset, dict):
        return []

    exposed = preset.get("exposedParams") or []
    if not isinstance(exposed, list):
        return []

    keys: List[str] = []
    for p in exposed:
        if isinstance(p, dict) and isinstance(p.get("key"), str):
            keys.append(p["key"])
    return keys


def _style_effect_allowed_keys(
    effects_library: Dict[str, Any],
    style_effect: Dict[str, Any],
) -> List[str]:
    allowed = style_effect.get("allowedParams")
    if isinstance(allowed, list) and allowed:
        out: List[str] = []
        for k in allowed:
            if isinstance(k, str):
                out.append(k)
        return out
    # fallback: all preset keys
    return _preset_param_keys(effects_library, style_effect.get("presetId"))


def _collect_overrides_map(layer_spec: Dict[str, Any], *, style_mode: bool) -> Dict[str, Any]:
    """
    Collect overrides for style-mode stacks.

    Supported:
      - layer_spec["effectOverrides"] : {effectId: {paramKey: ...}}
      - layer_spec["stackOverrides"]  : alias
      - layer_spec["effects"] (style-mode): [{id, overrides}, ...]  (same structure, but only overrides)
    """
    overrides_map: Dict[str, Any] = {}

    for key in ("effectOverrides", "stackOverrides"):
        raw = layer_spec.get(key)
        if isinstance(raw, dict):
            overrides_map = _deep_merge(overrides_map, raw)

    if style_mode:
        # In style-mode, "effects" can be used as a structured overrides list:
        #   effects: [{id:"lens_blur", overrides:{...}}, ...]
        raw_effects = layer_spec.get("effects")
        if isinstance(raw_effects, list):
            list_map: Dict[str, Any] = {}
            for e in raw_effects:
                if not isinstance(e, dict):
                    continue
                eid = e.get("id")
                ov = e.get("overrides")
                if isinstance(eid, str) and isinstance(ov, dict):
                    list_map[eid] = ov
            overrides_map = _deep_merge(overrides_map, list_map)

    return overrides_map


def resolve_effect_stack(layer_spec: Dict[str, Any], effects_library: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns a normalized effect stack for an adjustment layer.

    Supported layer_spec formats:

    A) Explicit stack (no styleId):
        {
          "type": "adjustment",
          "effects": [
            {"id":"lens", "presetId":"ef_bcc_lens_blur", "overrides": {...}},
            {"id":"tr", "presetId":"ef_transform_geo", "overrides": {...}}
          ]
        }

    B) Semantic style + overrides (recommended):
        {
          "type": "adjustment",
          "effectStyleId": "fx_drop_ultrahardbass_v1",
          "effects": [   // optional: same structure, but overrides only
            {"id":"lens_blur", "overrides": {"iris_scale": {"keys":[...]}}}
          ],
          "effectOverrides": {  // optional alternative
            "transform_main": {"scale_height": {"keys":[...]}}
          }
        }

        - effectStyleId is resolved from effects_library["semanticStyles"].
        - Each style effect entry MUST have an `id` so overrides can target it.
        - Each style effect can contain "defaultOverrides" (or legacy "overrides") that act as defaults.

    Output format is always a list of:
        { "id": str, "presetId": str, "overrides": dict, "enabled": bool }
    """
    style_id = _get_style_id(layer_spec)

    # Explicit mode only triggers when there is NO style_id
    raw_effects = layer_spec.get("effects")
    if not style_id and isinstance(raw_effects, list) and raw_effects:
        out: List[Dict[str, Any]] = []
        for idx, e in enumerate(raw_effects):
            if not isinstance(e, dict):
                continue
            out.append(
                {
                    "id": e.get("id") or f"fx_{idx+1}",
                    "presetId": e.get("presetId"),
                    "overrides": copy.deepcopy(e.get("overrides") or {}),
                    "enabled": bool(e.get("enabled", True)),
                }
            )
        return out

    if not style_id:
        return []

    styles = effects_library.get("semanticStyles") or {}
    style = styles.get(style_id) if isinstance(styles, dict) else None
    if not isinstance(style, dict):
        return []

    stack = style.get("effects") or style.get("stack") or []
    if not isinstance(stack, list):
        return []

    overrides_map = _collect_overrides_map(layer_spec, style_mode=True)

    normalized: List[Dict[str, Any]] = []
    for idx, e in enumerate(stack):
        if not isinstance(e, dict):
            continue

        eid = e.get("id") or e.get("instanceId") or f"fx_{idx+1}"
        preset_id = e.get("presetId")
        enabled = bool(e.get("enabled", True))

        base_defaults = e.get("defaultOverrides")
        if base_defaults is None:
            # legacy support
            base_defaults = e.get("overrides") or {}

        merged_overrides = copy.deepcopy(base_defaults or {})
        if eid in overrides_map and isinstance(overrides_map[eid], dict):
            merged_overrides = _deep_merge(merged_overrides, overrides_map[eid])

        normalized.append(
            {
                "id": eid,
                "presetId": preset_id,
                "overrides": merged_overrides,
                "enabled": enabled,
            }
        )

    return normalized


def validate_layer_effect_overrides(
    layer_spec: Dict[str, Any],
    effects_library: Dict[str, Any],
) -> List[str]:
    """
    Validates that overrides only target known effect ids and allowed param keys.

    Returns a list of human-readable error strings (empty list => OK).
    """
    errors: List[str] = []

    style_id = _get_style_id(layer_spec)
    raw_effects = layer_spec.get("effects")

    # EXPLICIT mode validation
    if not style_id and isinstance(raw_effects, list) and raw_effects:
        for e in raw_effects:
            if not isinstance(e, dict):
                continue
            eid = e.get("id") or "?"
            preset_id = e.get("presetId")
            allowed = set(_preset_param_keys(effects_library, preset_id))
            overrides = e.get("overrides") or {}
            if not isinstance(overrides, dict):
                continue
            for k in overrides.keys():
                if allowed and k not in allowed:
                    errors.append(f"[explicit] effect '{eid}' preset '{preset_id}': param '{k}' is not in exposedParams")
        return errors

    # STYLE mode validation
    if not style_id:
        return errors

    styles = effects_library.get("semanticStyles") or {}
    style = styles.get(style_id) if isinstance(styles, dict) else None
    if not isinstance(style, dict):
        errors.append(f"unknown effectStyleId '{style_id}'")
        return errors

    stack = style.get("effects") or style.get("stack") or []
    if not isinstance(stack, list):
        return errors

    stack_ids: Dict[str, Dict[str, Any]] = {}
    for idx, se in enumerate(stack):
        if not isinstance(se, dict):
            continue
        sid = se.get("id") or se.get("instanceId") or f"fx_{idx+1}"
        stack_ids[sid] = se

    overrides_map = _collect_overrides_map(layer_spec, style_mode=True)

    # 1) unknown instance ids
    for oid in overrides_map.keys():
        if oid not in stack_ids:
            errors.append(f"[style:{style_id}] override targets unknown instanceId '{oid}'")

    # 2) unknown/forbidden param keys
    for oid, ov in overrides_map.items():
        if oid not in stack_ids:
            continue
        if not isinstance(ov, dict):
            continue
        se = stack_ids[oid]
        allowed_keys = set(_style_effect_allowed_keys(effects_library, se))
        for k in ov.keys():
            if allowed_keys and k not in allowed_keys:
                errors.append(f"[style:{style_id}] '{oid}': param '{k}' not allowed (allowed: {sorted(list(allowed_keys))})")

    return errors


def build_effects_prompt_catalog(effects_library: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact "semantic catalog" view suitable for LLM prompting:

      styleId -> meaning + stack(instanceId/presetId) + allowed params with metadata.

    This lets you avoid sending the entire full-fidelity property trees in prompts.
    """
    out: Dict[str, Any] = {
        "effectsLibraryVersion": effects_library.get("libraryVersion"),
        "styles": {}
    }

    presets = effects_library.get("effectPresets") or {}
    styles = effects_library.get("semanticStyles") or {}
    if not isinstance(presets, dict) or not isinstance(styles, dict):
        return out

    # Build a preset param lookup: presetId -> key -> meta
    preset_param_meta: Dict[str, Dict[str, Any]] = {}
    for pid, p in presets.items():
        if not isinstance(p, dict):
            continue
        meta: Dict[str, Any] = {}
        for ep in (p.get("exposedParams") or []):
            if not isinstance(ep, dict):
                continue
            k = ep.get("key")
            if isinstance(k, str):
                meta[k] = {
                    "type": ep.get("type"),
                    "range": ep.get("range"),
                    "animatable": ep.get("animatable"),
                    "role": ep.get("role"),
                }
        preset_param_meta[pid] = meta

    for sid, s in styles.items():
        if not isinstance(s, dict):
            continue
        stack = s.get("effects") or s.get("stack") or []
        if not isinstance(stack, list):
            continue

        style_entry: Dict[str, Any] = {
            "meaning": s.get("meaning") or {"label": sid},
            "stack": []
        }

        for idx, se in enumerate(stack):
            if not isinstance(se, dict):
                continue
            inst = se.get("id") or se.get("instanceId") or f"fx_{idx+1}"
            pid = se.get("presetId")
            allowed = se.get("allowedParams")
            if not isinstance(allowed, list) or not allowed:
                allowed = _preset_param_keys(effects_library, pid)

            params_view: Dict[str, Any] = {}
            pm = preset_param_meta.get(pid, {})
            for k in allowed:
                if not isinstance(k, str):
                    continue
                params_view[k] = pm.get(k, {})

            style_entry["stack"].append(
                {
                    "instanceId": inst,
                    "presetId": pid,
                    "params": params_view,
                    "overrideGuidance": se.get("overrideGuidance") or {},
                }
            )

        out["styles"][sid] = style_entry

    return out


def build_semantic_prompt_catalog(effects_library: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible alias for build_effects_prompt_catalog."""
    return build_effects_prompt_catalog(effects_library)
