# render_v1/assembler_core.py
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

TEXT_FX_LIBRARY_PATH = Path("config/styles/text_fx_combos.json")

from .models import Payload
from render_v1.text_fx_logic import apply_text_fx_from_layer_fields, apply_text_fx_plan


def load_json(path: Path) -> dict:
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Decoding {path}: {exc}")
        return {}


def process_layer(
    layer: dict,
    styles_lib: dict,
    presets_lib: dict,
    global_fit_policy: str | None = None,
) -> dict:
    """
    Преобразует слой из composition.json-стиля:
      - styleId + content -> textDocument + стили,
      - presetId -> transform,
      - global_fit_policy -> fitPolicy для ref-слоёв (если локально не задано),
      - если есть inPoint, но нет startTime — ставим startTime = inPoint.
    """
    if "inPoint" in layer and "startTime" not in layer:
        layer["startTime"] = layer["inPoint"]

    # TEXT: styleId + content -> textDocument
    if layer.get("type") == "text" and "styleId" in layer:
        sid = layer["styleId"]
        if sid in styles_lib:
            style_props = copy.deepcopy(styles_lib[sid])
            if "textDocument" not in layer:
                layer["textDocument"] = {}
            for k, v in style_props.items():
                if k not in layer["textDocument"]:
                    layer["textDocument"][k] = v

            content = layer.get("content") or layer.get("text")
            if content:
                layer["textDocument"]["text"] = content
            if "content" in layer:
                del layer["content"]
            if "text" in layer and not isinstance(layer["text"], dict):
                del layer["text"]
        del layer["styleId"]

    # PRESET: presetId -> transform
    if "presetId" in layer:
        pid = layer["presetId"]
        if pid in presets_lib:
            preset_data = presets_lib[pid]
            if "transform" in preset_data:
                preset_transform = copy.deepcopy(preset_data["transform"])
                if "transform" not in layer:
                    layer["transform"] = {}
                for k, v in preset_transform.items():
                    if k not in layer["transform"]:
                        layer["transform"][k] = v
        del layer["presetId"]

    # FIT POLICY: глобальный флаг, если локально не указан
    if layer.get("type") == "ref" and global_fit_policy and "fitPolicy" not in layer:
        layer["fitPolicy"] = global_fit_policy

    return layer


def apply_defaults(item: dict, defaults: dict) -> dict:
    """
    Если в item (композиции) не хватает полей width/height/fps/pixelAspect/duration,
    берём их из defaults.
    """
    if item.get("type") == "comp":
        for key, val in defaults.items():
            if key not in item:
                item[key] = val
    return item


def build_project_payload_from_composition(
    styles_path: Path,
    presets_path: Path,
    composition: dict,
    entry_point: str = "comp_main",
) -> Tuple[Dict[str, Any], str]:
    """
    Общий ассемблер:
      - читает text_styles/footage_presets,
      - применяет defaults к comp-айтемам,
      - разруливает styleId/presetId/fitPolicy,
      - собирает Payload (PROJECT_DATA) и валидирует его.
    """
    styles_data = load_json(styles_path)
    presets_data = load_json(presets_path)

    project_settings = composition.get("projectSettings", {}) or {}
    defaults = project_settings.get("defaults", {}) or {}
    # Если fitPolicy не задан в composition.json, по умолчанию считаем COVER.
    # Иначе ref-слои останутся без fitPolicy, и движок в job_template.jsx
    # не применит авто-скейл: футаж просто «впишется» в композицию.
    #
    # Нам же нужно, чтобы вертикальные клипы кадрировали фон (cover),
    # даже если модель промолчала про fitPolicy.
    global_fit_policy = project_settings.get("fitPolicy") or "cover"

    print(f"Applying Defaults: {defaults}")
    if global_fit_policy:
        print(f"Global fitPolicy: {global_fit_policy}")

    final_items: List[dict] = []
    raw_items = composition.get("items", []) or []

    for item in raw_items:
        item = apply_defaults(item, defaults)

        if item.get("type") == "comp" and "layers" in item:
            processed_layers = []
            for layer in item["layers"]:
                processed_layer = process_layer(
                    layer, styles_data, presets_data, global_fit_policy=global_fit_policy
                )
                processed_layers.append(processed_layer)
            item["layers"] = processed_layers

        final_items.append(item)

    # Optional: Text FX expansion (text layer effect stacks + text animators)
    try:
        text_fx_lib = load_json(TEXT_FX_LIBRARY_PATH)
        applied_layers = apply_text_fx_from_layer_fields(
            final_items, text_fx_library=text_fx_lib, cleanup=True
        )
        text_fx_plan = composition.get("textFxPlan") or project_settings.get("textFxPlan")
        if isinstance(text_fx_plan, dict):
            applied_layers += apply_text_fx_plan(
                final_items, plan=text_fx_plan, text_fx_library=text_fx_lib, cleanup=False
            )
        if applied_layers:
            print(f"[text_fx] applied combos for {applied_layers} text layers")
    except Exception as exc:  # noqa: BLE001
        print(f"[text_fx] WARNING: failed to apply text fx: {exc}")

    raw_payload: Dict[str, Any] = {
        "project": {
            "projectName": project_settings.get("name", "Auto Build"),
            "items": final_items,
        },
        "entryPoint": entry_point,
    }

    model = Payload(**raw_payload)
    json_str = model.model_dump_json(indent=2, exclude_none=True)
    return raw_payload, json_str
