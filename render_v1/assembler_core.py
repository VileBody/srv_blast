from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from render_v1.ae_project_settings import (
    ENV_DEFAULTS,
    deep_merge,
    resolve_comp_fields,
    resolve_runtime_defaults,
)
from render_v1.ae_text_document import build_text_document
from render_v1.models import Payload
from render_v1.motion_resolver import resolve_preset_tree
from render_v1.style_pack import load_style_pack


# -----------------------------
# Main public API
# -----------------------------


def build_project_payload_from_composition(
    composition: Dict[str, Any],
    **kwargs: Any,
) -> Tuple[Dict[str, Any], str]:
    return build_project_payload_from_composition_v2(composition=composition, **kwargs)


def build_project_payload_from_composition_v2(
    *,
    composition: Dict[str, Any],
    # Backward-compat kwargs (старый planner.py их всё ещё передаёт)
    styles_path: Optional[Path] = None,
    presets_path: Optional[Path] = None,
    # Optional overrides
    motion_library_path: Optional[Path] = None,
    project_settings_template_path: Optional[Path] = None,
    style_pack: Optional[str] = None,
    entry_point: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """Transforms LLM composition.json -> PROJECT_DATA (AE engine JSON)."""

    ps = composition.get("projectSettings") or {}
    style_pack_name = style_pack or ps.get("stylePack")
    pack = load_style_pack(
        style_pack=style_pack_name,
        text_styles_path=styles_path,
        footage_presets_path=presets_path,
        text_motion_library_path=motion_library_path,
        project_settings_template_path=project_settings_template_path,
    )

    text_styles = pack.text_styles
    footage_presets = pack.footage_presets
    key_templates = pack.key_templates
    text_anim_presets = pack.text_anim_presets
    transform_presets = pack.transform_presets
    proj_defaults = pack.project_defaults

    # Defaults
    runtime_defaults = resolve_runtime_defaults(composition, ENV_DEFAULTS)
    duration = float(runtime_defaults.get("duration", 15.0))
    global_fit_policy = str(runtime_defaults.get("global_fit_policy", "cover"))

    global_start_sec = float(runtime_defaults.get("global_start_sec", 0.0))
    audio_ref_id = ps.get("audioRefId", "audio_main")
    entry_comp_id = composition.get("entryPoint") or entry_point or "comp_main"

    # Compose project items
    items_out: List[Dict[str, Any]] = []

    # Pass through footage items first
    for it in composition.get("items", []):
        if it.get("type") != "footage":
            continue
        items_out.append(
            {
                "id": it["id"],
                "type": "footage",
                "name": it.get("name", it["id"]),
                "path": it["path"],
                "isRef": bool(it.get("isRef", False)),
            }
        )

    # Comps
    for it in composition.get("items", []):
        if it.get("type") != "comp":
            continue

        comp_id = it["id"]
        comp_name = it.get("name", comp_id)

        comp_fields = resolve_comp_fields(it, proj_defaults, runtime_defaults)
        comp_duration = float(comp_fields["duration"])
        comp_fps = float(comp_fields["fps"])

        comp_conf = {
            "id": comp_id,
            "type": "comp",
            "name": comp_name,
            "width": int(comp_fields["width"]),
            "height": int(comp_fields["height"]),
            "duration": float(comp_fields["duration"]),
            "fps": float(comp_fields["fps"]),
            "pixelAspect": float(comp_fields["pixelAspect"]),
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

            # Default: if inPoint exists and startTime is not explicitly set -> startTime=inPoint
            if base.get("inPoint") is not None and base.get("startTime") is None:
                base["startTime"] = base["inPoint"]

            if ltype == "ref":
                base["refId"] = layer.get("refId") or ""

                # Apply footage preset if provided
                preset_id = layer.get("presetId")
                if preset_id and preset_id in footage_presets:
                    # preset -> base (layer overrides win)
                    base = deep_merge(copy.deepcopy(footage_presets[preset_id]), base)

                # Ensure fitPolicy exists (engine uses it for scaling)
                if "fitPolicy" not in base or base["fitPolicy"] is None:
                    base["fitPolicy"] = layer.get("fitPolicy") or global_fit_policy

                base["presetId"] = preset_id

                # Audio alignment: если это audio_main, сдвигаем startTime = -global_start_sec
                # чтобы 0.0 на таймлайне совпал с global_start_sec в исходном треке.
                if base.get("refId") == "audio_main":
                    if base.get("enabled") is None:
                        base["enabled"] = True
                    if base.get("audioEnabled") is None:
                        base["audioEnabled"] = True
                    if base.get("inPoint") is None:
                        base["inPoint"] = 0.0
                    if base.get("outPoint") is None:
                        base["outPoint"] = comp_duration
                    if global_start_sec:
                        base["startTime"] = -float(global_start_sec)

            elif ltype == "adjustment":
                # For now: no extra processing; user can add effects later
                pass

            elif ltype == "text":
                style_id = layer.get("styleId") or "main_subtitle"
                content = layer.get("content") if layer.get("content") is not None else layer.get("text", "")

                style_doc = build_text_document(text_styles, style_id, content)

                base["styleId"] = style_id
                base["content"] = content
                base["textDocument"] = style_doc

                # Overrides (LLM -> preset resolver)
                # LLM-friendly path: animParams/transformParams (ABS time) -> convert to procedural overrides.
                overrides = copy.deepcopy(layer.get("overrides") or {})

                anim_params = layer.get("animParams") or {}
                if isinstance(anim_params, dict) and "selector_start" not in overrides:
                    kind = anim_params.get("kind")
                    if kind == "reveal_ramp_abs":
                        overrides["selector_start"] = {
                            "procedural": {
                                "kind": "selector_ramp_abs",
                                "t_start": anim_params.get("t_start"),
                                "t_end": anim_params.get("t_end"),
                                "from": anim_params.get("from", 0),
                                "to": anim_params.get("to", 100),
                                "tpl_start": anim_params.get("tpl_start", "tpl_ease_explosive"),
                                "tpl_end": anim_params.get("tpl_end", "tpl_opacity_fade_end_fast"),
                            }
                        }
                    elif kind == "reveal_steps_3_abs":
                        overrides["selector_start"] = {
                            "procedural": {
                                "kind": "selector_steps_3_abs",
                                "t0": anim_params.get("t0"),
                                "t1": anim_params.get("t1"),
                                "t2": anim_params.get("t2"),
                                "v0": anim_params.get("v0", 25),
                                "v1": anim_params.get("v1", 50),
                                "v2": anim_params.get("v2", 100),
                                "tpl0": anim_params.get("tpl0", "tpl_linear_hold"),
                                "tpl1": anim_params.get("tpl1", "tpl_ease_explosive"),
                                "tpl2": anim_params.get("tpl2", "tpl_ease_explosive"),
                            }
                        }

                transform_params = layer.get("transformParams") or {}
                if isinstance(transform_params, dict) and "opacity" not in overrides:
                    if transform_params.get("kind") == "opacity_fade_abs":
                        overrides["opacity"] = {
                            "procedural": {
                                "kind": "opacity_fade_abs",
                                "t_start": transform_params.get("t_start"),
                                "t_end": transform_params.get("t_end"),
                                "from": transform_params.get("from", 100),
                                "to": transform_params.get("to", 0),
                                "tpl_start": transform_params.get("tpl_start", "tpl_fade_out"),
                                "tpl_end": transform_params.get("tpl_end", "tpl_fade_in_stop"),
                            }
                        }

                base["overrides"] = overrides

                # Transform preset -> transformTree
                transform_id = layer.get("transformId") or layer.get("textTransformId")
                if transform_id:
                    preset = transform_presets.get(transform_id)
                    if preset:
                        tr_tree = resolve_preset_tree(
                            preset,
                            overrides,
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
                        ta_tree = resolve_preset_tree(
                            preset,
                            overrides,
                            layer_in=float(base.get("inPoint") or 0.0),
                            layer_out=float(base.get("outPoint") or comp_duration),
                            fps=comp_fps,
                        )
                        if ta_tree:
                            base["textAnimTree"] = ta_tree
                    base["animId"] = anim_id  # keep for trace/debug

            comp_conf["layers"].append(base)

        items_out.append(comp_conf)

    # Shift audio layer in entry comp using global_start_sec
    for item in items_out:
        if (item.get("type") or "").lower() != "comp":
            continue
        if item.get("id") != entry_comp_id:
            continue

        for layer in item.get("layers") or []:
            if layer.get("type") != "ref":
                continue
            if layer.get("refId") != audio_ref_id:
                continue

            # Align audio so timeline 0.0 == global_start_sec in source track
            if global_start_sec:
                layer["startTime"] = -float(global_start_sec)

            layer["enabled"] = True
            layer["audioEnabled"] = True
            layer.setdefault("inPoint", 0.0)
            layer.setdefault("outPoint", duration)
            break
        break

    raw_payload: Dict[str, Any] = {
        "project": {
            "projectName": (
                ps.get("name")
                or composition.get("projectName")
                or (
                    pack.project_settings_template.get("projectName")
                    if isinstance(pack.project_settings_template, dict)
                    else "AE Project"
                )
                or "AE Project"
            ),
            "items": items_out,
        },
        "entryPoint": entry_comp_id,
        "libraries": {
            "keyTemplates": key_templates,
        },
    }

    payload = Payload(**raw_payload)
    json_str = payload.model_dump_json(indent=2, exclude_none=True)

    return raw_payload, json_str

