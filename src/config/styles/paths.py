from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    """
    Определяем корень репозитория (/app в контейнере) по расположению файла:
    src/config/styles/paths.py -> parents[3] == <repo_root>
    """
    return Path(__file__).resolve().parents[3]


STYLES_DIR: Path = (_repo_root() / "config" / "styles").resolve()


def _must_exist(p: Path) -> Path:
    """
    STRICT: если файла нет — падаем сразу и громко,
    чтобы миграция плоская->деревянная не скрывала ошибки.
    """
    if not p.is_file():
        raise FileNotFoundError(
            "Missing styles asset.\n"
            f"Expected: {p}\n"
            f"Styles root: {STYLES_DIR}\n"
            "Expected tree layout:\n"
            "  config/styles/project/project_settings_template.json\n"
            "  config/styles/text/text_styles.json\n"
            "  config/styles/text/text_fx_combos.json\n"
            "  config/styles/footage/footage_presets.json\n"
            "  config/styles/effects/effects_library.json\n"
        )
    return p


PROJECT_SETTINGS_TEMPLATE_PATH: Path = _must_exist(
    STYLES_DIR / "project" / "project_settings_template.json"
)

TEXT_STYLES_PATH: Path = _must_exist(
    STYLES_DIR / "text" / "text_styles.json"
)

TEXT_FX_LIBRARY_PATH: Path = _must_exist(
    STYLES_DIR / "text" / "text_fx_combos.json"
)

FOOTAGE_PRESETS_PATH: Path = _must_exist(
    STYLES_DIR / "footage" / "footage_presets.json"
)

MOTION_LIBRARY_PATH: Path = _must_exist(
    STYLES_DIR / "text" / "text_motion_library.json"
)

EFFECTS_LIBRARY_PATH: Path = _must_exist(
    STYLES_DIR / "effects" / "effects_library.json"
)
