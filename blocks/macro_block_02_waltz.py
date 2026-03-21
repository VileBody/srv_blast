# blocks/macro_block_02_waltz.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import FPS, INTRO_BLENDING_MODE_CODE, INTRO_TEXT_BASE
from core.stepper import StepperConfig, Token, build_percent_keyframes_by_words, keyframe_match_name
from core.text_rules import apply_style_rule
from core.types import KeyframeData, KeyframeEase, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =========================
# Local helpers (self-contained)
# =========================

def dt() -> float:
    if float(FPS) <= 0:
        raise ValueError("config.FPS must be > 0")
    return 1.0 / float(FPS)


def t_of(in_p: float, frames_from_in: int) -> float:
    return float(in_p) + float(frames_from_in) * dt()


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
# Layers
# =========================

@dataclass
class TextLayerWaltz:
    phrase: str
    in_p: float
    out_p: float
    z_index: int
    source_rect: Dict[str, float]
    tf_key: str
    tokens_data: Optional[List[Token]]

    def __post_init__(self) -> None:
        if not self.tokens_data:
            raise ValueError("Waltz text requires tokens (tokens-only mode).")

        self.blueprint = LayerBlueprint(
            name=self.phrase.replace("\r", " "),
            type="text",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            text=self.phrase,
            source_rect=self.source_rect,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0}
        self.blueprint.text_data["text_base"] = dict(INTRO_TEXT_BASE)
        self.blueprint.text_data["char_styles_ungrouped"] = apply_style_rule(self.phrase)

        apply_tf_from_config(self.blueprint, self.tf_key)

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

        cfg = StepperConfig(
            percent_prop="start",
            anchor="start",
            start_word=0,
            hold=True,
            fps=float(FPS),
            jump_frames=1,
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
        self.blueprint.text_data["layer_styles_enabled"] = False


@dataclass
class AdjImpactBlur:
    in_p: float
    out_p: float
    z_index: int = 21

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            "Adj Impact Blur",
            "adjustment",
            float(self.in_p),
            float(self.out_p),
            int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0}
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")
        self.blueprint.effects["ADBE Motion Blur"] = {
            "0001": PropertyData("ADBE Motion Blur-0001", value=90),
            "0002": PropertyData("ADBE Motion Blur-0002", value=50),
        }


@dataclass
class AdjSquish:
    in_p: float
    out_p: float
    z_index: int = 20

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            "Adj Squish",
            "adjustment",
            float(self.in_p),
            float(self.out_p),
            int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0}
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")
        self.blueprint.effects["ADBE Geometry2"] = {
            "0003": PropertyData("ADBE Geometry2-0003", value=200),
            "0011": PropertyData("ADBE Geometry2-0011", value=0),
        }


@dataclass
class AdjSlideOut:
    in_p: float
    out_p: float
    z_index: int = 24

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            "Adj Slide Out",
            "adjustment",
            float(self.in_p),
            float(self.out_p),
            int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0}
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")
        kfs = [
            KeyframeData(t=float(self.in_p), v=100, iit="6613", oit="6613"),
            KeyframeData(t=t_of(self.in_p, 53), v=85, iit="6613", oit="6613"),
        ]
        self.blueprint.effects["ADBE Geometry2"] = {"0003": PropertyData("ADBE Geometry2-0003", keyframes=kfs)}


@dataclass
class AdjSlideIn:
    in_p: float
    out_p: float
    z_index: int = 22

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            "Adj Slide In",
            "adjustment",
            float(self.in_p),
            float(self.out_p),
            int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0}
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")
        kfs = [
            KeyframeData(t=float(self.in_p), v=85, iit="6613", oit="6613"),
            KeyframeData(t=t_of(self.in_p, 53), v=100, iit="6613", oit="6613"),
        ]
        self.blueprint.effects["ADBE Geometry2"] = {"0003": PropertyData("ADBE Geometry2-0003", keyframes=kfs)}


# =========================
# Distributor
# =========================

class WaltzDistributor:
    def __init__(self, data: Dict[str, Any]):
        p1 = data["p1"]
        p2 = data["p2"]

        seam = float(p2["in_out"][0])
        end = float(p2["in_out"][1])
        one_frame = dt()

        self.impact = AdjImpactBlur(seam - one_frame, seam + one_frame).blueprint
        self.squish = AdjSquish(end - one_frame, end).blueprint

        slide_out_out = t_of(float(p1["in_out"][0]), 39)
        slide_in_out = t_of(float(p2["in_out"][0]), 39)

        self.slide_out = AdjSlideOut(float(p1["in_out"][0]), slide_out_out).blueprint
        self.slide_in = AdjSlideIn(float(p2["in_out"][0]), slide_in_out).blueprint

        self.txt1 = TextLayerWaltz(
            phrase=p1["phrase"],
            in_p=float(p1["in_out"][0]),
            out_p=float(p1["in_out"][1]),
            z_index=25,
            source_rect=p1["s_rect"],
            tf_key="WALTZ_P1_TEXT",
            tokens_data=tokens_from_data(p1),
        ).blueprint

        self.txt2 = TextLayerWaltz(
            phrase=p2["phrase"],
            in_p=float(p2["in_out"][0]),
            out_p=float(p2["in_out"][1]),
            z_index=23,
            source_rect=p2["s_rect"],
            tf_key="WALTZ_P2_TEXT",
            tokens_data=tokens_from_data(p2),
        ).blueprint

        self.layers = [self.squish, self.impact, self.slide_in, self.txt2, self.slide_out, self.txt1]
