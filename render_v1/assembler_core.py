# render_v1/assembler_core.py
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import Config
from render_v1.effects_logic import resolve_effect_stack
from .models import Payload

cfg = Config.from_env()

# Геометрия проекта задаётся конфигом/шаблоном, а не моделью
ENV_DEFAULTS: Dict[str, Any] = {
    "width": getattr(cfg, "target_width", 1080),
    "height": getattr(cfg, "target_height", 1920),
    "pixelAspect": 1.0,
    "fps": float(getattr(cfg, "target_fps", 23.976)),
}

# fallback-длительность, если модель не указала отрезок
DEFAULT_DURATION: float = 15.0


EFFECTS_LIBRARY_PATH = Path("config/styles/effects_library.json")


def load_json(path: Path) -> dict:
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Decoding {path}: {exc}")
        return {}


# project_settings_template.json по аналогии с text_styles / footage_presets
PROJECT_SETTINGS_TEMPLATE_PATH = Path("config/styles/project_settings_template.json")
try:
    _PROJECT_TEMPLATE = load_json(PROJECT_SETTINGS_TEMPLATE_PATH)
except Exception as exc:  # noqa: BLE001
    print(f"[assembler_core] WARNING: failed to load {PROJECT_SETTINGS_TEMPLATE_PATH}: {exc}")
    _PROJECT_TEMPLATE = {}

PROJECT_TEMPLATE_DEFAULTS: Dict[str, Any] = (
    _PROJECT_TEMPLATE.get("defaults", {}) if isinstance(_PROJECT_TEMPLATE, dict) else {}
) or {}


def _expand_normalized_keys(value_data: Any, start_time: float, end_time: float) -> Any:
    if not isinstance(value_data, dict) or "keys" not in value_data:
        return value_data

    keys = value_data.get("keys")
    if not isinstance(keys, list):
        return value_data

    dur = max(0.0, end_time - start_time)
    changed = False
    norm_keys = []
    for k in keys:
        if isinstance(k, dict) and ("time" not in k) and ("t" in k):
            try:
                t = float(k.get("t"))
            except Exception:  # noqa: BLE001
                norm_keys.append(k)
                continue
            kk = dict(k)
            kk["time"] = start_time + t * dur
            kk.pop("t", None)
            norm_keys.append(kk)
            changed = True
        else:
            norm_keys.append(k)

    if not changed:
        return value_data

    vd = dict(value_data)
    vd["keys"] = norm_keys
    return vd


def _build_effect_instance(
    preset: Dict[str, Any], overrides: Dict[str, Any], layer_in: float, layer_out: float
) -> Dict[str, Any] | None:
    prop_tree = preset.get("propertyTree") or {}
    match_name = prop_tree.get("matchName") if isinstance(prop_tree, dict) else None
    if not isinstance(match_name, str):
        return None

    exposed = preset.get("exposedParams") or []
    path_by_key: Dict[str, str] = {}
    if isinstance(exposed, list):
        for ep in exposed:
            if not isinstance(ep, dict):
                continue
            key = ep.get("key")
            path = ep.get("matchNamePath")
            if isinstance(key, str) and isinstance(path, str):
                path_by_key[key] = path

    params: Dict[str, Any] = {}
    for k, v in (overrides or {}).items():
        path = path_by_key.get(k)
        if not path:
            continue
        params[path] = _expand_normalized_keys(v, layer_in, layer_out)

    out: Dict[str, Any] = {"matchName": match_name}
    if params:
        out["params"] = params
    return out


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
    effects_library = load_json(EFFECTS_LIBRARY_PATH)
    effect_presets = effects_library.get("effectPresets", {}) if isinstance(effects_library, dict) else {}

    project_settings = composition.get("projectSettings", {}) or {}
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
    base_defaults.update(PROJECT_TEMPLATE_DEFAULTS)

    defaults: Dict[str, Any] = {}
    for key, value in base_defaults.items():
        defaults[key] = value

    for key, value in ps_defaults.items():
        if key in {"width", "height", "pixelAspect", "fps", "duration"}:
            continue
        defaults.setdefault(key, value)

    defaults["duration"] = duration

    # если fitPolicy не указан — по умолчанию считаем cover
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
                    styles_lib=styles_data,
                    presets_lib=presets_data,
                    global_fit_policy=global_fit_policy,
                )

                if processed_layer.get("type") == "adjustment":
                    layer_in = float(processed_layer.get("inPoint", 0.0) or 0.0)
                    layer_out = float(processed_layer.get("outPoint", layer_in) or layer_in)
                    stack = resolve_effect_stack(processed_layer, effects_library)
                    if stack:
                        fx_list = []
                        for inst in stack:
                            if not inst or inst.get("enabled") is False:
                                continue
                            preset_id = inst.get("presetId")
                            preset = effect_presets.get(preset_id) if preset_id else None
                            if not preset:
                                continue
                            overrides = inst.get("overrides") or {}
                            fx_conf = _build_effect_instance(preset, overrides, layer_in, layer_out)
                            if fx_conf:
                                fx_list.append(fx_conf)
                        if fx_list:
                            processed_layer["effects"] = fx_list

                processed_layers.append(processed_layer)
            item["layers"] = processed_layers

        final_items.append(item)

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
