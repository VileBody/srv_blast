from __future__ import annotations

import json

from src.core.config.style_loader import (
    get_effects_library,
    get_motion_library,
    get_text_fx_library,
    get_tags_catalog,
)
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
    fx = get_text_fx_library() or {}
    motion = get_motion_library() or {}

    fx_combos = (fx.get("combos") or {}) if isinstance(fx, dict) else {}
    motion_combos = (motion.get("combos") or {}) if isinstance(motion, dict) else {}

    all_ids = sorted(set(fx_combos.keys()) | set(motion_combos.keys()))

    combos_out = {}
    for cid in all_ids:
        fx_c = fx_combos.get(cid) or {}
        m_c = motion_combos.get(cid) or {}

        params = {}
        params.update((m_c.get("defaults") or {}) if isinstance(m_c, dict) else {})
        params.update((fx_c.get("defaults") or {}) if isinstance(fx_c, dict) else {})

        combos_out[cid] = {
            "description": (m_c.get("description") or fx_c.get("description") or ""),
            "parameters": params,
        }

    return json.dumps(combos_out, ensure_ascii=False, indent=2)


def _text_fx_prompt_block() -> str:
    return (
        "TEXT PRESETS (textFxComboId)\n"
        "- Each preset applies BOTH:\n"
        "  (a) text animators from text_motion_library.json\n"
        "  (b) text effects from text_fx_combos.json\n"
        "- Use 'textFxComboId' to apply preset.\n"
        "- Use 'textFxOverrides': { paramName: value } to tweak timing/intensity.\n"
        "AVAILABLE TEXT COMBOS:\n" + _text_fx_catalog_json()
    )


def _tags_catalog_json() -> str:
    """
    Каталог тегов может быть в любом формате (зависит от того, как вы соберёте config),
    поэтому мы просто сериализуем то, что есть.
    """
    tags = get_tags_catalog() or {}
    return json.dumps(tags, ensure_ascii=False, indent=2)


def _tags_prompt_block() -> str:
    tags = get_tags_catalog() or {}
    # Пытаемся извлечь "плоский" список id, если структура сложнее
    if isinstance(tags, dict):
        if "tag_catalog" in tags and isinstance(tags["tag_catalog"], dict):
            allowed = ", ".join(sorted(tags["tag_catalog"].keys()))
        elif "tags" in tags and isinstance(tags["tags"], dict):
            allowed = ", ".join(sorted(tags["tags"].keys()))
        else:
            allowed = ", ".join(sorted(tags.keys()))
    else:
        allowed = ""
    return (
        "TEXT TAGS (optional)\n"
        "- You MAY set subtitle.tag/tagId for each subtitle line.\n"
        "- tag/tagId must be from this union: [" + allowed + "]\n"
        "TAGS_CATALOG (JSON):\n" + _tags_catalog_json()
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
        _tags_prompt_block(),
        AE_PROJECT_FOOTER,
    ]
    return "\n\n".join(parts)


# Основной промпт для AE-проекта
AE_PROJECT_SYSTEM = build_ae_project_system_prompt()

# Для обратной совместимости: старое имя, которое уже ждёт AePlanner / planner
AE_EDIT_PLAN_SYSTEM = AE_PROJECT_SYSTEM
