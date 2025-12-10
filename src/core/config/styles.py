from enum import Enum


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
