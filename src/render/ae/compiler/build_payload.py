# src/render/ae/compiler/build_payload.py
from __future__ import annotations

import copy
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Config
from .effects_logic import resolve_effect_stack, stack_to_ae_effects_conf
from src.render.ae.contracts.payload import Payload

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
    """MVP -> BAKED: textFxComboId (+ overrides) -> layer.textAnimators for ALL text layers.

    After this, JSX should NOT interpret comboId; it must only apply baked `textAnimators`.
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
            overrides = (
                layer.get("textFxOverrides")
                or layer.get("text_fx_overrides")
                or layer.get("textFxOverrideParams")
                or layer.get("text_fx_override_params")
                or {}
            )

            base = combos.get(combo_id) or combos.get(default_id)
            baked = _deep_merge(deepcopy(base), overrides)

            if baked.get("textAnimators"):
                layer["textAnimators"] = baked["textAnimators"]
                changed += 1

            # NEW: bake combo effectStack onto TEXT LAYERS as layer.effects (append).
            # Adjustment layers are untouched by design (we only are in type=='text').
            eff_stack = baked.get("effectStack") or []
            if eff_stack:
                existing = layer.get("effects") or []
                # keep stable ordering: existing effects first, then combo stack
                layer["effects"] = list(existing) + list(eff_stack)

            # cleanup MVP markers (keep project pure baked)
            layer.pop("textFxComboId", None)
            layer.pop("text_fx_combo_id", None)
            layer.pop("textFxOverrides", None)
            layer.pop("text_fx_overrides", None)
            layer.pop("textFxOverrideParams", None)
            layer.pop("text_fx_override_params", None)

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


def _resolve_cfg_path(primary: Path) -> Path:
    """
    Support both layouts:
      legacy: config/styles/<file>.json
      new:    config/styles/<group>/<file>.json
    """

    if primary.is_file():
        return primary

    name = primary.name
    candidates = [
        Path("config/styles") / name,
        Path("config/styles/text") / name,
        Path("config/styles/footage") / name,
        Path("config/styles/effects") / name,
        Path("config/styles/project") / name,
    ]
    for c in candidates:
        if c.is_file():
            print(f"[WARN] Using fallback config path: {c} (primary missing: {primary})")
            return c

    return primary


# project_settings_template.json по аналогии с text_styles / footage_presets
PROJECT_SETTINGS_TEMPLATE_PATH = _resolve_cfg_path(Path("config/styles/project_settings_template.json"))
EFFECTS_LIBRARY_PATH = _resolve_cfg_path(Path("config/styles/effects_library.json"))
TEXT_FX_LIBRARY_PATH = _resolve_cfg_path(Path("config/styles/text_fx_combos.json"))
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

    # TEXT: (styleId?) + content -> textDocument
    # Tolerant mode: if LLM forgot styleId, we inject default style anyway.
    if layer.get("type") == "text":
        sid = layer.get("styleId") or "main_subtitle"
        style_props = {}
        if isinstance(styles_lib, dict):
            style_props = copy.deepcopy(styles_lib.get(sid) or styles_lib.get("main_subtitle") or {})

        if "textDocument" not in layer or not isinstance(layer.get("textDocument"), dict):
            layer["textDocument"] = {}

        # Fill defaults if missing
        for k, v in style_props.items():
            if k not in layer["textDocument"]:
                layer["textDocument"][k] = v

        # Ensure "text" is present for pydantic TextDocument
        content = layer.get("content") or (layer.get("text") if not isinstance(layer.get("text"), dict) else None)
        if content:
            layer["textDocument"]["text"] = content
        else:
            # last-resort: never let pydantic fail on missing text
            layer["textDocument"].setdefault("text", "")

        # cleanup authoring keys
        layer.pop("content", None)
        if "text" in layer and not isinstance(layer["text"], dict):
            layer.pop("text", None)
        layer.pop("styleId", None)

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
    ensure_default_text_fx_combo(final_items, text_fx_lib)
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
