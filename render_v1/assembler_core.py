# render_v1/assembler_core.py
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Config
from render_v1.effects_logic import resolve_effect_stack, stack_to_ae_effects_conf
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
EFFECTS_LIBRARY_PATH = Path("config/styles/effects_library.json")
TEXT_FX_COMBOS_PATH = Path("config/styles/text_fx_combos.json")
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
    _TEXT_FX_COMBOS = load_json(TEXT_FX_COMBOS_PATH)
except Exception as exc:  # noqa: BLE001
    print(f"[assembler_core] WARNING: failed to load {TEXT_FX_COMBOS_PATH}: {exc}")
    _TEXT_FX_COMBOS = {}

PROJECT_TEMPLATE_DEFAULTS: Dict[str, Any] = (
    _PROJECT_TEMPLATE.get("defaults", {}) if isinstance(_PROJECT_TEMPLATE, dict) else {}
) or {}



# --- Text FX combos (preset library) ---


def _get_text_fx_catalog() -> dict:
    if isinstance(_TEXT_FX_COMBOS, dict):
        return _TEXT_FX_COMBOS
    return {}


def _get_default_combo_id() -> str:
    catalog = _get_text_fx_catalog()
    default_id = catalog.get("defaultComboId")
    if isinstance(default_id, str) and default_id:
        return default_id
    return "GLITCH_DEFAULT_REVEAL"


def _lookup_combo(combo_id: str) -> Optional[dict]:
    catalog = _get_text_fx_catalog()
    combos = catalog.get("combos") or []
    if not isinstance(combos, list):
        return None
    for c in combos:
        if isinstance(c, dict) and c.get("id") == combo_id:
            return c
    return None


def _merge_keys_into_value_data(dst: dict, keys: list) -> dict:
    if not isinstance(dst, dict):
        dst = {}
    dst = copy.deepcopy(dst)
    dst["keys"] = keys
    return dst


def apply_text_fx_combo(layer: dict) -> dict:
    """Apply baked combo + overrides to a TEXT layer dict."""

    if (layer.get("type") or "").lower() != "text":
        return layer

    combo_id = layer.get("textFxComboId") or _get_default_combo_id()
    combo = _lookup_combo(combo_id)
    if combo is None:
        raise ValueError(f"Unknown textFxComboId: {combo_id}")

    baked = combo.get("baked") or {}
    if not isinstance(baked, dict):
        baked = {}

    baked_anim = baked.get("textAnimators")
    if baked_anim and "textAnimators" not in layer:
        layer["textAnimators"] = copy.deepcopy(baked_anim)

    baked_fx = baked.get("effects")
    if baked_fx and "effects" not in layer:
        layer["effects"] = copy.deepcopy(baked_fx)

    overrides = layer.get("textFxOverrides") or {}
    if isinstance(overrides, dict):
        reveal = overrides.get("revealKeys")
        if reveal and isinstance(reveal, list):
            try:
                anim0 = layer.get("textAnimators", [])[0]
                sel0 = (anim0.get("selectors") or [])[0]
                props = sel0.get("properties") or {}
                prop_name = baked.get("revealProperty") or "ADBE Text Percent Start"
                props[prop_name] = _merge_keys_into_value_data(
                    props.get(prop_name) or {}, reveal
                )
                sel0["properties"] = props
            except Exception:
                pass

        fade = overrides.get("opacityKeys")
        if fade and isinstance(fade, list):
            if "transform" not in layer or not isinstance(layer.get("transform"), dict):
                layer["transform"] = {}
            tr = layer["transform"]
            tr["opacity"] = _merge_keys_into_value_data(tr.get("opacity") or {}, fade)

        fx_over = overrides.get("effectParamKeys")
        if fx_over and isinstance(fx_over, list):
            if "effects" not in layer or not isinstance(layer.get("effects"), list):
                layer["effects"] = copy.deepcopy(baked_fx) if isinstance(baked_fx, list) else []
            for item in fx_over:
                if not isinstance(item, dict):
                    continue
                emn = item.get("effectMatchName")
                pmn = item.get("paramMatchName")
                keys = item.get("keys")
                if not (isinstance(emn, str) and isinstance(pmn, str) and isinstance(keys, list)):
                    continue
                for fx in layer["effects"]:
                    if isinstance(fx, dict) and fx.get("matchName") == emn:
                        params = fx.get("params") or {}
                        params[pmn] = _merge_keys_into_value_data(params.get(pmn) or {}, keys)
                        fx["params"] = params
                        break

    layer["textFxComboId"] = combo_id

    return layer


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

    # TEXT FX: apply baked combo + keyframe overrides (if configured)
    layer = apply_text_fx_combo(layer)

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

    plan_map: Dict[int, dict] = {}
    text_fx_plan = (
        composition.get("textFxPlan")
        or composition.get("text_fx_plan")
        or project_settings.get("textFxPlan")
        or project_settings.get("text_fx_plan")
        or {}
    )
    if isinstance(text_fx_plan, dict):
        plan_layers = text_fx_plan.get("layers") or []
        if isinstance(plan_layers, list):
            for pl in plan_layers:
                if not isinstance(pl, dict):
                    continue
                li = pl.get("layerIndex")
                if isinstance(li, int):
                    plan_map[li] = pl

    for item in raw_items:
        item = apply_defaults(item, defaults)

        if item.get("type") == "comp" and "layers" in item:
            processed_layers = []
            for li, layer in enumerate(item["layers"]):
                if plan_map and (item.get("id") == "comp_text" or (item.get("name") or "") == "Text"):
                    pl = plan_map.get(li)
                    if isinstance(pl, dict):
                        if pl.get("textFxComboId"):
                            layer["textFxComboId"] = pl.get("textFxComboId")
                        if pl.get("textFxOverrides"):
                            layer["textFxOverrides"] = pl.get("textFxOverrides")

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
