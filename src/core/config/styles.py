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


STYLES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "styles"
TEXT_STYLES_PATH = STYLES_DIR / "text_styles.json"
FOOTAGE_PRESETS_PATH = STYLES_DIR / "footage_presets.json"
MOTION_LIBRARY_PATH = STYLES_DIR / "text_motion_library.json"
EFFECTS_LIBRARY_PATH = STYLES_DIR / "effects_library.json"
TEXT_FX_LIBRARY_PATH = STYLES_DIR / "text_fx_library.json"
TEXT_FX_COMBOS_PATH = STYLES_DIR / "text_fx_combos.json"
