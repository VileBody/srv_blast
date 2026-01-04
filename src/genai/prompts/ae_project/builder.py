from __future__ import annotations

import json

from src.core.config.style_loader import get_tags_catalog

from .stages import (
    AE_COMPOSITION_STAGE,
    AE_FOOTAGE_STAGE,
    AE_PROJECT_FOOTER,
    AE_PROJECT_HEADER,
    AE_SUBTITLES_STAGE,
)

def _tags_catalog_json(style_id: str | None = None) -> str:
    tags = get_tags_catalog(style_id=style_id) or {}
    return json.dumps(tags, ensure_ascii=False, indent=2)


def _tags_prompt_block(style_id: str | None = None) -> str:
    tags = get_tags_catalog(style_id=style_id) or {}
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
        "TAGS_CATALOG (JSON):\n" + _tags_catalog_json(style_id)
    )


def build_ae_project_system_prompt(style_id: str | None = None) -> str:
    """
    Собирает большой system-prompt для задачи:
      аудио + библиотека → (шоты + сабы + composition.json).
    """
    parts = [
        AE_PROJECT_HEADER,
        AE_FOOTAGE_STAGE,
        AE_SUBTITLES_STAGE,
        AE_COMPOSITION_STAGE,
        _tags_prompt_block(style_id),
        AE_PROJECT_FOOTER,
    ]
    return "\n\n".join(parts)
