# src/render/ae/compiler/build_payload.py
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from config import Config
from .effects_logic import resolve_effect_stack, stack_to_ae_effects_conf
from src.render.ae.contracts.payload import Payload
from src.config.styles.paths import (
    PROJECT_SETTINGS_TEMPLATE_PATH,
    EFFECTS_LIBRARY_PATH,
    TEXT_FX_LIBRARY_PATH,
    TEXT_STYLES_PATH,
    FOOTAGE_PRESETS_PATH,
    MOTION_LIBRARY_PATH,
)

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

def set_value_by_path(target: Any, path: List[str | int], value: Any) -> None:
    """
    Идет по пути path внутри target и устанавливает value в конце.
    Поддерживает и dict, и list (если ключ в path — int).
    """
    ref = target
    for i, key in enumerate(path[:-1]):
        if isinstance(ref, list):
            try:
                idx = int(key)
                ref = ref[idx]
            except (ValueError, IndexError):
                return # Путь битый, выходим
        elif isinstance(ref, dict):
            ref = ref.get(key)
            if ref is None:
                return # Путь битый
        else:
            return
    
    # Установка значения
    last_key = path[-1]
    if isinstance(ref, list):
        try:
            idx = int(last_key)
            if 0 <= idx < len(ref):
                ref[idx] = value
        except (ValueError, IndexError):
            pass
    elif isinstance(ref, dict):
        ref[last_key] = value


def ensure_default_text_fx_combo(items: List[Dict[str, Any]], text_fx_library: Dict[str, Any]) -> int:
    """MVP: ensure every text layer has exactly one combo (default if missing).

    We keep it deliberately conservative:
    - Affects ALL comps (all text layers).
    - Only sets layer.textFxComboId if missing/empty.
    """
    default_id = (text_fx_library or {}).get("defaultComboId")
    combos = (text_fx_library or {}).get("combos") or {}
    if not default_id or default_id not in combos:
        return 0
    n = 0
    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if (layer.get("type") or "").lower() != "text":
                continue
            combo_id = layer.get("textFxComboId") or layer.get("text_fx_combo_id")
            if not combo_id:
                layer["textFxComboId"] = default_id
                n += 1
    return n


def _deep_merge(a: Any, b: Any) -> Any:
    """Deep-merge dicts; lists are replaced (MVP-предсказуемо)."""
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k in out:
                out[k] = _deep_merge(out.get(k), v)
            else:
                out[k] = v
        return out
    if isinstance(b, list):
        return list(b)
    return b


def expand_text_fx_combos(items: List[Dict[str, Any]], text_fx_library: Dict[str, Any]) -> int:
    """
    BAKED PHASE:
    Берем textFxComboId + overrides, находим шаблон, "компилируем" его
    (подставляем значения в template) и сохраняем результат в layer.
    """
    default_id = (text_fx_library or {}).get("defaultComboId")
    combos = (text_fx_library or {}).get("combos") or {}
    if not default_id or default_id not in combos:
        return 0

    changed = 0
    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if (layer.get("type") or "").lower() != "text":
                continue

            combo_id = (
                layer.get("textFxComboId")
                or layer.get("text_fx_combo_id")
                or default_id
            )
            
            # Если такого стиля нет, фолбэк на дефолт
            if combo_id not in combos:
                combo_id = default_id

            combo_conf = combos[combo_id]
            
            # 1. Берем template
            # (Поддержка легаси: если нет template, берем весь конфиг как template)
            raw_template = combo_conf.get("template", combo_conf)
            baked = copy.deepcopy(raw_template)

            # 2. Собираем параметры (Defaults + Overrides)
            # Ищем overrides в разных полях (для совместимости)
            layer_overrides = (
                layer.get("textFxOverrides")
                or layer.get("text_fx_overrides")
                or layer.get("textFxOverrideParams")
                or layer.get("text_fx_override_params")
                or {}
            )
            
            # Активные параметры = Defaults из конфига + то, что пришло от LLM
            active_params = copy.deepcopy(combo_conf.get("defaults", {}))
            active_params.update(layer_overrides)

            # 3. Применяем exposedMap
            mapping = combo_conf.get("exposedMap", {})
            for param_key, param_val in active_params.items():
                target_path = mapping.get(param_key)
                if target_path:
                    set_value_by_path(baked, target_path, param_val)

            # 4. Записываем результат в слой (EFFECTS-ONLY)
            eff_stack = baked.get("effects") or baked.get("effectStack") or []
            if eff_stack:
                existing = layer.get("effects") or []
                layer["effects"] = list(existing) + list(eff_stack)
                changed += 1

            # Чистим служебные поля
            for k in ["textFxComboId", "text_fx_combo_id", "textFxOverrides", "text_fx_overrides"]:
                layer.pop(k, None)

    return changed


def expand_text_motion_combos(items: List[Dict[str, Any]], motion_library: Dict[str, Any]) -> int:
    """
    MOTION PHASE:
    Берем textFxComboId + overrides, находим motion-template и сохраняем в layer:
      - threeD
      - textAnimators
      - textMoreOptions
    """
    default_id = (motion_library or {}).get("defaultComboId")
    combos = (motion_library or {}).get("combos") or {}
    if not default_id or default_id not in combos:
        return 0

    changed = 0
    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if (layer.get("type") or "").lower() != "text":
                continue

            combo_id = (
                layer.get("textFxComboId")
                or layer.get("text_fx_combo_id")
                or default_id
            )

            # STRICT: если чего-то нет — лучше упасть, чем "тихо без motion"
            if combo_id not in combos:
                raise KeyError(f"Unknown motion comboId={combo_id!r}. Known: {sorted(combos.keys())}")

            combo_conf = combos[combo_id]
            raw_template = combo_conf.get("template", combo_conf)
            baked = copy.deepcopy(raw_template)

            layer_overrides = (
                layer.get("textFxOverrides")
                or layer.get("text_fx_overrides")
                or layer.get("textFxOverrideParams")
                or layer.get("text_fx_override_params")
                or {}
            )

            active_params = copy.deepcopy(combo_conf.get("defaults", {}))
            active_params.update(layer_overrides)

            mapping = combo_conf.get("exposedMap", {})
            for param_key, param_val in active_params.items():
                target_path = mapping.get(param_key)
                if target_path:
                    set_value_by_path(baked, target_path, param_val)

            if baked.get("threeD"):
                layer["threeD"] = True

            if baked.get("textAnimators"):
                layer["textAnimators"] = baked["textAnimators"]
                changed += 1

            if baked.get("textMoreOptions"):
                layer["textMoreOptions"] = baked["textMoreOptions"]

    return changed


def load_json(path: Path) -> dict:
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Decoding {path}: {exc}")
        return {}



try:
    _PROJECT_TEMPLATE = load_json(PROJECT_SETTINGS_TEMPLATE_PATH)
except Exception as exc:  # noqa: BLE001
    print(f"[assembler_core] WARNING: failed to load {PROJECT_SETTINGS_TEMPLATE_PATH}: {exc}")
    _PROJECT_TEMPLATE = {}

try:
    _EFFECTS_LIBRARY = load_json(EFFECTS_LIBRARY_PATH)
except Exception as exc:  # noqa: BLE001
    print(f"[assembler_core] WARNING: failed to load {EFFECTS_LIBRARY_PATH}: {exc}")
    _EFFECTS_LIBRARY = {}

try:
    _TEXT_FX_LIBRARY = load_json(TEXT_FX_LIBRARY_PATH)
except Exception as exc:  # noqa: BLE001
    print(f"[assembler_core] WARNING: failed to load {TEXT_FX_LIBRARY_PATH}: {exc}")
    _TEXT_FX_LIBRARY = {}

try:
    _MOTION_LIBRARY = load_json(MOTION_LIBRARY_PATH)
except Exception as exc:  # noqa: BLE001
    print(f"[assembler_core] WARNING: failed to load {MOTION_LIBRARY_PATH}: {exc}")
    _MOTION_LIBRARY = {}

PROJECT_TEMPLATE_DEFAULTS: Dict[str, Any] = (
    _PROJECT_TEMPLATE.get("defaults", {}) if isinstance(_PROJECT_TEMPLATE, dict) else {}
) or {}


def process_layer(
    layer: dict,
    styles_lib: dict,
    presets_lib: dict,
    effects_lib: Optional[dict] = None,
    global_fit_policy: str | None = None,
) -> dict:
    """
    Преобразует слой из composition.json-стиля:
      - styleId + content -> textDocument + стили,
      - presetId -> transform (с учётом fitPolicy),
      - global_fit_policy -> fitPolicy для ref-слоёв (если локально не задано),
      - если есть inPoint, но нет startTime — ставим startTime = inPoint.
      - effectStyleId/effectOverrides -> effects (для adjustment-слоёв)
    """
    if "inPoint" in layer and "startTime" not in layer:
        layer["startTime"] = layer["inPoint"]

    # TEXT: always bake into `textDocument` (Payload requires it).
    # If style library is missing / styleId unknown, we still keep text and validate.
    if layer.get("type") == "text":
        # 1) extract raw text
        content = layer.get("content")
        if content is None and isinstance(layer.get("text"), str):
            content = layer.get("text")
        if content is None:
            content = ""

        # 2) ensure textDocument exists
        if not isinstance(layer.get("textDocument"), dict):
            layer["textDocument"] = {"text": str(content)}
        else:
            layer["textDocument"].setdefault("text", str(content))

        # 3) apply styleId if present & known
        sid = layer.get("styleId")
        if sid:
            style_props = styles_lib.get(sid)
            if isinstance(style_props, dict) and style_props:
                for k, v in copy.deepcopy(style_props).items():
                    layer["textDocument"].setdefault(k, v)
            else:
                print(f"[ASSEMBLER] WARNING: unknown styleId={sid!r} for text layer; using defaults")
            layer.pop("styleId", None)

        # 4) cleanup legacy fields
        layer.pop("content", None)
        if "text" in layer and not isinstance(layer["text"], dict):
            layer.pop("text", None)

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

    # EFFECT STYLE: semantic adjustment-layer presets
    if layer.get("type") == "adjustment" and effects_lib:
        existing_effects = layer.get("effects")
        effect_style_id = (
            layer.get("effectStyleId")
            or layer.get("fxStyleId")
            or layer.get("effectsStyleId")
        )
        effect_overrides = (
            layer.get("effectOverrides")
            or layer.get("fxOverrides")
            or layer.get("effectsOverrides")
            or {}
        )

        if not existing_effects and effect_style_id:
            layer_in = float(layer.get("inPoint") or layer.get("startTime") or 0.0)
            layer_out = float(layer.get("outPoint") or layer_in)
            try:
                stack = resolve_effect_stack(effect_style_id, effect_overrides, effects_lib)
                layer["effects"] = stack_to_ae_effects_conf(
                    stack,
                    effects_library=effects_lib,
                    layer_in=layer_in,
                    layer_out=layer_out,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[effects] failed to resolve style={effect_style_id} for layer={layer.get('name')}: {exc}"
                )

        # Cleanup aux keys to avoid leaking into downstream validators
        for key in (
            "effectStyleId",
            "effectsStyleId",
            "fxStyleId",
            "effectOverrides",
            "effectsOverrides",
            "fxOverrides",
        ):
            if key in layer:
                layer.pop(key)

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
    effects_data = copy.deepcopy(_EFFECTS_LIBRARY)

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
                    effects_lib=effects_data,
                    global_fit_policy=global_fit_policy,
                )
                processed_layers.append(processed_layer)
            item["layers"] = processed_layers

        final_items.append(item)

    text_fx_lib = copy.deepcopy(_TEXT_FX_LIBRARY)
    motion_lib = copy.deepcopy(_MOTION_LIBRARY)
    ensure_default_text_fx_combo(final_items, text_fx_lib)
    expand_text_motion_combos(final_items, motion_lib)
    expand_text_fx_combos(final_items, text_fx_lib)

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
