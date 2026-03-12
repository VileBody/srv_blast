from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    normalize_subtitles_mode,
)
from mlcore.models.subtitles_flow import SubtitleFlowPlan


_INTERP_CODES = {
    "linear": "6612",
    "bezier": "6613",
    "hold": "6614",
}


def _prop(match_name: str, value: Any = None, keyframes: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "match_name": match_name,
        "value": value if not keyframes else None,
        "keyframes": list(keyframes or []),
        "expression": None,
    }


def _kf(t: float, value: Any, interpolation: str = "bezier") -> Dict[str, Any]:
    interp = str(interpolation).strip().lower()
    if interp not in _INTERP_CODES:
        raise ValueError(f"unsupported interpolation={interpolation!r}; allowed={sorted(_INTERP_CODES)}")
    return {
        "t": float(t),
        "v": value,
        "iit": _INTERP_CODES[interp],
        "oit": _INTERP_CODES[interp],
        "ease_in": [{"speed": 0.0, "influence": 16.666666667}],
        "ease_out": [{"speed": 0.0, "influence": 16.666666667}],
    }


@dataclass(frozen=True)
class KeyframePoint:
    at: float
    value: Any
    interpolation: str = "bezier"


@dataclass(frozen=True)
class LayerMotionProfile:
    font: str
    font_size: int
    fill_color: List[float]
    scale_points: List[KeyframePoint]
    opacity_points: List[KeyframePoint]


IMPULSE_PROFILES: Dict[str, LayerMotionProfile] = {
    "long": LayerMotionProfile(
        font="Point-SemiBold",
        font_size=96,
        fill_color=[1, 1, 1],
        scale_points=[
            KeyframePoint(0.0, [90, 90, 100], "bezier"),
            KeyframePoint(0.45, [100, 100, 100], "bezier"),
            KeyframePoint(1.0, [100, 100, 100], "bezier"),
        ],
        opacity_points=[
            KeyframePoint(0.0, 100, "linear"),
            KeyframePoint(0.85, 100, "linear"),
            KeyframePoint(1.0, 0, "linear"),
        ],
    ),
    "short": LayerMotionProfile(
        font="Point-ExtraBold",
        font_size=116,
        fill_color=[1, 1, 1],
        scale_points=[
            KeyframePoint(0.0, [72, 72, 100], "bezier"),
            KeyframePoint(0.45, [128, 128, 100], "bezier"),
            KeyframePoint(1.0, [95, 95, 100], "bezier"),
        ],
        opacity_points=[
            KeyframePoint(0.0, 100, "linear"),
            KeyframePoint(0.9, 100, "linear"),
            KeyframePoint(1.0, 0, "linear"),
        ],
    ),
}


SCENES_PROFILES: Dict[str, LayerMotionProfile] = {
    "TYPE_1": LayerMotionProfile(
        font="Point-SemiBold",
        font_size=90,
        fill_color=[1, 1, 1],
        scale_points=[KeyframePoint(0.0, [90, 90, 100]), KeyframePoint(1.0, [100, 100, 100])],
        opacity_points=[KeyframePoint(0.0, 100, "linear"), KeyframePoint(1.0, 0, "linear")],
    ),
    "TYPE_2": LayerMotionProfile(
        font="Point-SemiBold",
        font_size=94,
        fill_color=[1, 1, 1],
        scale_points=[
            KeyframePoint(0.0, [88, 88, 100]),
            KeyframePoint(0.5, [102, 102, 100]),
            KeyframePoint(1.0, [100, 100, 100]),
        ],
        opacity_points=[KeyframePoint(0.0, 100, "linear"), KeyframePoint(1.0, 0, "linear")],
    ),
    "TYPE_3": LayerMotionProfile(
        font="Point-SemiBold",
        font_size=92,
        fill_color=[1, 1, 1],
        scale_points=[
            KeyframePoint(0.0, [88, 88, 100]),
            KeyframePoint(0.75, [102, 102, 100]),
            KeyframePoint(1.0, [112, 112, 100]),
        ],
        opacity_points=[KeyframePoint(0.0, 100, "linear"), KeyframePoint(1.0, 0, "linear")],
    ),
    "TYPE_4": LayerMotionProfile(
        font="Point-ExtraBold",
        font_size=112,
        fill_color=[0.99216, 0.08627, 0.07843],
        scale_points=[
            KeyframePoint(0.0, [72, 72, 100]),
            KeyframePoint(0.42, [132, 132, 100]),
            KeyframePoint(1.0, [96, 96, 100]),
        ],
        opacity_points=[KeyframePoint(0.0, 100, "linear"), KeyframePoint(1.0, 0, "linear")],
    ),
    "TYPE_5": LayerMotionProfile(
        font="Point-SemiBold",
        font_size=88,
        fill_color=[1, 1, 1],
        scale_points=[KeyframePoint(0.0, [92, 92, 100]), KeyframePoint(1.0, [100, 100, 100])],
        opacity_points=[KeyframePoint(0.0, 100, "linear"), KeyframePoint(1.0, 0, "linear")],
    ),
    "TYPE_6": LayerMotionProfile(
        font="Point-SemiBold",
        font_size=92,
        fill_color=[1, 1, 1],
        scale_points=[
            KeyframePoint(0.0, [90, 90, 100]),
            KeyframePoint(0.35, [102, 102, 100]),
            KeyframePoint(1.0, [100, 100, 100]),
        ],
        opacity_points=[KeyframePoint(0.0, 100, "linear"), KeyframePoint(1.0, 0, "linear")],
    ),
}


class FlowTextLayerRenderer:
    def __init__(self, *, mode: str, profiles: Dict[str, LayerMotionProfile]):
        self.mode = normalize_subtitles_mode(mode)
        self._profiles = profiles

    def _profile(self, style_tag: str) -> LayerMotionProfile:
        key = str(style_tag or "").strip()
        p = self._profiles.get(key)
        if p is None:
            raise ValueError(f"unknown style_tag={style_tag!r} for mode={self.mode}")
        return p

    def _timeline_keyframes(
        self,
        *,
        points: List[KeyframePoint],
        in_point: float,
        out_point: float,
    ) -> List[Dict[str, Any]]:
        dur = float(out_point) - float(in_point)
        if dur <= 0.0:
            raise ValueError(f"invalid segment duration {in_point}..{out_point}")
        out: List[Dict[str, Any]] = []
        for p in points:
            rel = float(p.at)
            if rel < 0.0 or rel > 1.0:
                raise ValueError(f"relative keyframe must be within [0..1], got {rel}")
            t = float(in_point) + rel * dur
            out.append(_kf(t=t, value=p.value, interpolation=p.interpolation))
        out.sort(key=lambda x: float(x["t"]))
        for i in range(1, len(out)):
            if float(out[i]["t"]) < float(out[i - 1]["t"]) - 1e-9:
                raise ValueError("non-monotonic keyframe time")
        return out

    def _text_value(self, seg: Dict[str, Any]) -> str:
        lines = seg.get("lines")
        if isinstance(lines, list) and lines:
            return "\r".join(str(x).strip().upper() for x in lines if str(x).strip())
        return str(seg.get("text") or "").strip().upper()

    def render(
        self,
        *,
        flow_plan: SubtitleFlowPlan,
        text_comp_name: str,
    ) -> List[Dict[str, Any]]:
        layers: List[Dict[str, Any]] = []
        z = 1000
        for seg in sorted(flow_plan.segments, key=lambda s: (float(s.in_point), str(s.segment_id))):
            p = self._profile(str(seg.style_tag))
            text_value = self._text_value(seg.model_dump(mode="json", by_alias=True))
            scale_kfs = self._timeline_keyframes(
                points=p.scale_points,
                in_point=float(seg.in_point),
                out_point=float(seg.out_point),
            )
            opacity_kfs = self._timeline_keyframes(
                points=p.opacity_points,
                in_point=float(seg.in_point),
                out_point=float(seg.out_point),
            )
            layer = {
                "name": str(seg.segment_id),
                "type": "text",
                "in_point": float(seg.in_point),
                "out_point": float(seg.out_point),
                "z_index": z,
                "text": text_value,
                "adjustment_layer": False,
                "source_rect": {},
                "props": {
                    "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                    "tf_position": _prop("ADBE Position", [540, 960, 0]),
                    "tf_scale": _prop("ADBE Scale", keyframes=scale_kfs),
                    "tf_rotation": _prop("ADBE Rotate Z", 0),
                    "layer_opacity": _prop("ADBE Opacity", keyframes=opacity_kfs),
                },
                "effects": {},
                "style_instructions": [],
                "text_data": {
                    "layer_meta": {
                        "blendingModeCode": "5212",
                        "startTime": 0.0,
                        "comp_name_target": str(text_comp_name),
                        "enabled": True,
                        "collapseTransformation": True,
                    },
                    "layer_styles_enabled": False,
                    "text_base": {
                        "font": p.font,
                        "fontSize": int(p.font_size),
                        "applyFill": True,
                        "fillColor": list(p.fill_color),
                        "applyStroke": False,
                        "strokeWidth": 0,
                        "strokeColor": None,
                        "tracking": -20,
                        "leading": int(max(72, p.font_size + 12)),
                        "autoLeading": False,
                        "justificationCode": "7415",
                        "allCaps": True,
                        "leftIndent": 0,
                        "rightIndent": 0,
                        "firstLineIndent": 0,
                        "spaceBefore": 0,
                        "spaceAfter": 0,
                    },
                    "char_styles_ungrouped": [],
                    "no_text_animator": True,
                },
            }
            layers.append(layer)
            z -= 1
        return layers


class TextFlowRendererFactory:
    @staticmethod
    def create(mode: str) -> FlowTextLayerRenderer:
        resolved = normalize_subtitles_mode(mode)
        if resolved == SUBTITLES_MODE_IMPULSE_2ND:
            return FlowTextLayerRenderer(mode=resolved, profiles=IMPULSE_PROFILES)
        if resolved == SUBTITLES_MODE_SCENES_3RD:
            return FlowTextLayerRenderer(mode=resolved, profiles=SCENES_PROFILES)
        raise RuntimeError(f"Flow renderer is unsupported for mode={resolved!r}")

    @staticmethod
    def is_flow_mode(mode: str) -> bool:
        resolved = normalize_subtitles_mode(mode)
        return resolved in {SUBTITLES_MODE_IMPULSE_2ND, SUBTITLES_MODE_SCENES_3RD}

    @staticmethod
    def is_legacy_mode(mode: str) -> bool:
        resolved = normalize_subtitles_mode(mode)
        return resolved == SUBTITLES_MODE_LEGACY_BLOCKS
