# blocks/macro_block_01_intro.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config import (
    FPS,
    INTRO_ADJ_START_TIME,
    INTRO_BLENDING_MODE_CODE,
    INTRO_TEXT_BASE,
    INTRO_TEXT_START_TIME,
)
from core.stepper import StepperConfig, Token, build_percent_keyframes_by_words, keyframe_match_name
from core.text_rules import apply_style_rule
from core.types import KeyframeData, KeyframeEase, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =========================
# Local helpers (self-contained)
# =========================

def _dt() -> float:
    if float(FPS) <= 0:
        raise ValueError("config.FPS must be > 0")
    return 1.0 / float(FPS)


def _t(in_p: float, frames_from_in: int) -> float:
    return float(in_p) + float(frames_from_in) * _dt()


def _kfe(speed: float, influence: float) -> KeyframeEase:
    return KeyframeEase(speed=float(speed), influence=float(influence))


def tokens_from_data(d: Dict[str, Any]) -> Optional[List[Token]]:
    raw = d.get("tokens")
    if not raw:
        return None
    out: List[Token] = []
    for it in raw:
        out.append(
            Token(
                text=str(it["text"]),
                t_start=float(it["t_start"]),
                t_end=float(it["t_end"]),
                trailing=str(it.get("trailing", "")),
            )
        )
    return out


# =========================
# Presets enums
# =========================

class IntroAdjFxPreset(Enum):
    V1 = "intro_adj_v1"


class IntroTextFxPreset(Enum):
    V1 = "intro_text_v1"


# =========================
# Layers
# =========================

@dataclass
class AdjLayerIntro:
    in_p: float
    out_p: float
    z_index: int
    name: str = "Adjustment Layer 11"
    fx_preset: IntroAdjFxPreset = IntroAdjFxPreset.V1

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name=self.name,
            type="adjustment",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            adjustment_layer=True,
        )

        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": float(INTRO_ADJ_START_TIME),
            "enabled": True,
            "threeDLayer": False,
            "timeRemapEnabled": False,
        }

        apply_tf_from_config(self.blueprint, "INTRO_ADJ_11")

        t0 = float(self.in_p)
        t1 = _t(self.in_p, 53)

        scale_h_kfs = [
            KeyframeData(t=t0, v=85, iit="6613", oit="6613", ease_out=[_kfe(115.356226415094, 4)]),
            KeyframeData(t=t1, v=100, iit="6613", oit="6613", ease_in=[_kfe(0.64285203574975, 95)]),
        ]
        rot_kfs = [
            KeyframeData(t=t0, v=-5, iit="6613", oit="6613", ease_out=[_kfe(38.4520754716981, 4)]),
            KeyframeData(t=t1, v=0, iit="6613", oit="6613", ease_in=[_kfe(0.21428401191658, 95)]),
        ]

        self.blueprint.effects["ADBE Geometry2"] = {
            "0001": PropertyData("ADBE Geometry2-0001", value=[540, 960]),
            "0002": PropertyData("ADBE Geometry2-0002", value=[540, 960]),
            "0003": PropertyData("ADBE Geometry2-0003", keyframes=scale_h_kfs),
            "0004": PropertyData("ADBE Geometry2-0004", value=100),
            "0005": PropertyData("ADBE Geometry2-0005", value=0),
            "0006": PropertyData("ADBE Geometry2-0006", value=0),
            "0007": PropertyData("ADBE Geometry2-0007", keyframes=rot_kfs),
            "0008": PropertyData("ADBE Geometry2-0008", value=100),
            "0009": PropertyData("ADBE Geometry2-0009", value=1),
            "0010": PropertyData("ADBE Geometry2-0010", value=0),
            "0011": PropertyData("ADBE Geometry2-0011", value=1),
            "0012": PropertyData("ADBE Geometry2-0012", value=1),
        }


@dataclass
class TextLayerIntro:
    phrase: str
    in_p: float
    out_p: float
    z_index: int
    source_rect: Dict[str, float]
    tokens_data: Optional[List[Token]] = None
    fx_preset: IntroTextFxPreset = IntroTextFxPreset.V1

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name=self.phrase.replace("\r", " "),
            type="text",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            text=self.phrase,
            source_rect=self.source_rect,
        )

        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": float(INTRO_TEXT_START_TIME),
        }

        self.blueprint.text_data["text_base"] = dict(INTRO_TEXT_BASE)
        self.blueprint.text_data["char_styles_ungrouped"] = apply_style_rule(self.phrase, "break_after_r")

        apply_tf_from_config(self.blueprint, "INTRO_TEXT")

        # Animator baseline
        self.blueprint.text_data["text_animator"] = {
            "name": "Animator 1",
            "opacity": 0,
            "selector": {
                "name": "Range Selector 1",
                "advanced": {
                    "units": 1,
                    "basedOn": 3,
                    "mode": 1,
                    "maxAmount": 100,
                    "shape": 1,
                    "smoothness": 0,
                    "hiEase": 0,
                    "loEase": 0,
                    "randomizeOrder": 0,
                },
                "percentEnd": 100,
            },
        }

        # Reveal (tokens-only preferred, but Intro allows empty tokens -> no reveal keys)
        if self.tokens_data:
            cfg = StepperConfig(
                percent_prop="start",
                anchor="end",
                start_word=0,
                hold=True,
                fps=float(FPS),
                jump_frames=1,
                ease_speed=599.4,
                ease_influence=16.666666667,
                iit="6612",
                oit="6612",
            )
            kfs = build_percent_keyframes_by_words(self.tokens_data, cfg)
            self.blueprint.props["reveal"] = PropertyData(keyframe_match_name(cfg), keyframes=kfs)

        # FX baseline
        self.blueprint.effects["ADBE Turbulent Displace"] = {
            "0002": PropertyData("ADBE Turbulent Displace-0002", value=7.5),
            "0006": PropertyData("ADBE Turbulent Displace-0006", expression="time*500"),
        }
        self.blueprint.effects["ADBE Posterize Time"] = {"0001": PropertyData("ADBE Posterize Time-0001", value=5)}

        mm_radius_kfs = [
            KeyframeData(
                t=float(self.in_p),
                v=15,
                iit="6612",
                oit="6612",
                ease_out=[_kfe(-359.64, 16.666666667)],
            ),
            KeyframeData(
                t=_t(self.in_p, 1),
                v=0,
                iit="6612",
                oit="6612",
                ease_in=[_kfe(-359.64, 16.666666667)],
            ),
        ]
        self.blueprint.effects["ADBE Minimax"] = {
            "0001": PropertyData("ADBE Minimax-0001", value=2),
            "0002": PropertyData("ADBE Minimax-0002", keyframes=mm_radius_kfs),
        }

        self.blueprint.text_data["layer_styles_enabled"] = False


# =========================
# Distributor
# =========================

class IntroDistributor:
    def __init__(self, data: Dict[str, Any]):
        in_p, out_p = data["in_out"]
        phrase = data["phrase"]
        s_rect = data["s_rect"]
        tokens = tokens_from_data(data)

        self.adj_obj = AdjLayerIntro(float(in_p), float(out_p), z_index=26)
        self.txt_obj = TextLayerIntro(
            phrase=phrase,
            in_p=float(in_p),
            out_p=float(out_p),
            z_index=27,
            source_rect=s_rect,
            tokens_data=tokens,
        )
        self.layers = [self.adj_obj.blueprint, self.txt_obj.blueprint]
