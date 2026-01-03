from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


def _repo_root() -> Path:
    """
    Определяем корень репозитория (/app в контейнере) по расположению файла:
    src/config/styles/paths.py -> parents[3] == <repo_root>
    """
    return Path(__file__).resolve().parents[3]


STYLES_BASE_DIR: Path = (_repo_root() / "config" / "styles").resolve()

# Минимальный набор файлов, по которому мы считаем style root валидным.
_REQUIRED_REL: List[str] = [
    "project/project_settings_template.json",
    "text/text_styles.json",
    "text/text_fx_combos.json",
    "text/text_motion_library.json",
    "footage/footage_presets.json",
    "effects/effects_library.json",
]


def _is_valid_styles_root(root: Path) -> bool:
    return all((root / rel).is_file() for rel in _REQUIRED_REL)


def list_style_ids() -> List[str]:
    """
    Возвращает список style_id (подпапок) в config/styles/,
    отсортированный детерминированно.
    """
    if not STYLES_BASE_DIR.exists() or not STYLES_BASE_DIR.is_dir():
        return []
    out: List[str] = []
    for d in sorted(STYLES_BASE_DIR.iterdir()):
        if d.is_dir() and _is_valid_styles_root(d):
            out.append(d.name)
    return out


def _auto_detect_styles_root() -> Path:
    """
    Авто-выбор style root:
      1) если config/styles/ сам по себе валиден (legacy-flat) — используем его.
      2) иначе foreach по подпапкам config/styles/<style_id>/ — берём первую валидную (sorted).

    Никаких падений на import — проверка происходит только при вызове.
    """
    # legacy-flat (если вдруг у кого-то останется)
    if _is_valid_styles_root(STYLES_BASE_DIR):
        return STYLES_BASE_DIR

    for sid in list_style_ids():
        return (STYLES_BASE_DIR / sid).resolve()

    raise FileNotFoundError(
        "Missing styles assets.\n"
        f"Styles base: {STYLES_BASE_DIR}\n"
        "Expected either:\n"
        "  config/styles/<style_id>/{project,text,footage,effects}/...\n"
        "or (not recommended):\n"
        "  config/styles/{project,text,footage,effects}/... (legacy-flat)\n"
        "Required files:\n  - " + "\n  - ".join(_REQUIRED_REL)
    )


def get_styles_root(style_id: Optional[str] = None) -> Path:
    """
    Возвращает валидный root для стилей.
    Если style_id задан — строго берём config/styles/<style_id>.
    Иначе — автодетект.
    """
    if style_id:
        cand = (STYLES_BASE_DIR / style_id).resolve()
        if not _is_valid_styles_root(cand):
            raise FileNotFoundError(
                f"Invalid style_id={style_id!r}. "
                f"Expected required files under: {cand}\n"
                "Have valid style_ids: " + ", ".join(list_style_ids())
            )
        return cand
    return _auto_detect_styles_root()


def get_style_paths(style_id: Optional[str] = None) -> Dict[str, Path]:
    """
    Главная точка входа для всего кода.
    Возвращает пути к JSON-библиотекам и tag-папкам.
    """
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
