from enum import Enum
from pathlib import Path


class SubtitleStyle(str, Enum):
    DEFAULT = "default"
    HIGHLIGHT = "highlight"


class FootagePresetId(str, Enum):
    """
    Идентификаторы пресетов для футажей (ключи из footage_presets.json).
    """

    BG_TRANSFORM = "bg_transform"
    VERTICAL_FIT = "vertical_fit"
    SHAKE_ADJ = "shake_adj"


# БЫЛО (3 parent): ведет в /app/src
# STYLES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "styles"

# СТАЛО (4 parent): ведет в /app (корень), где лежит папка config
STYLES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "styles"

# Пути строятся от STYLES_DIR
TEXT_STYLES_PATH = STYLES_DIR / "text" / "text_styles.json"
TEXT_FX_LIBRARY_PATH = STYLES_DIR / "text" / "text_fx_combos.json"

FOOTAGE_PRESETS_PATH = STYLES_DIR / "footage" / "footage_presets.json"

MOTION_LIBRARY_PATH = STYLES_DIR / "text" / "text_motion_library.json"
EFFECTS_LIBRARY_PATH = STYLES_DIR / "effects" / "effects_library.json"

PROJECT_SETTINGS_TEMPLATE_PATH = STYLES_DIR / "project" / "project_settings_template.json"
