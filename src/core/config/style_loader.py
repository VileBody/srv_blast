from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict

from .styles import (
    EFFECTS_LIBRARY_PATH,
    TEXT_FX_LIBRARY_PATH,
    FOOTAGE_PRESETS_PATH,
    MOTION_LIBRARY_PATH,
    TEXT_FX_COMBOS_PATH,
    TEXT_STYLES_PATH,
    FootagePresetId,
    SubtitleStyle,
)

log = logging.getLogger(__name__)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        log.warning("[style_loader] JSON file not found: %s", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.error("[style_loader] Failed to load JSON %s: %s", path, exc)
        return {}


_TEXT_STYLES = _load_json(TEXT_STYLES_PATH)
_FOOTAGE_PRESETS = _load_json(FOOTAGE_PRESETS_PATH)
_MOTION_LIBRARY: Dict[str, Any] | None = None
_EFFECTS_LIBRARY: Dict[str, Any] | None = None
_TEXT_FX_LIBRARY: Dict[str, Any] | None = None
_TEXT_FX_COMBOS: Dict[str, Any] | None = None

_SUBTITLE_STYLE_KEYS = {
    SubtitleStyle.DEFAULT: "main_subtitle",
    SubtitleStyle.HIGHLIGHT: "highlight_subtitle",
}


def get_text_style(style: SubtitleStyle | str) -> Dict[str, Any]:
    """Возвращает копию настроек текста для заданного стиля субтитров."""

    if isinstance(style, str):
        style = (
            SubtitleStyle(style)
            if style in SubtitleStyle._value2member_map_  # type: ignore[attr-defined]
            else SubtitleStyle.DEFAULT
        )

    style_key = _SUBTITLE_STYLE_KEYS.get(style, "main_subtitle")
    return copy.deepcopy(_TEXT_STYLES.get(style_key, {}))


def get_footage_preset(preset_id: FootagePresetId | str) -> Dict[str, Any]:
    """Возвращает копию настроек пресета для футажа."""

    pid = preset_id.value if isinstance(preset_id, FootagePresetId) else str(preset_id)
    return copy.deepcopy(_FOOTAGE_PRESETS.get(pid, {}))


def get_motion_library() -> Dict[str, Any]:
    """Полная библиотека motion-профилей (если определена)."""
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
    """Text-layer FX combos library (effect stack + text animators)."""
    global _TEXT_FX_LIBRARY

    if _TEXT_FX_LIBRARY is None:
        _TEXT_FX_LIBRARY = _load_json(TEXT_FX_LIBRARY_PATH)
    return copy.deepcopy(_TEXT_FX_LIBRARY)


def get_text_fx_combos() -> Dict[str, Any]:
    """Text-layer FX combos library (new preset catalog)."""
    global _TEXT_FX_COMBOS

    if _TEXT_FX_COMBOS is None:
        _TEXT_FX_COMBOS = _load_json(TEXT_FX_COMBOS_PATH)
    return copy.deepcopy(_TEXT_FX_COMBOS)
