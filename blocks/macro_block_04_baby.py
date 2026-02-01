# blocks/macro_block_04_baby.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import FPS, INTRO_BLENDING_MODE_CODE, INTRO_TEXT_BASE
from core.stepper import StepperConfig, Token, build_percent_keyframes_by_words, keyframe_match_name
from core.text_rules import apply_style_rule
from core.types import KeyframeData, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =============================================================================
# Local helpers (self-contained)
# =============================================================================

def _dt() -> float:
    """AE dt in seconds for 1 frame (matches comp.frameRate)."""
    if float(FPS) <= 0:
        raise ValueError("config.FPS must be > 0")
    return 1.0 / float(FPS)


def _t_norm(in_p: float, out_p: float, u: float) -> float:
    """
    A2: normalize time across [in_p..out_p] by u in [0..1].

    Why here:
      - this adjustment layer motion is a "gesture" spanning the whole Baby block
      - keeping relative phase positions is more stable than absolute timestamps from another project.
    """
    in_p = float(in_p)
    out_p = float(out_p)
    if out_p <= in_p:
        return in_p
    u = max(0.0, min(1.0, float(u)))
    return in_p + (out_p - in_p) * u


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
# Layers
# =============================================================================

@dataclass
class TextLayerBaby:
    """
    NEW decision (per your request):
      - p1 and p2 are BOTH "token reveal" layers with Text Animator,
        like block_03_photo (instead of p2 being a static layer with tail blur/fade).

    Why:
      - much easier to reason about, matches your pipeline philosophy:
        "words appear when audio says they appear"
      - no hardcoded absolute keyframe times for opacity/blur
      - all timing is either:
          (a) driven by tokens (Percent Start)
          (b) driven by block in/out (only for safety clamp)
    """
    phrase: str
    in_p: float
    out_p: float
    z_index: int
    source_rect: Dict[str, float]
    tf_key: str
    tokens_data: Optional[List[Token]]

    # Photo used hold=False; we intentionally mirror that behaviour here
    # (smoother, 1 key per word, and easier to keep "last key at out-1frame").
    HOLD: bool = False

    def __post_init__(self) -> None:
        if not self.tokens_data:
            raise ValueError("Baby text requires tokens (tokens-only mode).")

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
            "startTime": 0.0,
        }
        self.blueprint.text_data["text_base"] = dict(INTRO_TEXT_BASE)
        self.blueprint.text_data["char_styles_ungrouped"] = apply_style_rule(self.phrase)

        apply_tf_from_config(self.blueprint, self.tf_key)

        # Animator baseline (exact same structure as other blocks)
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

        # Token-driven reveal (Percent Start)
        cfg = StepperConfig(
            percent_prop="start",
            anchor="end",
            start_word=0,
            hold=bool(self.HOLD),          # <-- "как в 03_photo": hold=False
            fps=float(FPS),
            jump_frames=1,                  # ignored when hold=False, kept for consistency
            iit="6612",
            oit="6612",
        )
        kfs = build_percent_keyframes_by_words(self.tokens_data, cfg)

        # Safety: ensure last key is not earlier than out-1frame (same trick as Photo).
        # Reason: if the last word ends early, AE can "finish" reveal too soon.
        last_t = float(self.out_p) - _dt()
        if kfs and float(kfs[-1].t) < float(last_t):
            kfs.append(KeyframeData(t=float(last_t), v=kfs[-1].v, iit=cfg.iit, oit=cfg.oit))

        self.blueprint.props["reveal"] = PropertyData(keyframe_match_name(cfg), keyframes=kfs)

        # Animator Text Opacity is set via props.anim_opacity in JSX
        self.blueprint.props["anim_opacity"] = PropertyData("ADBE Opacity", value=0)

        # Common FX baseline (style preset; no timing dependence)
        self.blueprint.effects["ADBE Turbulent Displace"] = {
            "0002": PropertyData("ADBE Turbulent Displace-0002", value=7.5),
            "0006": PropertyData("ADBE Turbulent Displace-0006", expression="time*500"),
        }
        self.blueprint.effects["ADBE Posterize Time"] = {
            "0001": PropertyData("ADBE Posterize Time-0001", value=5),
        }

        # ---------------------------------------------------------------------
        # TODO (intentionally not implemented now):
        # Old design had p2 tail blur + tail fade-out. If you ever want it back,
        # implement it in a *separate overlay layer* (or an optional mode flag),
        # but keep timings expressed in frames relative to out_p (A1), not absolute t=7.x.
        # ---------------------------------------------------------------------

        self.blueprint.text_data["layer_styles_enabled"] = False


@dataclass
class AdjBabyMotionV4:
    """
    Adjustment layer “Adj 11 Stride” — multi-phase gesture across the whole Baby block.

    We keep this as-is (as you asked), but remove absolute timestamps:
      - old leaked keys were from another timeline (5.839..7.298..)
      - we remap to current [in_p..out_p] using A2 normalized phase u=0.8

    NOTE:
      - ease speed recompute is not applied here because we are using no temporal ease arrays
        (empty ease_in/ease_out). If later you add ease, recompute speed after remap.
    """
    in_p: float
    out_p: float
    z_index: int = 14

    PHASE_U_MID: float = 0.8  # derived from leaked timings: (7.007 - 5.839) / (7.299 - 5.839) ~= 0.8

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name="Adj 11 Stride",
            type="adjustment",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {"blendingModeCode": INTRO_BLENDING_MODE_CODE, "startTime": 0.0}
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")

        t0 = float(self.in_p)
        t2 = float(self.out_p)
        t1 = _t_norm(t0, t2, self.PHASE_U_MID)

        self.blueprint.effects["ADBE Geometry2"] = {
            # Scale Height (values preserved; only time remapped)
            "0003": PropertyData(
                "ADBE Geometry2-0003",
                keyframes=[
                    KeyframeData(t=t0, v=85, iit="6613", oit="6613"),
                    KeyframeData(t=t1, v=97.1970871625517, iit="6613", oit="6613"),
                    KeyframeData(t=t2, v=132.943718527025, iit="6613", oit="6613"),
                ],
            ),
            # Rotation Z (kept stable, rotates into end)
            "0007": PropertyData(
                "ADBE Geometry2-0007",
                keyframes=[
                    KeyframeData(t=t0, v=0, iit="6613", oit="6613"),
                    KeyframeData(t=t1, v=0, iit="6613", oit="6613"),
                    KeyframeData(t=t2, v=-3.5, iit="6613", oit="6613"),
                ],
            ),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Distributor
# =============================================================================

class BabyStrideDistributor:
    def __init__(self, data: Dict[str, Any]):
        p1 = data["p1"]
        p2 = data["p2"]

        # p1: token reveal text (Animator)
        self.txt1 = TextLayerBaby(
            phrase=p1["phrase"],
            in_p=float(p1["in_out"][0]),
            out_p=float(p1["in_out"][1]),
            z_index=17,
            source_rect=p1["s_rect"],
            tf_key="BABY_P1_TEXT",
            tokens_data=tokens_from_data(p1),
        ).blueprint

        # p2: token reveal text (Animator) — NEW (was "no animator" before)
        self.txt2 = TextLayerBaby(
            phrase=p2["phrase"],
            in_p=float(p2["in_out"][0]),
            out_p=float(p2["in_out"][1]),
            z_index=16,
            source_rect=p2["s_rect"],
            tf_key="BABY_P2_TEXT",
            tokens_data=tokens_from_data(p2),
        ).blueprint

        # Adjustment spans full Baby block: p1.in -> p2.out (unchanged)
        self.adj = AdjBabyMotionV4(
            in_p=float(p1["in_out"][0]),
            out_p=float(p2["in_out"][1]),
        ).blueprint

        # Keep original stacking: adj below texts, then p2, then p1
        self.layers = [self.adj, self.txt2, self.txt1]
