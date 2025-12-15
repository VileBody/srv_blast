from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
STYLES_DIR = REPO_ROOT / "config" / "styles"
DEFAULT_STYLE_PACK = "pop-music"


@dataclass(frozen=True)
class StylePackPaths:
    pack_name: str
    base_dir: Path
    text_styles: Path
    footage_presets: Path
    text_motion_library: Path
    project_settings_template: Path


@dataclass(frozen=True)
class StylePack:
    paths: StylePackPaths
    text_styles: Dict[str, Any]
    footage_presets: Dict[str, Any]
    motion_library: Dict[str, Any]
    project_settings_template: Dict[str, Any]

    @property
    def key_templates(self) -> Dict[str, Any]:
        return self.motion_library.get("keyTemplates") or {}

    @property
    def text_anim_presets(self) -> Dict[str, Any]:
        return self.motion_library.get("textAnimPresets") or {}

    @property
    def transform_presets(self) -> Dict[str, Any]:
        return self.motion_library.get("transformPresets") or {}

    @property
    def project_defaults(self) -> Dict[str, Any]:
        tpl = self.project_settings_template if isinstance(self.project_settings_template, dict) else {}
        if isinstance(tpl.get("defaults"), dict):
            return tpl.get("defaults") or {}
        # backward compat: allow defaults on root level
        return tpl

    @property
    def name(self) -> str:
        return self.paths.pack_name

    @property
    def dir(self) -> Path:
        return self.paths.base_dir


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        print(f"[WARN] JSON file not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Failed to decode JSON {path}: {exc}")
        return {}


def _resolve_pack_dir(pack_name: Optional[str]) -> tuple[str, Path]:
    name = (pack_name or os.getenv("AE_STYLE_PACK") or DEFAULT_STYLE_PACK).strip()
    candidate = STYLES_DIR / name
    if candidate.is_dir():
        return name, candidate
    return name, STYLES_DIR


def load_style_pack(
    *,
    style_pack: Optional[str] = None,
    text_styles_path: Optional[Path] = None,
    footage_presets_path: Optional[Path] = None,
    text_motion_library_path: Optional[Path] = None,
    project_settings_template_path: Optional[Path] = None,
) -> StylePack:
    """
    Load config JSONs for a style pack.

    - If style_pack exists under config/styles/<style_pack>, we use it.
    - Otherwise fallback to legacy config/styles/.
    - Individual *_path args override computed defaults (backward compat with older API).
    """
    pack_name, base_dir = _resolve_pack_dir(style_pack)

    paths = StylePackPaths(
        pack_name=pack_name,
        base_dir=base_dir,
        text_styles=(text_styles_path or (base_dir / "text_styles.json")),
        footage_presets=(footage_presets_path or (base_dir / "footage_presets.json")),
        text_motion_library=(text_motion_library_path or (base_dir / "text_motion_library.json")),
        project_settings_template=(project_settings_template_path or (base_dir / "project_settings_template.json")),
    )

    return StylePack(
        paths=paths,
        text_styles=_load_json(paths.text_styles),
        footage_presets=_load_json(paths.footage_presets),
        motion_library=_load_json(paths.text_motion_library),
        project_settings_template=_load_json(paths.project_settings_template),
    )

