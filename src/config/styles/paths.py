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
    "ae_presets/tags/catalog.json",
]


def _is_valid_styles_root(root: Path) -> bool:
    if not all((root / rel).is_file() for rel in _REQUIRED_REL):
        return False
    packs_dir = root / "ae_presets" / "tags" / "packs"
    return packs_dir.is_dir()


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
    for sid in list_style_ids():
        return (STYLES_BASE_DIR / sid).resolve()

    raise FileNotFoundError(
        "Missing styles assets.\n"
        f"Styles base: {STYLES_BASE_DIR}\n"
        "Expected:\n"
        "  config/styles/<style_id>/ae_presets/tags/catalog.json\n"
        "  config/styles/<style_id>/ae_presets/tags/packs/\n"
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
    Главная точка входа для всего кода (новая схема стилей).
    Возвращает пути для ae_presets/tag packs.
    """
    root = get_styles_root(style_id)
    ae_presets = root / "ae_presets"
    tags_dir = ae_presets / "tags"
    return {
        "root": root,
        "ae_presets_dir": ae_presets,
        "tags_dir": tags_dir,
        "tags_catalog": tags_dir / "catalog.json",
        "tags_packs_dir": tags_dir / "packs",
        "canonical_debug_dir": ae_presets / "_canonical_debug",
    }
