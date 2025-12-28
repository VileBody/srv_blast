from __future__ import annotations

import json

from src.core.config.style_loader import get_effects_library, get_text_fx_library
from src.render.ae.compiler.effects_logic import build_semantic_prompt_catalog

from .stages import (
    AE_COMPOSITION_STAGE,
    AE_FOOTAGE_STAGE,
    AE_PROJECT_FOOTER,
    AE_PROJECT_HEADER,
    AE_SUBTITLES_STAGE,
)


def _effects_semantic_catalog_json() -> str:
    lib = get_effects_library() or {}
    cat = build_semantic_prompt_catalog(lib, include_defaults=True)
    return json.dumps(cat, ensure_ascii=False, indent=2)


def _effects_semantic_prompt_block() -> str:
    lib = get_effects_library() or {}
    allowed = ", ".join(sorted((lib.get("semanticStyles") or {}).keys())) if isinstance(lib, dict) else ""
    return (
        "ADJUSTMENT LAYER EFFECT STYLES (semantic modes)\n"
        "- For every *footage* layer (type 'ref' pointing to footage) and every *text* layer, create **one** adjustment layer immediately above it.\n"
        "- Adjustment layer must have: type='adjustment', inPoint/outPoint matching the target window, and effectStyleId from this union: ["
        + allowed
        + "]\n"
        "- Use effectOverrides ONLY to retime keyframes (edit 't' or 'time'). Keep keyframe values (intensity) as-is from the preset for now.\n"
        "- Prefer normalized keyframe time: 't' in [0..1] inside the layer window (assembler maps to absolute 'time').\n"
        "- Footage default (no alternatives yet): ftg_al16_default_v1\n"
        "- Text defaults: txt_soft_v1 (normal), txt_punch_v1 (emphasis/hype), txt_drop_v1 (hard impact)\n"
        "\n"
        "EFFECTS_STYLES_CATALOG (JSON):\n"
        + _effects_semantic_catalog_json()
    )


def _text_fx_catalog_json() -> str:
    # Загружаем через геттер, чтобы избежать ImportError с приватной переменной
    lib = get_text_fx_library() or {}
    
    combos_out = {}
    for cid, cdata in (lib.get("combos") or {}).items():
        combos_out[cid] = {
            "description": cdata.get("description", ""),
            "parameters": cdata.get("defaults", {}) # Показываем, что можно крутить
        }
    
    return json.dumps(combos_out, ensure_ascii=False, indent=2)


def _text_fx_prompt_block() -> str:
    return (
        "TEXT ANIMATION STYLES (textFxComboId)\n"
        "- Use 'textFxComboId' to apply animation preset.\n"
        "- Use 'textFxOverrides': { paramName: value } to tweak timing/intensity.\n"
        "AVAILABLE TEXT COMBOS:\n" + _text_fx_catalog_json()
    )


def build_ae_project_system_prompt() -> str:
    """
    Собирает большой system-prompt для задачи:
      аудио + библиотека → (шоты + сабы + composition.json).
    """
    parts = [
        AE_PROJECT_HEADER,
        AE_FOOTAGE_STAGE,
        AE_SUBTITLES_STAGE,
        AE_COMPOSITION_STAGE,
        _effects_semantic_prompt_block(),
        _text_fx_prompt_block(), 
        AE_PROJECT_FOOTER,
    ]
    return "\n\n".join(parts)


# Основной промпт для AE-проекта
AE_PROJECT_SYSTEM = build_ae_project_system_prompt()

# Для обратной совместимости: старое имя, которое уже ждёт AePlanner / planner
AE_EDIT_PLAN_SYSTEM = AE_PROJECT_SYSTEM