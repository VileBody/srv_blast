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

_TAGS_CATALOG_BY_ROOT: Dict[str, Dict[str, Any]] = {}
_TAG_PACK_CACHE_BY_ROOT: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (root_key, tag_id) -> pack


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
    raise RuntimeError("Legacy text styles are not supported in ae_presets-only mode.")


def get_footage_preset(preset_id: FootagePresetId | str, *, style_id: Optional[str] = None) -> Dict[str, Any]:
    raise RuntimeError("Legacy footage presets are not supported in ae_presets-only mode.")


def get_motion_library(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    raise RuntimeError("Legacy motion library is not supported in ae_presets-only mode.")


def get_effects_library(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    raise RuntimeError("Legacy effects library is not supported in ae_presets-only mode.")


def get_text_fx_library(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    raise RuntimeError("Legacy text FX library is not supported in ae_presets-only mode.")


# ---------------------------
# TAGS (ae_presets-only)
# ---------------------------


def _load_pair(base_dir: Path, rel_dir: str, fname: str) -> Optional[Dict[str, Any]]:
    p = (base_dir / rel_dir / fname).resolve()
    if p.is_file():
        return _load_json(p)
    return None


def get_tags_catalog(*, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Возвращает tags/catalog.json из ae_presets.
    """
    rkey, paths = _root_key(style_id)
    if rkey in _TAGS_CATALOG_BY_ROOT:
        return copy.deepcopy(_TAGS_CATALOG_BY_ROOT[rkey])

    cat_path = paths.get("tags_catalog")
    if not isinstance(cat_path, Path) or not cat_path.is_file():
        _TAGS_CATALOG_BY_ROOT[rkey] = {}
        return {}

    data = _load_json(cat_path)
    _TAGS_CATALOG_BY_ROOT[rkey] = data
    return copy.deepcopy(data)


def get_tag_pack(tag_id: str, *, style_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Возвращает runtime tag pack (manifest + loaded files) из:
      ae_presets/tags/packs/<tag_id>/manifest.json
    """
    if not tag_id:
        return {}

    rkey, paths = _root_key(style_id)
    cache_key = (rkey, tag_id)
    if cache_key in _TAG_PACK_CACHE_BY_ROOT:
        return copy.deepcopy(_TAG_PACK_CACHE_BY_ROOT[cache_key])

    packs_dir = paths.get("tags_packs_dir")
    if not isinstance(packs_dir, Path):
        _TAG_PACK_CACHE_BY_ROOT[cache_key] = {}
        return {}

    base_dir = (packs_dir / tag_id).resolve()
    manifest_path = (base_dir / "manifest.json").resolve()
    if not manifest_path.is_file():
        _TAG_PACK_CACHE_BY_ROOT[cache_key] = {}
        return {}

    manifest = _load_json(manifest_path)
    files = manifest.get("files") or {}
    text_files = (files.get("text_layer") or {}) if isinstance(files, dict) else {}
    adj_files = (files.get("adjustment_layer") or {}) if isinstance(files, dict) else {}

    def load_domain(role_dir: str, domain_name: str) -> Dict[str, Any]:
        spec = (text_files if role_dir == "text_layer" else adj_files).get(domain_name) or {}
        if not isinstance(spec, dict):
            return {}
        out: Dict[str, Any] = {}
        tpl = spec.get("template")
        kf = spec.get("keyframes")
        if isinstance(tpl, str) and tpl:
            out["template"] = _load_pair(base_dir, role_dir, tpl) or {}
        if isinstance(kf, str) and kf:
            out["keyframes"] = _load_pair(base_dir, role_dir, kf) or {}
        return out

    pack: Dict[str, Any] = {
        "tagId": manifest.get("tagId") or tag_id,
        "label": manifest.get("label") or tag_id,
        "requires_words": bool(manifest.get("requires_words", False)),
        "refs": manifest.get("refs") or {},
        "manifest": manifest,
        "layers": {
            "text": {
                "__raw__": {
                    "transform": load_domain("text_layer", "transform"),
                    "textDoc": load_domain("text_layer", "textDoc"),
                    "textAnim": load_domain("text_layer", "textAnim"),
                    "effects": load_domain("text_layer", "effects"),
                },
            },
            "adjustment": {
                "__raw__": {
                    "transform": load_domain("adjustment_layer", "transform"),
                    "effects": load_domain("adjustment_layer", "effects"),
                },
            },
        },
    }

    _TAG_PACK_CACHE_BY_ROOT[cache_key] = pack
    return copy.deepcopy(pack)
