# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Final

from core.video_timing import AE_FPS

# ============================================================
# Global timing
# ============================================================
# Используй реальное значение из AE (comp.frameRate), а не "24".
FPS: Final[float] = float(AE_FPS)

def DT() -> float:
    return 1.0 / FPS


# ============================================================
# Global style rules (твои решения)
# ============================================================
# По умолчанию: до \r SemiBold 100, после \r ExtraBold 200 (кроме MINE).
STYLE_RULE_DEFAULT: Final[str] = "break_after_r"

# Разрешенные “особые” правила (ты сказал: только эти)
STYLE_RULE_DUAL_OUTLINE: Final[str] = "dual_outline"
STYLE_RULE_MINE_INNER: Final[str] = "mine_inner"

# Как считать чанки -> видимые символы (если где-то будешь вычислять)
COUNT_R_AS_SYMBOL: Final[bool] = True
INCLUDE_SPACE_AFTER_WORD: Final[bool] = True


# ============================================================
# Code mappings from dumps (kept as string codes)
# ============================================================
INTRO_BLENDING_MODE_CODE: Final[str] = "5212"  # Normal
JUSTIFICATION_CENTER_CODE: Final[str] = "7415" # CENTER_JUSTIFY


# ============================================================
# Default TextDocument base (используется в большинстве блоков)
# ============================================================
TEXT_BASE_DEFAULT: Dict[str, Any] = {
    "font": "Point-SemiBold",
    "fontSize": 100,

    "applyFill": True,
    "fillColor": [1, 1, 1],

    "applyStroke": False,
    "strokeWidth": 7,
    # strokeColor можно задавать при dual_outline/mine_inner
    # "strokeColor": [1, 1, 1],

    "tracking": -50,
    "leading": 150,
    "autoLeading": False,

    "justificationCode": JUSTIFICATION_CENTER_CODE,
    "allCaps": True,

    "leftIndent": 0,
    "rightIndent": 0,
    "firstLineIndent": -60,
    "spaceBefore": 0,
    "spaceAfter": 0,
}

# Алиас, чтобы старые блоки не ломались
INTRO_TEXT_BASE = TEXT_BASE_DEFAULT


# ============================================================
# Default TF baseline
# ============================================================
TF_BASE_DEFAULT: Dict[str, Any] = {
    "position": [540, 960, 0],
    "scale": [75, 75, 100],
    "rotationZ": 0,
    "opacity": 100,
}

# ============================================================
# Intro-specific baselines (как ты уже использовал)
# ============================================================
# (*) startTime — “мистика”, лежит в конфиге и редактируется руками.
INTRO_TEXT_START_TIME: float = 0.0   # ты сказал будешь выставлять как нравится
INTRO_ADJ_START_TIME: float = 0.0

INTRO_TEXT_TF: Dict[str, Any] = {
    "anchorPoint": [-56.435546875, 40.8935546875, 0],
    **TF_BASE_DEFAULT,
    "scale": [75, 75, 100],
}

INTRO_ADJ_TF: Dict[str, Any] = {
    "anchorPoint": [540, 960, 0],
    "position": [540, 960, 0],
    "scale": [100, 100, 100],
    "rotationZ": 0,
    "opacity": 100,
}
# --- BLOCKS_V3_TF_LAYERS_START ---
# TF for layers (hardcoded from dumps). blocks_v3 imports these.
# Any field can be None to indicate "do not set".
# IMPORTANT: if a layer has keyframed opacity, set opacity=None here to avoid setValue() error.

# Default for adjustment solids / generic layers
TF_LAYERS = {
    "ADJ_SOLID_DEFAULT": {
        "anchorPoint": [540, 960, 0],
        "position": [540, 960, 0],
        "scale": [100, 100, 100],
        "rotationZ": 0,
        "opacity": 100,
    },

    # Intro
    # If INTRO_TEXT_TF / INTRO_ADJ_TF exist above, we reuse them for consistency.
    "INTRO_TEXT": {
        "anchorPoint": INTRO_TEXT_TF["anchorPoint"],
        "position": INTRO_TEXT_TF["position"],
        "scale": INTRO_TEXT_TF["scale"],
        "rotationZ": INTRO_TEXT_TF["rotationZ"],
        "opacity": INTRO_TEXT_TF["opacity"],
    },
    "INTRO_ADJ_11": {
        "anchorPoint": INTRO_ADJ_TF["anchorPoint"],
        "position": INTRO_ADJ_TF["position"],
        "scale": INTRO_ADJ_TF["scale"],
        "rotationZ": INTRO_ADJ_TF["rotationZ"],
        "opacity": INTRO_ADJ_TF["opacity"],
    },

    # Waltz
    "WALTZ_P1_TEXT": {
        "anchorPoint": [-60.1953125, 38.8427734375, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 75],
        "rotationZ": 0,
        "opacity": 100,
    },
    "WALTZ_P2_TEXT": {
        "anchorPoint": [-61.2939453125, 40.8935546875, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": 100,
    },

    # Photo
    "PHOTO_TEXT": {
        "anchorPoint": [-61.2939453125, 40.8935546875, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": 100,
    },

    # Baby
    "BABY_P1_TEXT": {
        "anchorPoint": [-56.484375, -33.49609375, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": 100,
    },
    "BABY_P2_TEXT": {
        "anchorPoint": [-61.2939453125, 40.8935546875, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": None,  # opacity keyframed -> do not set
    },

    # Glitch
    "GLITCH_HIS_EYES": {
        "anchorPoint": [-57.6806640625, -33.49609375, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": None,  # opacity keyframed -> do not set
    },
    "GLITCH_HIS_EYES_WERE": {
        "anchorPoint": [-58.7548828125, -33.49609375, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": 100,
    },
    "GLITCH_HIS_EYES_WERE_LIKE": {
        "anchorPoint": [-58.7548828125, -33.49609375, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": None,  # opacity keyframed -> do not set
    },

    # Mine (video layer in main comp)
    "MINE_VIDEO_LAYER": {
        "anchorPoint": [540, 960, 0],
        "position": [540, 960, 0],
        "scale": None,    # scale is keyframed -> do not set
        "rotationZ": 0,
        "opacity": 25,
    },
    # Mine inner (text inside compId=88)
    "MINE_INNER_TEXT": {
        "anchorPoint": [0, 0, 0],
        "position": [540, 960, 0],
        "scale": [100, 100, 100],
        "rotationZ": 0,
        "opacity": 100,
    },

"DUAL_FILL": {
    "anchorPoint": [-64.00390625, 41.7119140625, 0],
    "position": [540, 960, 0],
    "scale": [75, 75, 100],
    "rotationZ": 0,
    "opacity": 100,
},
"DUAL_STROKE": {
    "anchorPoint": [-64.00390625, 41.7119140625, 0],
    "position": [540, 960, 0],
    "scale": [75, 75, 100],
    "rotationZ": 0,
    "opacity": 100,
},


    # Finale
    "FINALE_KID": {
        "anchorPoint": [-58.7548828125, 41.5687561035156, 0],
        "position": [540, 960, 0],
        "scale": [75, 75, 100],
        "rotationZ": 0,
        "opacity": 100,
    },
    "FINALE_SON": {
        "anchorPoint": [-54.43359375, 42.3095703125, 0],
        "position": [540, 960, 0],
        "scale": [90, 90, 100],
        "rotationZ": 0,
        "opacity": None,  # opacity keyframed -> do not set
    },
}
# --- BLOCKS_V3_TF_LAYERS_END ---
