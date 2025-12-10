from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import Payload


def load_json(path: Path) -> dict:
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Decoding {path}: {e}")
        return {}


def process_layer(layer: dict, styles_lib: dict, presets_lib: dict) -> dict:
    """
    Преобразует слой из composition.json-стиля:
      - styleId + content → textDocument + стили,
      - presetId → transform,
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

    return layer


def apply_defaults(item: dict, defaults: dict) -> dict:
    """
    Если в item (композиции) не хватает полей width/height/fps/pixelAspect/duration,
    берём их из defaults (как в composition.json).
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
      - разруливает styleId/presetId в слоях,
      - собирает Payload (PROJECT_DATA) и валидирует его.

    Возвращает:
      raw_payload (dict) и json_str (готовый JSON для инъекции в JSX).
    """
    styles_data = load_json(styles_path)
    presets_data = load_json(presets_path)

    project_settings = composition.get("projectSettings", {})
    defaults = project_settings.get("defaults", {})

    final_items: List[dict] = []
    raw_items = composition.get("items", [])

    for item in raw_items:
        item = apply_defaults(item, defaults)

        if item.get("type") == "comp" and "layers" in item:
            processed_layers = []
            for layer in item["layers"]:
                processed_layer = process_layer(layer, styles_data, presets_data)
                processed_layers.append(processed_layer)
            item["layers"] = processed_layers

        final_items.append(item)

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
