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


class TextAnimPresetId(str, Enum):
    """
    Идентификаторы пресетов анимации текста (ключи из text_motion_library.json:textAnimPresets).
    """

    REVEAL_OPACITY = "anim_reveal_opacity"
    STATIC = "anim_static"


class TransformPresetId(str, Enum):
    """
    Идентификаторы пресетов трансформации (ключи из text_motion_library.json:transformPresets).
    """

    SUBTITLE_BASE = "tf_subtitle_base"
