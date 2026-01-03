from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Dict

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]

STYLES_BASE_DIR: Path = (_repo_root() / "config" / "styles").resolve()

_REQUIRED_REL = [
    "project/project_settings_template.json",
    "text/text_styles.json",
    "text/text_fx_combos.json",
    "text/text_motion_library.json",
    "footage/footage_presets.json",
    "effects/effects_library.json",
]

def _is_valid_styles_root(root: Path) -> bool:
    return all((root / rel).is_file() for rel in _REQUIRED_REL)

def _auto_detect_styles_root() -> Path:
    # 1) legacy flat
    if _is_valid_styles_root(STYLES_BASE_DIR):
        return STYLES_BASE_DIR

    # 2) foreach style_id (sorted = deterministic)
    if STYLES_BASE_DIR.is_dir():
        for d in sorted(STYLES_BASE_DIR.iterdir()):
            if d.is_dir() and _is_valid_styles_root(d):
                return d

    raise FileNotFoundError(
        "Missing styles asset.\n"
        f"Styles base: {STYLES_BASE_DIR}\n"
        "Expected either:\n"
        "  config/styles/project/... (legacy flat)\n"
        "or:\n"
        "  config/styles/<style_id>/project/... (multi-style)\n"
    )

def get_styles_root(style_id: Optional[str] = None) -> Path:
    if style_id:
        cand = (STYLES_BASE_DIR / style_id).resolve()
        if _is_valid_styles_root(cand):
            return cand
        raise FileNotFoundError(f"Style '{style_id}' not found or incomplete under {STYLES_BASE_DIR}")
    return _auto_detect_styles_root()

def get_style_paths(style_id: Optional[str] = None) -> Dict[str, Path]:
    root = get_styles_root(style_id)
    return {
        "root": root,
        "project_settings": root / "project" / "project_settings_template.json",
        "text_styles": root / "text" / "text_styles.json",
        "text_fx": root / "text" / "text_fx_combos.json",
        "motion": root / "text" / "text_motion_library.json",
        "footage": root / "footage" / "footage_presets.json",
        "effects": root / "effects" / "effects_library.json",
        "tags_dir": root / "tags",
        "tags_packs_dir": root / "tags" / "packs",
    }
