from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from render_v1.models import Payload

# -----------------------------
# Paths
# -----------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
STYLES_DIR = REPO_ROOT / "config" / "styles"

TEXT_STYLES_PATH = STYLES_DIR / "text_styles.json"
FOOTAGE_PRESETS_PATH = STYLES_DIR / "footage_presets.json"
TEXT_MOTION_LIBRARY_PATH = STYLES_DIR / "text_motion_library.json"
PROJECT_SETTINGS_TEMPLATE_PATH = STYLES_DIR / "project_settings_template.json"

# -----------------------------
# Defaults (can be overridden by composition['projectSettings']['defaults'])
# -----------------------------

ENV_DEFAULTS: Dict[str, Any] = {
    "duration": 15.0,
    "global_fit_policy": "cover",
}


# -----------------------------
# Utilities
# -----------------------------


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Decoding {path}: {exc}")
        return {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _is_value_data_dict(v: Any) -> bool:
    return isinstance(v, dict) and (
        "keys" in v
        or "expression" in v
        or "value" in v
        or "procedural" in v
    )


_SEG_RE = re.compile(r"^(?P<name>.+?)(?:\[(?P<idx>\d+)\])?$")


def _parse_segment(seg: str) -> Tuple[str, Optional[int]]:
    """Supports indices like: "ADBE Text Animator[2]" (1-based)."""
    seg = (seg or "").strip()
    m = _SEG_RE.match(seg)
    if not m:
        return seg, None
    name = (m.group("name") or "").strip()
    idx = m.group("idx")
    return name, (int(idx) if idx is not None else None)


def _normalize_property_tree(node: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize preset trees: convert nested group dicts under 'properties' into child nodes.

    This lets the JSX engine apply the whole tree uniformly via matchName navigation.
    """
    if not isinstance(node, dict):
        return node

    children = list(node.get("children") or [])
    props = dict(node.get("properties") or {})

    new_props: Dict[str, Any] = {}
    for k, v in props.items():
        if isinstance(v, dict) and not _is_value_data_dict(v):
            # treat as nested group
            children.append({
                "matchName": k,
                "properties": v,
            })
        else:
            new_props[k] = v

    node["properties"] = new_props if new_props else None
    node["children"] = children if children else None

    # recurse
    if node.get("children"):
        node["children"] = [_normalize_property_tree(c) for c in node["children"]]

    # clean None
    if node.get("properties") is None:
        node.pop("properties", None)
    if node.get("children") is None:
        node.pop("children", None)

    return node


def _tree_find_or_create_child(node: Dict[str, Any], match_name: str, index_1based: int) -> Dict[str, Any]:
    """Return the N-th (1-based) child with the given matchName, creating siblings if needed."""
    if index_1based < 1:
        index_1based = 1

    children = node.setdefault("children", [])
    hits = [ch for ch in children if isinstance(ch, dict) and ch.get("matchName") == match_name]

    while len(hits) < index_1based:
        new_ch = {"matchName": match_name}
        children.append(new_ch)
        hits.append(new_ch)

    return hits[index_1based - 1]


def _tree_set_value(root: Dict[str, Any], match_name_path: str, value: Any) -> None:
    """Set value in a matchName-based tree at a given path (supports [index] segments)."""
    segs = [s for s in (match_name_path or "").split("/") if s]
    if not segs:
        return

    node = root
    root_name, _root_idx = _parse_segment(segs[0])
    if node.get("matchName") == root_name:
        segs = segs[1:]

    if not segs:
        return

    for seg in segs[:-1]:
        name, idx = _parse_segment(seg)
        node = _tree_find_or_create_child(node, name, idx or 1)

    leaf_name, _leaf_idx = _parse_segment(segs[-1])
    props = node.setdefault("properties", {})
    props[leaf_name] = value


def _expand_procedural(value_data: Any, *, layer_in: float, layer_out: float, fps: float) -> Any:
    """If value_data contains a 'procedural' spec, bake it to concrete keyframes."""
    if not isinstance(value_data, dict) or "procedural" not in value_data:
        return value_data

    spec = value_data.get("procedural") or {}
    kind = spec.get("kind")

    start = float(spec.get("startTime", layer_in))
    end = float(spec.get("endTime", layer_out))
    if end < start:
        start, end = end, start

    if kind == "ramp":
        v0 = spec.get("from", 0)
        v1 = spec.get("to", 100)
        tpl = spec.get("templateRef")
        return {
            "keys": [
                {"time": start, "value": v0, **({"templateRef": tpl} if tpl else {})},
                {"time": end, "value": v1, **({"templateRef": tpl} if tpl else {})},
            ]
        }

    if kind == "normalized_curve":
        pts = spec.get("points") or []
        tpl = spec.get("templateRef")
        dur = max(1e-6, end - start)
        keys = []
        for p in pts:
            t_norm = float(p.get("t", 0))
            v = p.get("value")
            t_abs = start + t_norm * dur
            k = {"time": t_abs, "value": v}
            if tpl:
                k["templateRef"] = tpl
            keys.append(k)
        return {"keys": keys}

    if kind == "oscillate":
        wave = spec.get("wave", "sine")
        freq = float(spec.get("frequencyHz", 2.0))
        amp = float(spec.get("amplitude", 10.0))
        off = float(spec.get("offset", 0.0))
        phase = float(spec.get("phase", 0.0))
        sample_rate = float(spec.get("sampleRate", fps))
        tpl = spec.get("templateRef")

        step = 1.0 / max(1.0, sample_rate)
        t = start
        keys = []
        while t <= end + 1e-6:
            x = 2.0 * math.pi * freq * (t - start) + phase
            if wave == "triangle":
                # triangle in [-1, 1]
                tri = 2.0 * abs(2.0 * ((x / (2.0 * math.pi)) % 1.0) - 1.0) - 1.0
                v = off + amp * tri
            else:
                v = off + amp * math.sin(x)

            k = {"time": t, "value": v}
            if tpl:
                k["templateRef"] = tpl
            keys.append(k)
            t += step

        return {"keys": keys}

    # Unknown procedural kind -> passthrough (keeps raw for debugging)
    return value_data


def _resolve_preset_tree(
    preset: Dict[str, Any],
    overrides: Dict[str, Any],
    *,
    layer_in: float,
    layer_out: float,
    fps: float,
) -> Optional[Dict[str, Any]]:
    tree = preset.get("propertyTree")
    if not tree:
        return None

    tree = _normalize_property_tree(copy.deepcopy(tree))

    exposed = preset.get("exposedParams") or []
    # exposedParams can be a list[{key, matchNamePath}] or dict[key]=matchNamePath
    mapping: List[Tuple[str, str]] = []
    if isinstance(exposed, list):
        for rec in exposed:
            if not isinstance(rec, dict):
                continue
            k = rec.get("key")
            p = rec.get("matchNamePath")
            if k and p:
                mapping.append((str(k), str(p)))
    elif isinstance(exposed, dict):
        for k, p in exposed.items():
            mapping.append((str(k), str(p)))

    for k, path in mapping:
        if k not in overrides:
            continue
        v = overrides[k]
        v = _expand_procedural(v, layer_in=layer_in, layer_out=layer_out, fps=fps)
        _tree_set_value(tree, path, v)

    return tree


# -----------------------------
# Main public API
# -----------------------------


def build_project_payload_from_composition(composition: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Transforms LLM composition.json -> PROJECT_DATA (AE engine JSON).

    Returns:
        raw_payload (dict) and pretty JSON string.
    """
    text_styles = _load_json(TEXT_STYLES_PATH)
    footage_presets = _load_json(FOOTAGE_PRESETS_PATH)
    motion_lib = _load_json(TEXT_MOTION_LIBRARY_PATH)

    key_templates = motion_lib.get("keyTemplates") or {}
    text_anim_presets = motion_lib.get("textAnimPresets") or {}
    transform_presets = motion_lib.get("transformPresets") or {}

    project_settings_tpl = _load_json(PROJECT_SETTINGS_TEMPLATE_PATH)
    proj_defaults = project_settings_tpl.get("defaults", {}) if isinstance(project_settings_tpl, dict) else {}

    # Defaults
    defaults = copy.deepcopy(ENV_DEFAULTS)
    defaults = _deep_merge(defaults, (composition.get("projectSettings") or {}).get("defaults") or {})
    duration = float(defaults.get("duration", 15.0))
    global_fit_policy = str(defaults.get("global_fit_policy", "cover"))

    # Compose project items
    items_out: List[Dict[str, Any]] = []

    # Pass through footage items first
    for it in composition.get("items", []):
        if it.get("type") != "footage":
            continue
        items_out.append({
            "id": it["id"],
            "type": "footage",
            "name": it.get("name", it["id"]),
            "path": it["path"],
            "isRef": bool(it.get("isRef", False)),
        })

    # Comps
    for it in composition.get("items", []):
        if it.get("type") != "comp":
            continue

        comp_id = it["id"]
        comp_name = it.get("name", comp_id)

        comp_duration = float(it.get("duration", duration))
        comp_fps = float(it.get("fps", proj_defaults.get("fps", 23.976)))

        comp_conf = {
            "id": comp_id,
            "type": "comp",
            "name": comp_name,
            "width": int(it.get("width", proj_defaults.get("width", 1080))),
            "height": int(it.get("height", proj_defaults.get("height", 1080))),
            "duration": comp_duration,
            "fps": comp_fps,
            "pixelAspect": float(it.get("pixelAspect", proj_defaults.get("pixelAspect", 1.0))),
            "layers": [],
        }

        # Layers
        for layer in it.get("layers", []):
            ltype = layer.get("type")
            base = {
                "type": ltype,
                "name": layer.get("name"),
                "inPoint": layer.get("inPoint"),
                "outPoint": layer.get("outPoint"),
                "startTime": layer.get("startTime"),
                "enabled": layer.get("enabled", True),
                "audioEnabled": layer.get("audioEnabled"),
                "transform": layer.get("transform"),  # legacy direct transform dict
            }

            if ltype == "ref":
                # Apply footage preset if provided
                preset_id = layer.get("presetId")
                if preset_id and preset_id in footage_presets:
                    base = _deep_merge(base, footage_presets[preset_id])

                # Ensure fitPolicy exists (engine uses it for scaling)
                if "fitPolicy" not in base or base["fitPolicy"] is None:
                    base["fitPolicy"] = layer.get("fitPolicy") or global_fit_policy

                base["refId"] = layer["refId"]
                base["presetId"] = preset_id

            elif ltype == "adjustment":
                # For now: no extra processing; user can add effects later
                pass

            elif ltype == "text":
                style_id = layer.get("styleId") or "main_subtitle"
                content = layer.get("content") if layer.get("content") is not None else layer.get("text", "")

                style_doc = copy.deepcopy(text_styles.get(style_id, {}))
                style_doc["text"] = content

                base["styleId"] = style_id
                base["content"] = content
                base["textDocument"] = style_doc

                overrides = layer.get("overrides") or {}

                # Transform preset -> transformTree
                transform_id = layer.get("transformId") or layer.get("textTransformId")
                if transform_id:
                    preset = transform_presets.get(transform_id)
                    if preset:
                        tr_tree = _resolve_preset_tree(
                            preset, overrides,
                            layer_in=float(base.get("inPoint") or 0.0),
                            layer_out=float(base.get("outPoint") or comp_duration),
                            fps=comp_fps,
                        )
                        if tr_tree:
                            base["transformTree"] = tr_tree
                    base["transformId"] = transform_id  # keep for trace/debug

                # Text anim preset -> textAnimTree
                anim_id = layer.get("animId") or layer.get("textAnimId")
                if anim_id:
                    preset = text_anim_presets.get(anim_id)
                    if preset:
                        ta_tree = _resolve_preset_tree(
                            preset, overrides,
                            layer_in=float(base.get("inPoint") or 0.0),
                            layer_out=float(base.get("outPoint") or comp_duration),
                            fps=comp_fps,
                        )
                        if ta_tree:
                            base["textAnimTree"] = ta_tree
                    base["animId"] = anim_id  # keep for trace/debug

            remember_layer = base

            # Normalize startTime for non-audio footage layers: if LLM set differently, align to inPoint
            if remember_layer.get("type") == "ref" and remember_layer.get("refId") != "audio_main":
                if remember_layer.get("startTime") is not None and remember_layer.get("inPoint") is not None:
                    remember_layer["startTime"] = remember_layer["inPoint"]

            comp_conf["layers"].append(remember_layer)

        items_out.append(comp_conf)

    raw_payload: Dict[str, Any] = {
        "project": {
            "projectName": composition.get("projectName", project_settings_tpl.get("projectName", "AE Project")),
            "items": items_out,
        },
        "entryPoint": composition.get("entryPoint", "comp_main"),
        "libraries": {
            "keyTemplates": key_templates,
        },
    }

    payload = Payload(**raw_payload)
    json_str = payload.model_dump_json(indent=2, exclude_none=True)

    return raw_payload, json_str
