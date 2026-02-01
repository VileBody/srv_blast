# blocks/macro_block_06_dual.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import FPS, INTRO_BLENDING_MODE_CODE, INTRO_TEXT_BASE
from core.stepper import StepperConfig, Token, build_percent_keyframes_by_words, keyframe_match_name
from core.text_rules import apply_style_rule
from core.types import KeyframeData, KeyframeEase, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =============================================================================
# Local helpers
# =============================================================================

DUAL_USE_HOLD_JUMP = True
PERCENT_EASE_INFLUENCE = 16.666666667
PERCENT_IIT = "6612"
PERCENT_OIT = "6612"


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


def _apply_percent_ease_like_dump(kfs: List[KeyframeData]) -> List[KeyframeData]:
    """
    Делает как в оригинальных AE-дампах:
      - iit/oit = 6612
      - ease speed = abs(Δv)/Δt между соседними ключами
      - influence фикс 16.666666667
      - ease_out на предыдущем, ease_in на следующем
    """
    if not kfs:
        return []

    kfs_sorted = sorted(kfs, key=lambda x: float(x.t))
    out: List[KeyframeData] = [
        KeyframeData(t=float(k.t), v=float(k.v), iit=PERCENT_IIT, oit=PERCENT_OIT) for k in kfs_sorted
    ]
    inf = float(PERCENT_EASE_INFLUENCE)

    for i in range(len(out) - 1):
        a = out[i]
        b = out[i + 1]
        dt = float(b.t) - float(a.t)
        dv = float(b.v) - float(a.v)
        speed = 0.0 if dt <= 0 else abs(dv) / dt
        a.ease_out = [KeyframeEase(speed=speed, influence=inf)]
        b.ease_in = [KeyframeEase(speed=speed, influence=inf)]

    return out


# =============================================================================
# Layers
# =============================================================================

class TextLayerDual:
    def __init__(
        self,
        phrase: str,
        io: List[float],
        z: int,
        s_rect: Dict[str, Any],
        mode: str,  # "fill" | "stroke"
        shared_kfs: List[KeyframeData],
    ):
        self.blueprint = LayerBlueprint(
            name=f"txt_dual_{mode}",
            type="text",
            in_point=float(io[0]),
            out_point=float(io[1]),
            z_index=int(z),
            text=phrase,
            source_rect=s_rect,
        )

        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": 0,
        }

        base = dict(INTRO_TEXT_BASE)

        def _set_text_animator(percent_start: float, percent_end: float) -> None:
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
                    "percentStart": float(percent_start),
                    "percentEnd": float(percent_end),
                },
            }

        # baseline animator opacity property (used by JSX to set Animator Text Opacity)
        self.blueprint.props["anim_opacity"] = PropertyData("ADBE Opacity", value=0)

        if mode == "stroke":
            base["applyFill"] = False
            base["applyStroke"] = True
            base["strokeWidth"] = 5
            base["strokeColor"] = [1, 1, 1]
            self.blueprint.text_data["text_base"] = base
            self.blueprint.text_data["char_styles_ungrouped"] = apply_style_rule(phrase, "dual_outline")
            apply_tf_from_config(self.blueprint, "DUAL_STROKE")

            # stroke: selector clamped; reveal uses Percent End
            _set_text_animator(percent_start=0, percent_end=0)

            cfg = StepperConfig(
                percent_prop="end",
                anchor="end",
                start_word=0,
                hold=False,
                fps=float(FPS),
            )
            self.blueprint.props["reveal_end"] = PropertyData(keyframe_match_name(cfg), keyframes=shared_kfs)

        else:
            base["applyFill"] = True
            base["applyStroke"] = False
            self.blueprint.text_data["text_base"] = base
            self.blueprint.text_data["char_styles_ungrouped"] = apply_style_rule(phrase, "break_after_r")
            apply_tf_from_config(self.blueprint, "DUAL_FILL")

            # fill: normal reveal uses Percent Start
            _set_text_animator(percent_start=0, percent_end=100)

            cfg = StepperConfig(
                percent_prop="start",
                anchor="end",
                start_word=0,
                hold=False,
                fps=float(FPS),
            )
            self.blueprint.props["reveal"] = PropertyData(keyframe_match_name(cfg), keyframes=shared_kfs)

        self.blueprint.effects["ADBE Turbulent Displace"] = {
            "0002": PropertyData("ADBE Turbulent Displace-0002", value=7.5),
            "0006": PropertyData("ADBE Turbulent Displace-0006", expression="time*500"),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


class AdjDualPhysics:
    def __init__(self, in_p: float, out_p: float):
        self.blueprint = LayerBlueprint(
            name="Adj 2 Fluid",
            type="adjustment",
            in_point=float(in_p),
            out_point=float(out_p),
            z_index=6,
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": 0,
        }

        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")

        self.blueprint.effects["ADBE Geometry2"] = {
            "0003": PropertyData(
                "ADBE Geometry2-0003",
                keyframes=[
                    KeyframeData(t=float(in_p), v=125, ease_out=[_kfe(-2997, 0.17)]),
                    KeyframeData(t=float(in_p) + 1.46, v=102.3, ease_in=[_kfe(-3.15, 99.9)], ease_out=[_kfe(24.8, 16)]),
                    KeyframeData(t=float(out_p) + 0.38, v=115, ease_in=[_kfe(14.19, 29)]),
                ],
            ),
            "0007": PropertyData(
                "ADBE Geometry2-0007",
                keyframes=[
                    KeyframeData(t=float(in_p), v=-5, ease_out=[_kfe(599.4, 0.1)]),
                    KeyframeData(t=float(out_p) + 0.38, v=0, ease_in=[_kfe(0, 100)]),
                ],
            ),
            "0011": PropertyData("ADBE Geometry2-0011", value=1),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Distributor
# =============================================================================

class DualTruthDistributor:
    def __init__(self, data: Dict[str, Any]):
        io = data["in_out"]
        phrase = data["phrase"]
        s_rect = data["s_rect"]

        tokens = tokens_from_data(data)
        if not tokens:
            raise ValueError("DualTruthDistributor requires tokens (tokens-only mode).")

        cfg_shared = StepperConfig(
            percent_prop="start",
            anchor="end",
            start_word=0,
            hold=DUAL_USE_HOLD_JUMP,
            fps=float(FPS),
            jump_frames=1,
            ease_speed=599.4,
            ease_influence=PERCENT_EASE_INFLUENCE,
            iit=PERCENT_IIT,
            oit=PERCENT_OIT,
        )

        raw_kfs = build_percent_keyframes_by_words(tokens, cfg_shared)
        shared_kfs = _apply_percent_ease_like_dump(raw_kfs)

        self.adj = AdjDualPhysics(io[0], io[1]).blueprint
        self.fill = TextLayerDual(phrase, io, 7, s_rect, "fill", shared_kfs).blueprint
        self.stroke = TextLayerDual(phrase, io, 8, s_rect, "stroke", shared_kfs).blueprint

        self.layers = [self.adj, self.fill, self.stroke]
