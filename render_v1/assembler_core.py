# render_v1/assembler_core.py
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import Config
from .models import Payload

cfg = Config.from_env()

# Базовые дефолты на случай, если модель что-то не допишет
BASE_DEFAULTS: Dict[str, Any] = {
    "width":        getattr(cfg, "target_width", 1080),
    "height":       getattr(cfg, "target_height", 1920),
    "pixelAspect":  1.0,
    "fps":          float(getattr(cfg, "target_fps", 23.976)),
    "duration":     15.0,
}


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
      - presetId -> transform (с учётом fitPolicy),
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

        # если есть глобальный fitPolicy и это ref-слой — не тащим transform из пресета,
        # чтобы не мешать автоматическому cover/contain в движке
        use_preset_transform = not (
            layer.get("type") == "ref" and global_fit_policy is not None
        )

        if use_preset_transform and pid in presets_lib:
            preset_data = presets_lib[pid]
            if "transform" in preset_data:
                preset_transform = copy.deepcopy(preset_data["transform"])
                if "transform" not in layer:
                    layer["transform"] = {}
                for k, v in preset_transform.items():
                    if k not in layer["transform"]:
                        layer["transform"][k] = v

        # presetId нам дальше не нужен
        del layer["presetId"]

    # FIT POLICY: глобальный флаг, если локально не указан
    if layer.get("type") == "ref" and global_fit_policy and "fitPolicy" not in layer:
        layer["fitPolicy"] = global_fit_policy

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
      - берёт projectSettings.defaults и дополняет базовыми дефолтами,
      - разруливает styleId/presetId/fitPolicy,
      - собирает Payload (PROJECT_DATA) и валидирует его.
    """
    styles_data = load_json(styles_path)
    presets_data = load_json(presets_path)

    project_settings = composition.get("projectSettings", {}) or {}
    defaults = dict(BASE_DEFAULTS)
    defaults.update(project_settings.get("defaults") or {})

    # если fitPolicy не указан — по умолчанию считаем cover
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
                    layer,
                    styles_lib=styles_data,
                    presets_lib=presets_data,
                    global_fit_policy=global_fit_policy,
                )
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

    # Валидация strict-моделью Payload, плюс диагностика
    try:
        model = Payload(**raw_payload)
    except Exception as exc:  # noqa: BLE001
        from pprint import pprint

        print("[ASSEMBLER] Failed to validate project payload:")
        print(type(exc), exc)
        if hasattr(exc, "errors"):
            try:
                pprint(exc.errors())
            except Exception:
                pass
        try:
            print("[ASSEMBLER] Raw payload:")
            print(json.dumps(raw_payload, ensure_ascii=False, indent=2))
        except Exception:
            pass
        raise

    json_str = model.model_dump_json(indent=2, exclude_none=True)
    return raw_payload, json_str
