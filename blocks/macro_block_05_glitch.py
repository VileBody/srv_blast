# blocks/macro_block_05_glitch.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config import FPS, INTRO_BLENDING_MODE_CODE, INTRO_TEXT_BASE
from core.text_rules import apply_style_rule
from core.types import KeyframeData, KeyframeEase, LayerBlueprint, PropertyData

from .common import apply_tf_from_config


# =============================================================================
# Time helpers
# =============================================================================

def _dt() -> float:
    if float(FPS) <= 0:
        raise ValueError("config.FPS must be > 0")
    return 1.0 / float(FPS)


def _t_from_out_minus_frames(out_p: float, frames_before_out: int) -> float:
    return float(out_p) - float(frames_before_out) * _dt()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _kfe(speed: float, influence: float) -> KeyframeEase:
    return KeyframeEase(speed=float(speed), influence=float(influence))


def _apply_dvdt_ease_scalar(kfs: List[KeyframeData], *, influence: float = 16.666666667) -> List[KeyframeData]:
    """
    After we change keyframe times, old KeyframeEase.speed becomes wrong.
    Recompute like dumps do: speed = abs(Δv)/Δt.
    """
    if not kfs:
        return []
    ks = sorted(kfs, key=lambda x: float(x.t))
    out = [KeyframeData(t=float(k.t), v=k.v, iit=k.iit, oit=k.oit, ease_in=[], ease_out=[]) for k in ks]

    for i in range(len(out) - 1):
        a = out[i]
        b = out[i + 1]
        dt = float(b.t) - float(a.t)
        try:
            dv = float(b.v) - float(a.v)
        except Exception:
            dv = 0.0
        sp = 0.0 if dt <= 1e-9 else abs(dv) / dt
        a.ease_out = [_kfe(sp, influence)]
        b.ease_in = [_kfe(sp, influence)]

    return out


def _clip_tail_start_by_fraction(*, in_p: float, out_p: float, tail_start: float, min_frac: float) -> float:
    """
    "Клиппер", чтобы разнос НЕ начинался слишком рано на коротких окнах.
    tail_start = (out - N frames) по классике.
    min_frac = минимальная доля окна, после которой разрешаем стартовать (например 0.60).
    Итог: max(tail_start, in + (out-in)*min_frac)

    Почему:
      - когда prefix-окно короткое (например 0.5s), out-11f ≈ in+1f -> разнос почти сразу.
      - хотим, чтобы prefix хоть чуть-чуть "пожил" чистым текстом перед разносом.
    """
    in_p = float(in_p)
    out_p = float(out_p)
    if out_p <= in_p:
        return in_p
    min_frac = max(0.0, min(1.0, float(min_frac)))
    floor_t = in_p + (out_p - in_p) * min_frac
    return max(float(tail_start), float(floor_t))


# =============================================================================
# DUMP TRUTH (layout only)
# =============================================================================

FORCE_DUMP_START_TIME = False  # startTime in dumps is absolute -> ломает clip-agnostic
FORCE_DUMP_ANCHOR = True

# Duration of precomp 'Текст "Mine"' (must match app/project_config.py)
MINE_COMP_DUR: float = 2.54421087754421

DUMP_HIS_EYES_WERE = {
    "layer_name": "his eyes were ",
    "start_time": -5.38038038038038,
    "source_rect": {
        "t": 7.67434100767434,
        "left": -404.462890625,
        "top": -68.212890625,
        "width": 691.416015625,
        "height": 69.43359375,
    },
    "anchor": [-58.7548828125, -33.49609375, 0.0],
}

DUMP_HIS_EYES_WERE_LIKE = {
    "layer_name": "his eyes were like 3",
    "start_time": -5.38038038038038,
    "source_rect": {
        "t": 8.00800800800801,
        "left": -404.462890625,
        "top": -68.212890625,
        "width": 691.416015625,
        "height": 218.212890625,
    },
    "anchor": [-61.2939453125, 40.8935546875, 0.0],
}


def _apply_dump_truth(blueprint: LayerBlueprint, dump: Dict[str, Any]) -> None:
    meta = blueprint.text_data.get("layer_meta", {})
    if FORCE_DUMP_START_TIME:
        meta["startTime"] = float(dump["start_time"])
    blueprint.text_data["layer_meta"] = meta

    blueprint.source_rect = dump["source_rect"]

    if FORCE_DUMP_ANCHOR:
        blueprint.props["anchor"] = PropertyData("ADBE Anchor Point", value=dump["anchor"])


# =============================================================================
# FX helpers (style presets, not timing)
# =============================================================================

def fx_posterize_5() -> Dict[str, PropertyData]:
    return {"0001": PropertyData("ADBE Posterize Time-0001", value=5)}


def fx_box_blur2_baseline() -> Dict[str, PropertyData]:
    return {
        "0002": PropertyData("ADBE Box Blur2-0002", value=3),
        "0003": PropertyData("ADBE Box Blur2-0003", value=1),
        "0004": PropertyData("ADBE Box Blur2-0004", value=0),
    }


def fx_turbulent_baseline(
    *,
    amount: float = 7.5,
    size: float = 50,
    offset: Optional[List[float]] = None,
    complexity: float = 1,
) -> Dict[str, PropertyData]:
    return {
        "0001": PropertyData("ADBE Turbulent Displace-0001", value=1),
        "0002": PropertyData("ADBE Turbulent Displace-0002", value=float(amount)),
        "0003": PropertyData("ADBE Turbulent Displace-0003", value=float(size)),
        "0004": PropertyData("ADBE Turbulent Displace-0004", value=offset if offset is not None else [540, 960]),
        "0005": PropertyData("ADBE Turbulent Displace-0005", value=float(complexity)),
        "0006": PropertyData("ADBE Turbulent Displace-0006", expression="time*500"),
    }


# =============================================================================
# Token helpers for splitting "Это всё\rты!" -> "Это всё" + Mine("ты!")
# =============================================================================

def _tokens_from_seg(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    t = seg.get("tokens") or []
    return [x for x in t if isinstance(x, dict) and isinstance(x.get("text"), str)]


def _recon_phrase(tokens: List[Dict[str, Any]]) -> str:
    return "".join(str(t.get("text", "")) + str(t.get("trailing", "")) for t in tokens)


def _prefix_tokens(tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Take all tokens except last, and fix trailing so:
      - last token trailing becomes "" (no '\r' at end)
    """
    if len(tokens) <= 1:
        return []
    out = [dict(x) for x in tokens[:-1]]
    if out:
        out[-1]["trailing"] = ""
    return out


# =============================================================================
# Presets
# =============================================================================

class GlitchTextPreset(Enum):
    SLOW = "slow"
    FAST = "fast"
    PEAK_PREFIX = "peak_prefix"  # "Это всё" only (no "ты")


# =============================================================================
# Layers: glitch texts
# =============================================================================

@dataclass
class TextLayerGlitch:
    phrase: str
    in_p: float
    out_p: float
    z_index: int
    source_rect: Dict[str, float]
    tf_key: str
    preset: GlitchTextPreset
    layer_name: Optional[str] = None

    # classic tail offsets (frames) from leaked dump “feel”
    FADE_FRAMES_SLOW: int = 4

    FADE_FRAMES_PEAK: int = 8
    BLUR_START_FRAMES_PEAK: int = 8
    BLUR_END_FRAMES_PEAK: int = 1
    TURB_START_FRAMES_PEAK: int = 11
    TURB_END_FRAMES_PEAK: int = 3

    # NEW: don't start chaos too early on short prefix windows
    PEAK_MIN_FRACTION: float = 0.60  # start no earlier than last 40% of window

    def __post_init__(self) -> None:
        name = self.layer_name if self.layer_name else self.phrase.replace("\r", " ")
        self.blueprint = LayerBlueprint(
            name=name,
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

        # -----------------------------
        # Timing-dependent parts (NO absolute timestamps)
        # -----------------------------
        if self.preset == GlitchTextPreset.SLOW:
            # fade last 4 frames
            t0 = _t_from_out_minus_frames(self.out_p, self.FADE_FRAMES_SLOW)
            t1 = float(self.out_p)
            if t0 < float(self.in_p):
                t0 = float(self.in_p)

            self.blueprint.props["opacity"] = PropertyData(
                "ADBE Opacity",
                keyframes=[
                    KeyframeData(t=t0, v=100, iit="6612", oit="6612"),
                    KeyframeData(t=t1, v=0, iit="6612", oit="6612"),
                ],
            )
            self.blueprint.text_data["no_text_animator"] = True

            self.blueprint.effects["ADBE Turbulent Displace"] = fx_turbulent_baseline(amount=7.5, size=50)
            self.blueprint.effects["ADBE Posterize Time"] = fx_posterize_5()

        elif self.preset == GlitchTextPreset.FAST:
            self.blueprint.effects["ADBE Turbulent Displace"] = fx_turbulent_baseline(amount=7.5, size=50)
            self.blueprint.effects["ADBE Posterize Time"] = fx_posterize_5()

        else:
            # PEAK_PREFIX: "Это всё" lives only until Mine starts (out_p == mine_drop.t_start)
            in_p = float(self.in_p)
            out_p = float(self.out_p)

            # -----------------------------
            # Opacity fade (out-8f -> out), clipped by fraction
            # -----------------------------
            raw_op0 = _t_from_out_minus_frames(out_p, self.FADE_FRAMES_PEAK)
            t_op0 = _clip_tail_start_by_fraction(
                in_p=in_p, out_p=out_p, tail_start=raw_op0, min_frac=self.PEAK_MIN_FRACTION
            )
            t_op1 = out_p

            self.blueprint.props["opacity"] = PropertyData(
                "ADBE Opacity",
                keyframes=[
                    KeyframeData(t=t_op0, v=100, iit="6612", oit="6612"),
                    KeyframeData(t=t_op1, v=0, iit="6612", oit="6612"),
                ],
            )

            # -----------------------------
            # Turbulent amount burst (out-11f -> out-3f), clipped by fraction
            # -----------------------------
            raw_t0 = _t_from_out_minus_frames(out_p, self.TURB_START_FRAMES_PEAK)
            raw_t1 = _t_from_out_minus_frames(out_p, self.TURB_END_FRAMES_PEAK)

            t_t0 = _clip_tail_start_by_fraction(
                in_p=in_p, out_p=out_p, tail_start=raw_t0, min_frac=self.PEAK_MIN_FRACTION
            )
            t_t1 = max(raw_t1, t_t0)  # keep ordering
            if t_t1 > out_p:
                t_t1 = out_p

            turb_kfs = _apply_dvdt_ease_scalar(
                [
                    KeyframeData(t=t_t0, v=7.5, iit="6612", oit="6612"),
                    KeyframeData(t=t_t1, v=647.0, iit="6612", oit="6612"),
                ],
                influence=16.666666667,
            )

            self.blueprint.effects["ADBE Turbulent Displace"] = {
                **fx_turbulent_baseline(amount=7.5, size=50, offset=[540, 960], complexity=1),
                "0002": PropertyData("ADBE Turbulent Displace-0002", keyframes=turb_kfs),
            }
            self.blueprint.effects["ADBE Posterize Time"] = fx_posterize_5()

            # -----------------------------
            # Blur radius tail (out-8f -> out-1f), clipped by fraction
            # -----------------------------
            raw_b0 = _t_from_out_minus_frames(out_p, self.BLUR_START_FRAMES_PEAK)
            raw_b1 = _t_from_out_minus_frames(out_p, self.BLUR_END_FRAMES_PEAK)

            t_b0 = _clip_tail_start_by_fraction(
                in_p=in_p, out_p=out_p, tail_start=raw_b0, min_frac=self.PEAK_MIN_FRACTION
            )
            t_b1 = max(raw_b1, t_b0)
            if t_b1 > out_p:
                t_b1 = out_p

            blur_kfs = _apply_dvdt_ease_scalar(
                [
                    KeyframeData(t=t_b0, v=0.0, iit="6612", oit="6612"),
                    KeyframeData(t=t_b1, v=3.0, iit="6612", oit="6612"),
                ],
                influence=16.666666667,
            )

            self.blueprint.effects["ADBE Box Blur2"] = {
                "0001": PropertyData("ADBE Box Blur2-0001", keyframes=blur_kfs),
                **fx_box_blur2_baseline(),
            }

        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Adjustment layer 10 (kept)
# =============================================================================

@dataclass
class AdjGlitchPhysics:
    in_p: float
    out_p: float
    z_index: int = 11

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name="Adjustment Layer 10",
            type="adjustment",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            adjustment_layer=True,
        )
        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": 0.0,
            "motionBlur": True,
        }
        apply_tf_from_config(self.blueprint, "ADJ_SOLID_DEFAULT")
        self.blueprint.effects["ADBE Geometry2"] = {
            "0011": PropertyData("ADBE Geometry2-0011", value=1),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Mine: TWO precomp instances in comp "Текст"
# Must appear ONLY during mine_drop window (word "ты")
# =============================================================================

@dataclass
class VideoLayerMineBlur:
    """
    Lower mine instance (bg blur). Lives only on [mine_in..mine_out].
    Old project had scale keyframes at absolute times -> map to window by u.
    """
    in_p: float
    out_p: float
    z_index: int = 10
    comp_id: int = 88

    SCALE_FROM: List[float] = (50.0, 50.0, 100.0)
    SCALE_TO: List[float] = (150.0, 150.0, 100.0)
    SCALE_U: float = 0.64  # derived from old (10.093 - 8.466) / 2.544 ~= 0.64

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name='Текст "Mine"',
            type="video",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            comp_id=int(self.comp_id),
        )
        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": float(self.in_p),
            "motionBlur": True,
            "enabled": True,
        }

        self.blueprint.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=[540, 960, 0])
        self.blueprint.props["tf_position"] = PropertyData("ADBE Position", value=[540, 960, 0])
        self.blueprint.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=0)
        self.blueprint.props["tf_opacity"] = PropertyData("ADBE Opacity", value=25)

        dur = float(self.out_p) - float(self.in_p)
        t0 = float(self.in_p)
        t1 = float(self.out_p)
        t_mid = t0 + self.SCALE_U * dur if dur > 1e-6 else t0

        # keep inside window and not identical to t0
        if t_mid <= t0 + 1e-6:
            t_mid = min(t1, t0 + _dt())
        if t_mid > t1:
            t_mid = t1

        self.blueprint.props["scale"] = PropertyData(
            "ADBE Scale",
            keyframes=[
                KeyframeData(t=t0, v=list(self.SCALE_FROM), iit="6613", oit="6613"),
                KeyframeData(t=t_mid, v=list(self.SCALE_TO), iit="6613", oit="6613"),
            ],
        )

        self.blueprint.effects["ADBE Box Blur2"] = {
            "0001": PropertyData("ADBE Box Blur2-0001", value=5),
            **fx_box_blur2_baseline(),
        }
        self.blueprint.text_data["layer_styles_enabled"] = False


@dataclass
class VideoLayerMineMain:
    """
    Upper mine instance (fg). Lives only on [mine_in..mine_out].
    """
    in_p: float
    out_p: float
    z_index: int = 9
    comp_id: int = 88

    def __post_init__(self) -> None:
        self.blueprint = LayerBlueprint(
            name='Текст "Mine"1',
            type="video",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            comp_id=int(self.comp_id),
        )
        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": float(self.in_p),
            "motionBlur": True,
            "enabled": True,
        }

        self.blueprint.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=[540, 960, 0])
        self.blueprint.props["tf_position"] = PropertyData("ADBE Position", value=[540, 960, 0])
        self.blueprint.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=0)
        self.blueprint.props["tf_opacity"] = PropertyData("ADBE Opacity", value=100)
        self.blueprint.props["tf_scale"] = PropertyData("ADBE Scale", value=[100, 100, 100])

        self.blueprint.text_data["layer_styles_enabled"] = False


# =============================================================================
# Inner text inside compId=88 ("ты")
# =============================================================================

@dataclass
class MineInnerTextLayer:
    in_p: float
    out_p: float
    text_value: str
    z_index: int = 1
    comp_id: int = 88

    def __post_init__(self) -> None:
        txt = str(self.text_value)

        self.blueprint = LayerBlueprint(
            name="mine",
            type="text",
            in_point=float(self.in_p),
            out_point=float(self.out_p),
            z_index=int(self.z_index),
            text=txt,
            source_rect={
                "t": float(self.in_p),
                "left": -178.232421875,
                "top": -67.3828125,
                "width": 238.271484375,
                "height": 67.7734375,
            },
        )

        self.blueprint.text_data["layer_meta"] = {
            "blendingModeCode": INTRO_BLENDING_MODE_CODE,
            "startTime": 0.0,
            "enabled": True,
            "comp_id_target": int(self.comp_id),
        }

        red = [0.99216002225876, 0.0862699970603, 0.0784300044179]
        base = dict(INTRO_TEXT_BASE)
        base["font"] = "Point-ExtraBold"
        base["fontSize"] = 100
        base["applyFill"] = True
        base["fillColor"] = red
        base["applyStroke"] = False
        base["tracking"] = -50
        base["leading"] = 150
        base["autoLeading"] = False
        base["allCaps"] = True
        base["justificationCode"] = "7415"
        self.blueprint.text_data["text_base"] = base

        self.blueprint.text_data["char_styles_ungrouped"] = [
            {"i": i, "font": "Point-ExtraBold", "fontSize": 100}
            for i in range(len(txt))
        ]

        self.blueprint.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=[0, 0, 0])
        self.blueprint.props["tf_position"] = PropertyData("ADBE Position", value=[540, 960, 0])
        self.blueprint.props["tf_scale"] = PropertyData("ADBE Scale", value=[100, 100, 100])
        self.blueprint.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=0)

        self.blueprint.text_data["layer_styles_enabled"] = False


class MineInnerDistributor:
    """
    nest_in == mine_in so inner local time starts at 0.
    """
    def __init__(self, mine_drop: Dict[str, Any], *, nest_in: float, mine_comp_dur: float = MINE_COMP_DUR):
        text = str(mine_drop.get("text", "mine"))

        t0_g = float(mine_drop.get("t_start", 0.0))
        t1_g = float(mine_drop.get("t_end", t0_g + 0.001))

        t0 = t0_g - float(nest_in)
        t1 = t1_g - float(nest_in)

        t0 = _clamp(t0, 0.0, float(mine_comp_dur))
        t1 = _clamp(t1, 0.0, float(mine_comp_dur))
        if t1 <= t0:
            t1 = min(float(mine_comp_dur), t0 + 0.001)

        self.txt = MineInnerTextLayer(in_p=t0, out_p=t1, text_value=text).blueprint
        self.layers = [self.txt]


# =============================================================================
# Distributor
# =============================================================================

class GlitchCrescendoDistributor:
    def __init__(self, data: Dict[str, Any]):
        l = data["layers"]

        slow = l["slowly_in"]
        fast = l["fast_reveal"]
        peak = l["glitch_peak"]

        mine_drop = l.get("mine_drop")
        if not isinstance(mine_drop, dict):
            pt = _tokens_from_seg(peak)
            if not pt:
                mine_drop = {"text": "mine", "t_start": float(peak["in_out"][0]), "t_end": float(peak["in_out"][0]) + 0.2}
            else:
                last = pt[-1]
                mine_drop = {"text": str(last.get("text", "")), "t_start": float(last.get("t_start")), "t_end": float(last.get("t_end"))}

        mine_in = float(mine_drop["t_start"])
        mine_out = float(mine_drop["t_end"])

        # Split peak into prefix (everything except last token) and Mine window
        peak_tokens = _tokens_from_seg(peak)
        prefix_tokens = _prefix_tokens(peak_tokens)
        prefix_phrase = _recon_phrase(prefix_tokens) if prefix_tokens else str(peak.get("phrase", ""))

        peak_in = float(peak["in_out"][0])
        prefix_out = max(peak_in, mine_in)

        # fallback if weird
        if prefix_out <= peak_in + 1e-9:
            prefix_phrase = str(peak.get("phrase", ""))
            prefix_out = float(peak["in_out"][1])

        # layers
        self.txt1 = TextLayerGlitch(
            phrase=str(slow["phrase"]),
            in_p=float(slow["in_out"][0]),
            out_p=float(slow["in_out"][1]),
            z_index=15,
            source_rect=slow["s_rect"],
            tf_key="GLITCH_HIS_EYES",
            preset=GlitchTextPreset.SLOW,
            layer_name="slowly_in",
        ).blueprint

        self.txt2 = TextLayerGlitch(
            phrase=str(fast["phrase"]),
            in_p=float(fast["in_out"][0]),
            out_p=float(fast["in_out"][1]),
            z_index=13,
            source_rect=fast["s_rect"],
            tf_key="GLITCH_HIS_EYES_WERE",
            preset=GlitchTextPreset.FAST,
            layer_name="fast_reveal",
        ).blueprint
        _apply_dump_truth(self.txt2, DUMP_HIS_EYES_WERE)

        self.txt3_prefix = TextLayerGlitch(
            phrase=prefix_phrase,
            in_p=peak_in,
            out_p=prefix_out,  # ends exactly at Mine start -> no remnants under "ты"
            z_index=12,
            source_rect=peak["s_rect"],
            tf_key="GLITCH_HIS_EYES_WERE_LIKE",
            preset=GlitchTextPreset.PEAK_PREFIX,
            layer_name="glitch_peak_prefix",
        ).blueprint
        _apply_dump_truth(self.txt3_prefix, DUMP_HIS_EYES_WERE_LIKE)

        # adj spans whole crescendo including Mine
        adj_in = float(slow["in_out"][0])
        adj_out = max(float(peak["in_out"][1]), mine_out)
        self.adj = AdjGlitchPhysics(adj_in, adj_out).blueprint

        # Mine only during "ты"
        self.mine_bg = VideoLayerMineBlur(mine_in, mine_out).blueprint
        self.mine_fg = VideoLayerMineMain(mine_in, mine_out).blueprint
        self.mine_inner = MineInnerDistributor(mine_drop, nest_in=mine_in, mine_comp_dur=MINE_COMP_DUR)

        self.layers = [
            self.mine_bg,
            self.mine_fg,
            self.adj,
            self.txt3_prefix,
            self.txt2,
            self.txt1,
            *self.mine_inner.layers,
        ]
