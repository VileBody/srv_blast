from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import FPS, INTRO_BLENDING_MODE_CODE, INTRO_TEXT_BASE
from core.stepper import StepperConfig, Token, build_percent_keyframes_by_words, keyframe_match_name
from core.text_rules import apply_style_rule
from core.types import KeyframeData, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =========================
# Local helpers (self-contained)
# =========================

def dt() -> float:
    if float(FPS) <= 0:
        raise ValueError("config.FPS must be > 0")
    return 1.0 / float(FPS)


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
class TextLayerPhoto:
    phrase: str
    in_p: float
    out_p: float
    z_index: int
    source_rect: Dict[str, float]
    tokens_data: Optional[List[Token]]

    def __post_init__(self) -> None:
        if not self.tokens_data:
            raise ValueError("Photo text requires tokens (tokens-only mode).")

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

        apply_tf_from_config(self.blueprint, "PHOTO_TEXT")

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

        # Photo: hold=False
        cfg = StepperConfig(
            percent_prop="start",
            anchor="end",
            start_word=0,
            hold=False,
            fps=float(FPS),
            iit="6612",
            oit="6612",
        )
        kfs = build_percent_keyframes_by_words(self.tokens_data, cfg)

        # ensure last key not earlier than out-1frame
        last_t = float(self.out_p) - (1.0 / float(FPS))
        if kfs and float(kfs[-1].t) < float(last_t):
            kfs.append(KeyframeData(t=float(last_t), v=kfs[-1].v, iit=cfg.iit, oit=cfg.oit))

        self.blueprint.props["reveal"] = PropertyData(keyframe_match_name(cfg), keyframes=kfs)
        self.blueprint.props["anim_opacity"] = PropertyData("ADBE Opacity", value=0)

        self.blueprint.effects["ADBE Turbulent Displace"] = {
            "0002": PropertyData("ADBE Turbulent Displace-0002", value=7.5),
            "0006": PropertyData("ADBE Turbulent Displace-0006", expression="time*500"),
        }
        self.blueprint.effects["ADBE Posterize Time"] = {"0001": PropertyData("ADBE Posterize Time-0001", value=5)}
        self.blueprint.text_data["layer_styles_enabled"] = False


@dataclass
class AdjPhotoSquish:
    in_p: float
    out_p: float
    z_index: int = 18

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name="Adj 5 Squish (H)",
            type="adjustment",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0}
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")
        self.blueprint.effects["ADBE Geometry2"] = {
            "0004": PropertyData("ADBE Geometry2-0004", value=150),
            "0011": PropertyData("ADBE Geometry2-0011", value=0),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


# =========================
# Distributor
# =========================

class PhotoDistributor:
    def __init__(self, data: Dict[str, Any]):
        in_p, out_p = data["in_out"]
        phrase = data["phrase"]
        tokens = tokens_from_data(data)

        self.txt = TextLayerPhoto(
            phrase=phrase,
            in_p=float(in_p),
            out_p=float(out_p),
            z_index=19,
            source_rect=data["s_rect"],
            tokens_data=tokens,
        ).blueprint

        one_frame = dt()
        self.squish = AdjPhotoSquish(float(out_p) - one_frame, float(out_p)).blueprint
        self.layers = [self.squish, self.txt]
