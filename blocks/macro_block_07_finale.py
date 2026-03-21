# blocks/macro_block_07_finale.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config import FPS, INTRO_BLENDING_MODE_CODE, INTRO_TEXT_BASE
from core.stepper import StepperConfig, Token, build_percent_keyframes_by_words, keyframe_match_name
from core.text_rules import apply_style_rule
from core.types import KeyframeData, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =============================================================================
# Local helpers
# =============================================================================

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


# =============================================================================
# Text layer
# =============================================================================

class FinaleTextPreset(Enum):
    KID = "kid"
    SON = "son"


@dataclass
class TextLayerFinale:
    phrase: str
    in_p: float
    out_p: float
    z_index: int
    s_rect: Dict[str, float]
    preset: FinaleTextPreset
    tokens_data: Optional[List[Token]]

    def __post_init__(self) -> None:
        if not self.tokens_data:
            raise ValueError("Finale text requires tokens (tokens-only mode).")

        name = "but the kid" if self.preset == FinaleTextPreset.KID else "is not my son"
        tf_key = "FINALE_KID" if self.preset == FinaleTextPreset.KID else "FINALE_SON"

        self.blueprint = LayerBlueprint(
            name=name,
            type="text",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            text=self.phrase,
            source_rect=self.s_rect,
        )

        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": 0,
        }
        self.blueprint.text_data["text_base"] = dict(INTRO_TEXT_BASE)
        self.blueprint.text_data["char_styles_ungrouped"] = apply_style_rule(self.phrase)

        # SON: keyframed layer opacity BEFORE TF, чтобы apply_tf_from_config не ставил static opacity
        if self.preset == FinaleTextPreset.SON:
            self.blueprint.props["layer_opacity"] = PropertyData(
                "ADBE Opacity",
                keyframes=[
                    KeyframeData(t=float(self.out_p) - 1.2095428762095, v=100, iit="6612", oit="6612"),
                    KeyframeData(t=float(self.out_p) - 0.2085418752085, v=0, iit="6612", oit="6612"),
                ],
            )

        apply_tf_from_config(self.blueprint, tf_key)

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
        self.blueprint.props["anim_opacity"] = PropertyData("ADBE Opacity", value=0)

        cfg = StepperConfig(
            percent_prop="start",
            anchor="end",
            start_word=0,
            hold=True,
            fps=float(FPS),
            jump_frames=1,
            iit="6612",
            oit="6612",
        )
        kfs = build_percent_keyframes_by_words(self.tokens_data, cfg)
        self.blueprint.props["reveal"] = PropertyData(keyframe_match_name(cfg), keyframes=kfs)

        # SON: blur tail
        if self.preset == FinaleTextPreset.SON:
            self.blueprint.effects["ADBE Box Blur2"] = {
                "0001": PropertyData(
                    "ADBE Box Blur2-0001",
                    keyframes=[
                        KeyframeData(t=float(self.out_p) - 1.1261261269176, v=0, iit="6612", oit="6612"),
                        KeyframeData(t=float(self.out_p) - 0.2085418752085, v=3, iit="6612", oit="6612"),
                    ],
                ),
                "0002": PropertyData("ADBE Box Blur2-0002", value=3),
                "0003": PropertyData("ADBE Box Blur2-0003", value=1),
                "0004": PropertyData("ADBE Box Blur2-0004", value=0),
            }

        # FX baseline
        self.blueprint.effects["ADBE Turbulent Displace"] = {
            "0002": PropertyData("ADBE Turbulent Displace-0002", value=7.5),
            "0006": PropertyData("ADBE Turbulent Displace-0006", expression="time*500"),
        }
        self.blueprint.effects["ADBE Posterize Time"] = {
            "0001": PropertyData("ADBE Posterize Time-0001", value=5),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Adjustment layer
# =============================================================================

class Adj3Mode(Enum):
    SINK = "sink"
    STABILIZE = "stabilize"


@dataclass
class AdjFinaleMotion:
    in_p: float
    out_p: float
    z_index: int
    mode: Adj3Mode

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name="Adjustment Layer 3",
            type="adjustment",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": 0,
        }

        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")

        if self.mode == Adj3Mode.SINK:
            self.blueprint.effects["ADBE Geometry2"] = {
                "0003": PropertyData(
                    "ADBE Geometry2-0003",
                    keyframes=[
                        KeyframeData(t=float(self.in_p), v=100, iit="6613", oit="6613"),
                        KeyframeData(t=float(self.in_p) + 2.3773773773774, v=85, iit="6613", oit="6613"),
                    ],
                ),
                "0011": PropertyData("ADBE Geometry2-0011", value=1),
            }
        else:
            self.blueprint.effects["ADBE Geometry2"] = {
                "0003": PropertyData(
                    "ADBE Geometry2-0003",
                    keyframes=[
                        KeyframeData(t=float(self.in_p), v=80, iit="6613", oit="6613"),
                        KeyframeData(t=float(self.in_p) + 3.4200867534201, v=100, iit="6613", oit="6613"),
                    ],
                ),
                "0011": PropertyData("ADBE Geometry2-0011", value=1),
            }

        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Distributor
# =============================================================================

class FinaleDistributor:
    def __init__(self, data: Dict[str, Any]):
        p1 = data["part1"]
        p2 = data["part2"]

        self.adj_top = AdjFinaleMotion(float(p2["in_out"][0]), float(p2["in_out"][1]), 2, Adj3Mode.STABILIZE).blueprint

        self.txt_son = TextLayerFinale(
            phrase=p2["phrase"],
            in_p=float(p2["in_out"][0]),
            out_p=float(p2["in_out"][1]),
            z_index=3,
            s_rect=p2["s_rect"],
            preset=FinaleTextPreset.SON,
            tokens_data=tokens_from_data(p2),
        ).blueprint

        self.adj_bot = AdjFinaleMotion(float(p1["in_out"][0]), float(p1["in_out"][1]), 4, Adj3Mode.SINK).blueprint

        self.txt_kid = TextLayerFinale(
            phrase=p1["phrase"],
            in_p=float(p1["in_out"][0]),
            out_p=float(p1["in_out"][1]),
            z_index=5,
            s_rect=p1["s_rect"],
            preset=FinaleTextPreset.KID,
            tokens_data=tokens_from_data(p1),
        ).blueprint

        self.layers = [self.adj_top, self.txt_son, self.adj_bot, self.txt_kid]
