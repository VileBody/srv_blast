# src/render/ae/compiler/build_payload.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Config
from .tag_styles import ensure_tag_adjustment_layers, apply_tag_styles
from .tag_baked_apply import apply_tag_baked_to_layers
from src.render.ae.contracts.payload import Payload
from src.config.styles.paths import get_style_paths
from src.core.config.style_loader import get_tags_catalog

cfg = Config.from_env()
log = logging.getLogger(__name__)

# Геометрия проекта задаётся конфигом/шаблоном, а не моделью
ENV_DEFAULTS: Dict[str, Any] = {
    "width": getattr(cfg, "target_width", 1080),
    "height": getattr(cfg, "target_height", 1920),
    "pixelAspect": 1.0,
    "fps": float(getattr(cfg, "target_fps", 23.976)),
}

# fallback-длительность, если модель не указала отрезок
DEFAULT_DURATION: float = 15.0


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
    *,
    global_fit_policy: str | None = None,
) -> dict:
    """
    ae_presets-only:
      - гарантируем startTime
      - text: content -> textDocument.text (остальные поля придут из tagBaked apply)
      - ref: fitPolicy default
      - presetId/styleId/effectStyleId игнорируем
    """
    if "inPoint" in layer and "startTime" not in layer:
        layer["startTime"] = layer["inPoint"]

    if layer.get("type") == "text":
        content = layer.get("content")
        if content is None and isinstance(layer.get("text"), str):
            content = layer.get("text")
        if content is None:
            content = ""

        if not isinstance(layer.get("textDocument"), dict):
            layer["textDocument"] = {"text": str(content)}
        else:
            layer["textDocument"].setdefault("text", str(content))

        layer.pop("content", None)
        if "text" in layer and not isinstance(layer["text"], dict):
            layer.pop("text", None)

    if "presetId" in layer:
        layer.pop("presetId", None)

    if layer.get("type") == "ref" and global_fit_policy and "fitPolicy" not in layer:
        layer["fitPolicy"] = global_fit_policy

    for key in ("styleId", "effectStyleId", "effectsStyleId", "fxStyleId", "effectOverrides", "effectsOverrides", "fxOverrides"):
        layer.pop(key, None)

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
    composition: dict,
    entry_point: str = "comp_main",
    *,
    style_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Общий ассемблер:
      - резолвит style root по style_id (или авто),
      - собирает Payload (PROJECT_DATA) и валидирует его.
    """
    project_settings = composition.get("projectSettings", {}) or {}
    inferred_style_id = (
        style_id
        or project_settings.get("styleId")
        or composition.get("styleId")
        or None
    )

    paths = get_style_paths(inferred_style_id)
    ps_defaults = project_settings.get("defaults") or {}

    raw_global_start = composition.get("global_start_sec")
    if raw_global_start is None:
        raw_global_start = project_settings.get("global_start_sec", 0.0)

    try:
        global_start_sec = float(raw_global_start or 0.0)
    except (TypeError, ValueError):
        global_start_sec = 0.0

    raw_global_end = composition.get("global_end_sec")
    if raw_global_end is None:
        raw_global_end = project_settings.get("global_end_sec")

    try:
        global_end_sec: float | None = (
            float(raw_global_end) if raw_global_end is not None else None
        )
    except (TypeError, ValueError):
        global_end_sec = None

    audio_ref_id = project_settings.get("audioRefId", "audio_main")

    if global_end_sec is not None and global_end_sec > global_start_sec:
        duration = float(global_end_sec - global_start_sec)
        if duration <= 0:
            duration = DEFAULT_DURATION
    else:
        duration = float(ps_defaults.get("duration", DEFAULT_DURATION))

    base_defaults = dict(ENV_DEFAULTS)

    defaults: Dict[str, Any] = {}
    for key, value in base_defaults.items():
        defaults[key] = value

    for key, value in ps_defaults.items():
        if key in {"width", "height", "pixelAspect", "fps", "duration"}:
            continue
        defaults.setdefault(key, value)

    defaults["duration"] = duration

    global_fit_policy = project_settings.get("fitPolicy") or "cover"

    print(f"Applying Defaults: {defaults}")
    if global_fit_policy:
        print(f"Global fitPolicy: {global_fit_policy}")
    print(
        f"Global segment: start={global_start_sec}, end={global_end_sec}, duration={duration}"
    )

    final_items: List[dict] = []
    raw_items = composition.get("items", []) or []

    for item in raw_items:
        item = apply_defaults(item, defaults)

        if item.get("type") == "comp" and "layers" in item:
            processed_layers = []
            for layer in item["layers"]:
                processed_layer = process_layer(
                    layer,
                    global_fit_policy=global_fit_policy,
                )
                processed_layers.append(processed_layer)
            item["layers"] = processed_layers

        final_items.append(item)

    # ---------------------------
    # TAG-BASED STYLES (optional)
    # ---------------------------
    tags_catalog = get_tags_catalog(style_id=inferred_style_id) or {}
    if tags_catalog:
        try:
            ensure_tag_adjustment_layers(final_items, tags_catalog)
            apply_tag_styles(
                final_items,
                tags_catalog,
                fps=float(defaults.get("fps") or ENV_DEFAULTS.get("fps") or 23.976),
                global_start_sec=global_start_sec,
                style_id=inferred_style_id,
            )
            apply_tag_baked_to_layers(final_items, style_id=inferred_style_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[assembler] tag styles failed: %s", exc)

    # ae_presets-only: no legacy text_fx/motion/effects stages

    # сдвигаем аудио-слой в главной композиции по глобальному старту
    for item in final_items:
        if (item.get("type") or "").lower() != "comp":
            continue
        if item.get("id") != entry_point:
            continue

        for layer in item.get("layers") or []:
            if layer.get("type") == "ref" and layer.get("refId") == audio_ref_id:
                if global_start_sec > 0.0:
                    layer["startTime"] = -global_start_sec

                layer["enabled"] = True
                if layer.get("audioEnabled") is not False:
                    layer["audioEnabled"] = True

                layer.setdefault("inPoint", 0.0)
                layer.setdefault("outPoint", duration)
                break
        break

    # 5) Нормализуем startTime для видеофутажей: ожидаем, что startTime == inPoint
    #    (кроме аудио-рефа, который смещается отдельно). Если LLM выставил другое
    #    значение, принудительно приводим к inPoint и логируем предупреждение.
    for item in final_items:
        if (item.get("type") or "").lower() != "comp":
            continue

        for layer in item.get("layers") or []:
            if layer.get("type") != "ref":
                continue

            if layer.get("refId") == audio_ref_id:
                continue

            if "inPoint" not in layer:
                continue

            in_point = layer["inPoint"]
            current_start = layer.get("startTime")

            if current_start is None or current_start == in_point:
                continue

            print(
                f"[ASSEMBLER] WARNING: ref layer {layer.get('refId')} has startTime={current_start} "
                f"!= inPoint={in_point}, overriding startTime to inPoint"
            )
            layer["startTime"] = in_point

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
