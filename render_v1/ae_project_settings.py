from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Tuple

# Minimal runtime defaults (LLM can override via composition.projectSettings.defaults)
ENV_DEFAULTS: Dict[str, Any] = {
    "duration": 15.0,
    "global_fit_policy": "cover",
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_global_segment(composition: Dict[str, Any]) -> Tuple[float, Optional[float]]:
    ps = composition.get("projectSettings") or {}

    raw_start = composition.get("global_start_sec")
    if raw_start is None:
        raw_start = ps.get("global_start_sec", 0.0)

    try:
        start = float(raw_start or 0.0)
    except (TypeError, ValueError):
        start = 0.0

    raw_end = composition.get("global_end_sec")
    if raw_end is None:
        raw_end = ps.get("global_end_sec")

    try:
        end = float(raw_end) if raw_end is not None else None
    except (TypeError, ValueError):
        end = None

    return start, end


def resolve_runtime_defaults(composition: Dict[str, Any], env_defaults: Dict[str, Any] = ENV_DEFAULTS) -> Dict[str, Any]:
    """Merge runtime defaults + compute duration from global segment if present."""
    ps = composition.get("projectSettings") or {}
    defaults = copy.deepcopy(env_defaults)
    defaults = deep_merge(defaults, ps.get("defaults") or {})

    start, end = resolve_global_segment(composition)
    if end is not None and end > start:
        duration = float(end - start)
        if duration > 0:
            defaults["duration"] = duration

    defaults.setdefault("global_start_sec", start)
    if end is not None:
        defaults.setdefault("global_end_sec", end)

    return defaults


def resolve_comp_fields(
    comp_item: Dict[str, Any],
    project_template_defaults: Dict[str, Any],
    runtime_defaults: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve comp width/height/fps/pixelAspect/duration from template + runtime defaults."""
    dur = float(runtime_defaults.get("duration", 15.0))

    def _pick(name: str, fallback: Any) -> Any:
        """
        Treat explicit nulls from LLM as "missing".
        comp_item may contain {"width": null} which must fallback to template.
        """
        v = comp_item.get(name, None)
        if v is None:
            v = project_template_defaults.get(name, fallback)
        if v is None:
            v = fallback
        return v

    def _to_int(v: Any, fallback: int) -> int:
        try:
            if v is None:
                return int(fallback)
            return int(float(v))
        except (TypeError, ValueError):
            return int(fallback)

    def _to_float(v: Any, fallback: float) -> float:
        try:
            if v is None:
                return float(fallback)
            return float(v)
        except (TypeError, ValueError):
            return float(fallback)

    width_v = _pick("width", 1080)
    height_v = _pick("height", 1080)
    fps_v = _pick("fps", 23.976)
    pixel_aspect_v = _pick("pixelAspect", 1.0)

    duration_v = comp_item.get("duration", None)
    if duration_v is None:
        duration_v = dur

    return {
        "width": _to_int(width_v, int(project_template_defaults.get("width", 1080) or 1080)),
        "height": _to_int(height_v, int(project_template_defaults.get("height", 1080) or 1080)),
        "duration": _to_float(duration_v, dur),
        "fps": _to_float(fps_v, float(project_template_defaults.get("fps", 23.976) or 23.976)),
        "pixelAspect": _to_float(pixel_aspect_v, float(project_template_defaults.get("pixelAspect", 1.0) or 1.0)),
    }

