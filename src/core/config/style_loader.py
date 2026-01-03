from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.config.styles.paths import get_style_paths
from .styles import FootagePresetId, SubtitleStyle

log = logging.getLogger(__name__)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to load JSON {path}: {exc}") from exc


# ---------------------------
# Per-style caches
# key = styles_root.as_posix()
# ---------------------------

_TEXT_STYLES_BY_ROOT: Dict[str, Dict[str, Any]] = {}
_FOOTAGE_PRESETS_BY_ROOT: Dict[str, Dict[str, Any]] = {}
_MOTION_LIBRARY_BY_ROOT: Dict[str, Dict[str, Any]] = {}
_EFFECTS_LIBRARY_BY_ROOT: Dict[str, Dict[str, Any]] = {}
_TEXT_FX_LIBRARY_BY_ROOT: Dict[str, Dict[str, Any]] = {}

_TAGS_CATALOG_BY_ROOT: Dict[str, Dict[str, Any]] = {}
_TAG_PACK_CACHE_BY_ROOT: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (root_key, tag_id) -> pack

_SUBTITLE_STYLE_KEYS = {
    SubtitleStyle.DEFAULT: "main_subtitle",
    SubtitleStyle.HIGHLIGHT: "highlight_subtitle",
}


def _root_key(style_id: Optional[str] = None) -> Tuple[str, Dict[str, Path]]:
    """
    Resolves style paths and returns:
      - cache key (styles root path string)
      - resolved paths dict
    """
    paths = get_style_paths(style_id)
    root = paths["root"]
    return root.as_posix(), paths


# ---------------------------
# Base libraries
# ---------------------------

def get_text_style(style: SubtitleStyle | str, *, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Возвращает копию настроек текста для заданного стиля субтитров.
    Читает из text/text_styles.json выбранного style root.
    """
    rkey, paths = _root_key(style_id)

    if rkey not in _TEXT_STYLES_BY_ROOT:
        _TEXT_STYLES_BY_ROOT[rkey] = _load_json(paths["text_styles"])

    if isinstance(style, str):
        style = (
            SubtitleStyle(style)
            if style in SubtitleStyle._value2member_map_  # type: ignore[attr-defined]
            else SubtitleStyle.DEFAULT
        )

    style_key = _SUBTITLE_STYLE_KEYS.get(style, "main_subtitle")
    return copy.deepcopy((_TEXT_STYLES_BY_ROOT.get(rkey) or {}).get(style_key, {}))


def get_footage_preset(preset_id: FootagePresetId | str, *, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Возвращает копию настроек пресета для футажа из footage/footage_presets.json выбранного style root.
    """
    rkey, paths = _root_key(style_id)

    if rkey not in _FOOTAGE_PRESETS_BY_ROOT:
        _FOOTAGE_PRESETS_BY_ROOT[rkey] = _load_json(paths["footage"])

    pid = preset_id.value if isinstance(preset_id, FootagePresetId) else str(preset_id)
    return copy.deepcopy((_FOOTAGE_PRESETS_BY_ROOT.get(rkey) or {}).get(pid, {}))


def get_motion_library(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Motion-часть textFxComboId: threeD/textAnimators/textMoreOptions + defaults/exposedMap.
    """
    rkey, paths = _root_key(style_id)
    if rkey not in _MOTION_LIBRARY_BY_ROOT:
        _MOTION_LIBRARY_BY_ROOT[rkey] = _load_json(paths["motion"])
    return copy.deepcopy(_MOTION_LIBRARY_BY_ROOT[rkey])


def get_effects_library(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Semantic adjustment-layer effects library.
    """
    rkey, paths = _root_key(style_id)
    if rkey not in _EFFECTS_LIBRARY_BY_ROOT:
        _EFFECTS_LIBRARY_BY_ROOT[rkey] = _load_json(paths["effects"])
    return copy.deepcopy(_EFFECTS_LIBRARY_BY_ROOT[rkey])


def get_text_fx_library(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Effects-часть textFxComboId: effects + defaults/exposedMap (без textAnimators).
    """
    rkey, paths = _root_key(style_id)
    if rkey not in _TEXT_FX_LIBRARY_BY_ROOT:
        _TEXT_FX_LIBRARY_BY_ROOT[rkey] = _load_json(paths["text_fx"])
    return copy.deepcopy(_TEXT_FX_LIBRARY_BY_ROOT[rkey])


# ---------------------------
# TAGS (optional)
# ---------------------------

def get_tags_catalog(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Возвращает JSON-каталог тегов (если есть) для выбранного style root.

    Мы не привязываемся к одному имени, ищем несколько вариантов:
      tags/preset_catalog_v2.json
      tags/tags_catalog.json
      tags/tag_catalog.json
      tags/catalog.json
    """
    rkey, paths = _root_key(style_id)
    if rkey in _TAGS_CATALOG_BY_ROOT:
        return copy.deepcopy(_TAGS_CATALOG_BY_ROOT[rkey])

    tags_dir = paths.get("tags_dir")
    if not tags_dir or not isinstance(tags_dir, Path):
        _TAGS_CATALOG_BY_ROOT[rkey] = {}
        return {}

    candidates = [
        tags_dir / "preset_catalog_v2.json",
        tags_dir / "tags_catalog.json",
        tags_dir / "tag_catalog.json",
        tags_dir / "catalog.json",
    ]

    for p in candidates:
        try:
            if p.is_file():
                data = _load_json(p)
                _TAGS_CATALOG_BY_ROOT[rkey] = data
                return copy.deepcopy(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("[style_loader] Failed to load tags catalog from %s: %s", p, exc)

    _TAGS_CATALOG_BY_ROOT[rkey] = {}
    return {}


def get_tag_pack(tag_id: str, *, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Загружает tag pack (если есть) для выбранного style root.

    Ищем в порядке:
      tags/packs/<tag_id>.json
      tags/<tag_id>.json
      tags/<tag_id>/tag_pack.json
    """
    if not tag_id:
        return {}

    rkey, paths = _root_key(style_id)
    cache_key = (rkey, tag_id)
    if cache_key in _TAG_PACK_CACHE_BY_ROOT:
        return copy.deepcopy(_TAG_PACK_CACHE_BY_ROOT[cache_key])

    tags_dir = paths.get("tags_dir")
    packs_dir = paths.get("tags_packs_dir")

    candidates = []
    if isinstance(packs_dir, Path):
        candidates.append((packs_dir / f"{tag_id}.json").resolve())
    if isinstance(tags_dir, Path):
        candidates.append((tags_dir / f"{tag_id}.json").resolve())
        candidates.append((tags_dir / tag_id / "tag_pack.json").resolve())

    for p in candidates:
        try:
            if p.is_file():
                data = _load_json(p)
                _TAG_PACK_CACHE_BY_ROOT[cache_key] = data
                return copy.deepcopy(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("[style_loader] Failed to load tag pack %s from %s: %s", tag_id, p, exc)

    _TAG_PACK_CACHE_BY_ROOT[cache_key] = {}
    return {}
