from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
STYLES_ROOT = REPO_ROOT / "config" / "styles"

DEFAULT_STYLE_PACK = "pop-music"


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Decoding {path}: {exc}")
        return {}


def _resolve_pack_dir(pack_name: Optional[str]) -> Path:
    pack = pack_name or os.getenv("AE_STYLE_PACK") or DEFAULT_STYLE_PACK
    pack_dir = STYLES_ROOT / pack
    # Backward compat: if pack dir doesn't exist, fallback to legacy root folder.
    return pack_dir if pack_dir.is_dir() else STYLES_ROOT


@dataclass(frozen=True)
class StylePack:
    name: str
    dir: Path
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
        return (tpl.get("defaults") or {}) if isinstance(tpl.get("defaults"), dict) else {}


def load_style_pack(
    pack_name: Optional[str] = None,
    *,
    text_styles_filename: str = "text_styles.json",
    footage_presets_filename: str = "footage_presets.json",
    motion_library_filename: str = "text_motion_library.json",
    project_settings_filename: str = "project_settings_template.json",
) -> StylePack:
    """Load a style pack from config/styles/<pack_name>/.

    Filenames are injectable so you can reuse the loader later without hard-coding.
    """
    pack = pack_name or os.getenv("AE_STYLE_PACK") or DEFAULT_STYLE_PACK
    pack_dir = _resolve_pack_dir(pack)

    # If we're in legacy mode, keep a human-readable name
    actual_name = pack if pack_dir != STYLES_ROOT else "legacy-root"

    def p(fname: str) -> Path:
        cand = pack_dir / fname
        if cand.is_file():
            return cand
        # fallback to legacy-root path if pack dir exists but file missing
        fb = STYLES_ROOT / fname
        return fb

    text_styles = _load_json(p(text_styles_filename))
    footage_presets = _load_json(p(footage_presets_filename))
    motion_library = _load_json(p(motion_library_filename))
    project_settings_template = _load_json(p(project_settings_filename))

    return StylePack(
        name=actual_name,
        dir=pack_dir,
        text_styles=text_styles,
        footage_presets=footage_presets,
        motion_library=motion_library,
        project_settings_template=project_settings_template,
    )

