from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from .styles import SubtitleStyle, FootagePresetId

log = logging.getLogger(__name__)

# repo_root/config/styles[/<pack>]
STYLES_DIR = Path(__file__).resolve().parents[3] / "config" / "styles"
DEFAULT_STYLE_PACK = "pop-music"
PACK_NAME = (os.getenv("AE_STYLE_PACK") or DEFAULT_STYLE_PACK).strip()
PACK_DIR = (STYLES_DIR / PACK_NAME) if (STYLES_DIR / PACK_NAME).is_dir() else STYLES_DIR

TEXT_STYLES_PATH = PACK_DIR / "text_styles.json"
FOOTAGE_PRESETS_PATH = PACK_DIR / "footage_presets.json"
TEXT_MOTION_LIBRARY_PATH = PACK_DIR / "text_motion_library.json"

_SUBTITLE_STYLE_KEYS = {
    SubtitleStyle.DEFAULT: "main_subtitle",
    SubtitleStyle.HIGHLIGHT: "highlight_subtitle",
}

_TEXT_STYLES: Dict[str, Any] = {}
_FOOTAGE_PRESETS: Dict[str, Any] = {}
_TEXT_MOTION_LIBRARY: Dict[str, Any] = {}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        log.warning("[style_loader] JSON file not found: %s", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.error("[style_loader] Failed to load JSON %s: %s", path, exc)
        return {}


def _reload_all() -> None:
    global _TEXT_STYLES, _FOOTAGE_PRESETS, _TEXT_MOTION_LIBRARY
    _TEXT_STYLES = _load_json(TEXT_STYLES_PATH)
    _FOOTAGE_PRESETS = _load_json(FOOTAGE_PRESETS_PATH)
    _TEXT_MOTION_LIBRARY = _load_json(TEXT_MOTION_LIBRARY_PATH)


# load eagerly on import
_reload_all()


def get_text_style(style: SubtitleStyle | str) -> Dict[str, Any]:
    """Возвращает копию TextDocument-настроек из config/styles/text_styles.json."""

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


def get_text_motion_library() -> Dict[str, Any]:
    """Возвращает копию полной библиотеки text motion (anim+transform+keyTemplates)."""
    return copy.deepcopy(_TEXT_MOTION_LIBRARY)


def get_key_templates() -> Dict[str, Any]:
    return copy.deepcopy((_TEXT_MOTION_LIBRARY.get("keyTemplates") or {}))


def get_text_anim_preset(anim_id: str) -> Dict[str, Any]:
    presets = _TEXT_MOTION_LIBRARY.get("textAnimPresets") or {}
    return copy.deepcopy(presets.get(anim_id, {}))


def get_transform_preset(transform_id: str) -> Dict[str, Any]:
    presets = _TEXT_MOTION_LIBRARY.get("transformPresets") or {}
    return copy.deepcopy(presets.get(transform_id, {}))
