from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.config.styles.paths import (
    EFFECTS_LIBRARY_PATH,
    FOOTAGE_PRESETS_PATH,
    MOTION_LIBRARY_PATH,
    TEXT_FX_LIBRARY_PATH,
    TEXT_STYLES_PATH,
    TAGS_CATALOG_PATH,
    TAGS_DIR,
    TAGS_PACKS_DIR,
    tag_pack_path,
)
from .styles import FootagePresetId, SubtitleStyle

log = logging.getLogger(__name__)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        # STRICT: не глотаем ошибки, иначе ты “тонешь” и не понимаешь, что реально не так
        raise RuntimeError(f"Failed to load JSON {path}: {exc}") from exc


_TEXT_STYLES: Optional[Dict[str, Any]] = None
_FOOTAGE_PRESETS: Optional[Dict[str, Any]] = None
_MOTION_LIBRARY: Dict[str, Any] | None = None
_EFFECTS_LIBRARY: Dict[str, Any] | None = None
_TEXT_FX_LIBRARY: Dict[str, Any] | None = None  # <--- Добавлена переменная
_TAGS_CATALOG: Dict[str, Any] | None = None
_TAG_PACK_CACHE: Dict[str, Dict[str, Any]] = {}

_SUBTITLE_STYLE_KEYS = {
    SubtitleStyle.DEFAULT: "main_subtitle",
    SubtitleStyle.HIGHLIGHT: "highlight_subtitle",
}


def get_text_style(style: SubtitleStyle | str) -> Dict[str, Any]:
    """Возвращает копию настроек текста для заданного стиля субтитров."""

    global _TEXT_STYLES
    if _TEXT_STYLES is None:
        _TEXT_STYLES = _load_json(TEXT_STYLES_PATH)

    if isinstance(style, str):
        style = (
            SubtitleStyle(style)
            if style in SubtitleStyle._value2member_map_  # type: ignore[attr-defined]
            else SubtitleStyle.DEFAULT
        )

    style_key = _SUBTITLE_STYLE_KEYS.get(style, "main_subtitle")
    return copy.deepcopy((_TEXT_STYLES or {}).get(style_key, {}))


def get_footage_preset(preset_id: FootagePresetId | str) -> Dict[str, Any]:
    """Возвращает копию настроек пресета для футажа."""

    global _FOOTAGE_PRESETS
    if _FOOTAGE_PRESETS is None:
        _FOOTAGE_PRESETS = _load_json(FOOTAGE_PRESETS_PATH)

    pid = preset_id.value if isinstance(preset_id, FootagePresetId) else str(preset_id)
    return copy.deepcopy((_FOOTAGE_PRESETS or {}).get(pid, {}))


def get_motion_library() -> Dict[str, Any]:
    """Motion-часть textFxComboId: threeD/textAnimators/textMoreOptions + defaults/exposedMap."""
    global _MOTION_LIBRARY

    if _MOTION_LIBRARY is None:
        _MOTION_LIBRARY = _load_json(MOTION_LIBRARY_PATH)
    return copy.deepcopy(_MOTION_LIBRARY)


def get_effects_library() -> Dict[str, Any]:
    """Semantic adjustment-layer effects library."""
    global _EFFECTS_LIBRARY

    if _EFFECTS_LIBRARY is None:
        _EFFECTS_LIBRARY = _load_json(EFFECTS_LIBRARY_PATH)
    return copy.deepcopy(_EFFECTS_LIBRARY)


def get_text_fx_library() -> Dict[str, Any]:
    """Effects-часть textFxComboId: effects + defaults/exposedMap (без textAnimators)."""
    global _TEXT_FX_LIBRARY

    if _TEXT_FX_LIBRARY is None:
        _TEXT_FX_LIBRARY = _load_json(TEXT_FX_LIBRARY_PATH)
    return copy.deepcopy(_TEXT_FX_LIBRARY)


# ---------------------------
# TAGS (optional)
# ---------------------------


def get_tags_catalog() -> Dict[str, Any]:
    """
    Возвращает JSON-каталог тегов (если есть).
    Формат каталога оставляем гибким: это может быть как {tagId: {...}}, так и
    объект с ключом "tag_catalog" / "tags".
    """
    global _TAGS_CATALOG
    if _TAGS_CATALOG is None:
        if TAGS_CATALOG_PATH is None:
            _TAGS_CATALOG = {}
        else:
            _TAGS_CATALOG = _load_json(TAGS_CATALOG_PATH)
    return copy.deepcopy(_TAGS_CATALOG or {})


def get_tag_pack(tag_id: str) -> Dict[str, Any]:
    """
    Загружает tag pack (если есть).
    Поддерживаем несколько layout'ов, чтобы миграции не были болезненными.
    """
    if not tag_id:
        return {}

    if tag_id in _TAG_PACK_CACHE:
        return copy.deepcopy(_TAG_PACK_CACHE[tag_id])

    candidates = [
        # preferred
        tag_pack_path(tag_id),
        # alt layouts
        (TAGS_PACKS_DIR / f"{tag_id}.json").resolve(),
        (TAGS_DIR / f"{tag_id}.json").resolve(),
        (TAGS_DIR / tag_id / "tag_pack.json").resolve(),
    ]

    for p in candidates:
        try:
            if p.is_file():
                data = _load_json(p)
                _TAG_PACK_CACHE[tag_id] = data
                return copy.deepcopy(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("[style_loader] Failed to load tag pack %s from %s: %s", tag_id, p, exc)

    _TAG_PACK_CACHE[tag_id] = {}
    return {}
