from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Tuple

from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    normalize_subtitles_mode,
)
from mlcore.models.subtitles_flow import SubtitleFlowPlan


_LOG = logging.getLogger("app.text_flow_renderer")
_INTERP_CODES = {
    "linear": "6612",
    "bezier": "6613",
    "hold": "6614",
}
_FRAME_SEC = 1.0 / 23.976
_REVEAL_STEP_SEC = _FRAME_SEC
_REVEAL_START_PERCENT = 25.0


def _prop(
    match_name: str,
    value: Any = None,
    keyframes: List[Dict[str, Any]] | None = None,
    expression: str | None = None,
) -> Dict[str, Any]:
    return {
        "match_name": match_name,
        "value": value if not keyframes else None,
        "keyframes": list(keyframes or []),
        "expression": expression,
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

    def _text_animator_cfg(self) -> Dict[str, Any]:
        return {
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

    def _segment_token_times(
        self,
        *,
        seg: Any,
        text_value: str,
    ) -> List[Tuple[str, float, float]]:
        seg_in = float(seg.in_point)
        seg_out = float(seg.out_point)
        if seg_out <= seg_in:
            return []

        out: List[Tuple[str, float, float]] = []
        tokens = sorted(seg.tokens, key=lambda t: (float(t.t_start), float(t.t_end), str(t.text)))
        for tok in tokens:
            ts = max(seg_in, min(float(tok.t_start), seg_out))
            te = max(seg_in, min(float(tok.t_end), seg_out))
            if te <= ts:
                te = min(seg_out, ts + _FRAME_SEC)
            if te <= ts:
                continue
            out.append((str(tok.text), ts, te))

        if out:
            return out

        words = [w for w in text_value.replace("\r", " ").split(" ") if w]
        if not words:
            words = [str(seg.segment_id)]
        dur = seg_out - seg_in
        step = dur / float(max(1, len(words)))
        for i, word in enumerate(words):
            ws = seg_in + float(i) * step
            we = seg_in + float(i + 1) * step
            out.append((word, ws, we))

        _LOG.warning(
            "subtitle_flow_warning mode=%s segment_id=%s reason=no_tokens action=fallback_equidistant_words=%s",
            self.mode,
            seg.segment_id,
            len(words),
        )
        return out

    def _reveal_keyframes(self, *, seg: Any, text_value: str) -> List[Dict[str, Any]]:
        token_times = self._segment_token_times(seg=seg, text_value=text_value)
        n = len(token_times)
        if n == 0:
            return []

        step = (100.0 - _REVEAL_START_PERCENT) / float(n)
        thresholds = [_REVEAL_START_PERCENT + step * i for i in range(n + 1)]
        seg_in = float(seg.in_point)
        seg_out = float(seg.out_point)
        out: List[Dict[str, Any]] = []
        prev_t = seg_in
        for i, (_word, t_start, _t_end) in enumerate(token_times):
            hold_t = max(float(t_start), prev_t)
            jump_t = min(seg_out, hold_t + _REVEAL_STEP_SEC)
            out.append(_kf(t=hold_t, value=thresholds[i], interpolation="bezier"))
            out.append(_kf(t=jump_t, value=thresholds[i + 1], interpolation="bezier"))
            prev_t = jump_t

        tail_t = min(seg_out, max(prev_t, seg_out - _FRAME_SEC))
        if tail_t > prev_t + 1e-9:
            out.append(_kf(t=tail_t, value=100.0, interpolation="bezier"))
        return out

    def _scene_lines_words(self, *, seg: Any, text_value: str) -> List[List[str]]:
        out: List[List[str]] = []
        for ln in list(seg.lines or []):
            words = [w for w in str(ln).strip().split(" ") if w]
            if words:
                out.append(words)
        if out:
            return out
        words = [w for w in text_value.replace("\r", " ").split(" ") if w]
        if words:
            return [words]
        return [[str(seg.segment_id)]]

    def _words_with_linebreaks(self, sub_words: List[str], scene_lines: List[List[str]]) -> str:
        if not scene_lines:
            return " ".join(str(w).upper() for w in sub_words)
        word_line: Dict[str, int] = {}
        for li, line in enumerate(scene_lines):
            for w in line:
                key = str(w).lower()
                if key not in word_line:
                    word_line[key] = li
        parts: List[str] = []
        prev_li: int | None = None
        for w in sub_words:
            li = word_line.get(str(w).lower(), 0)
            up = str(w).upper()
            if prev_li is None:
                parts.append(up)
            elif li != prev_li:
                parts.append("\r" + up)
            else:
                parts.append(" " + up)
            prev_li = li
        return "".join(parts)

    def _effect_turbulent_displace(self) -> Dict[str, Any]:
        return {
            "0001": _prop("ADBE Turbulent Displace-0001", 1),
            "0002": _prop("ADBE Turbulent Displace-0002", 7.5),
            "0003": _prop("ADBE Turbulent Displace-0003", 50.0),
            "0004": _prop("ADBE Turbulent Displace-0004", [540, 960]),
            "0005": _prop("ADBE Turbulent Displace-0005", 1.0),
            "0006": _prop("ADBE Turbulent Displace-0006", expression="time*500"),
            "0012": _prop("ADBE Turbulent Displace-0012", 3),
        }

    def _effect_posterize_time(self) -> Dict[str, Any]:
        return {"0001": _prop("ADBE Posterize Time-0001", 5)}

    def _effect_box_blur(self, *, t_start: float, t_end: float, v_start: float = 0.0, v_end: float = 3.0) -> Dict[str, Any]:
        return {
            "0001": _prop(
                "ADBE Box Blur2-0001",
                keyframes=[
                    _kf(t=t_start, value=v_start, interpolation="bezier"),
                    _kf(t=t_end, value=v_end, interpolation="bezier"),
                ],
            ),
            "0002": _prop("ADBE Box Blur2-0002", 3),
            "0003": _prop("ADBE Box Blur2-0003", 1),
            "0004": _prop("ADBE Box Blur2-0004", 0),
        }

    def _effect_minimax_intro(self, *, t_in: float) -> Dict[str, Any]:
        return {
            "0001": _prop("ADBE Minimax-0001", 2),
            "0002": _prop(
                "ADBE Minimax-0002",
                keyframes=[
                    _kf(t=t_in, value=15, interpolation="bezier"),
                    _kf(t=t_in + _FRAME_SEC, value=0, interpolation="bezier"),
                ],
            ),
            "0003": _prop("ADBE Minimax-0003", 2),
        }

    def _effect_minimax_exit(self, *, t_out: float) -> Dict[str, Any]:
        return {
            "0001": _prop("ADBE Minimax-0001", 2),
            "0002": _prop(
                "ADBE Minimax-0002",
                keyframes=[
                    _kf(t=t_out - _FRAME_SEC * 2, value=0, interpolation="bezier"),
                    _kf(t=t_out - _FRAME_SEC, value=32, interpolation="bezier"),
                ],
            ),
            "0003": _prop("ADBE Minimax-0003", 2),
        }

    def _effect_geometry_scale_anim(
        self,
        *,
        t_in: float,
        t_out: float,
        scale_start: float = 85.0,
        scale_end: float = 100.0,
        skew_start: float | None = None,
        skew_end: float | None = None,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "0001": _prop("ADBE Geometry2-0001", [540, 960]),
            "0002": _prop("ADBE Geometry2-0002", [540, 960]),
            "0011": _prop("ADBE Geometry2-0011", 1),
            "0003": _prop(
                "ADBE Geometry2-0003",
                keyframes=[
                    _kf(t=t_in, value=scale_start, interpolation="bezier"),
                    _kf(t=t_out + 0.5, value=scale_end, interpolation="bezier"),
                ],
            ),
            "0004": _prop("ADBE Geometry2-0004", 100),
            "0008": _prop("ADBE Geometry2-0008", 100),
        }
        if skew_start is not None and skew_end is not None:
            out["0007"] = _prop(
                "ADBE Geometry2-0007",
                keyframes=[
                    _kf(t=t_in, value=float(skew_start), interpolation="bezier"),
                    _kf(t=t_out, value=float(skew_end), interpolation="bezier"),
                ],
            )
        return out

    def _effect_geometry_type3(self, *, t_in: float, t_out: float) -> Dict[str, Any]:
        dur = max(_FRAME_SEC, t_out - t_in)
        t80 = t_in + dur * 0.80
        return {
            "0001": _prop("ADBE Geometry2-0001", [540, 960]),
            "0002": _prop("ADBE Geometry2-0002", [540, 960]),
            "0011": _prop("ADBE Geometry2-0011", 1),
            "0003": _prop(
                "ADBE Geometry2-0003",
                keyframes=[
                    _kf(t=t_in, value=85, interpolation="bezier"),
                    _kf(t=t80, value=97.2, interpolation="bezier"),
                    _kf(t=t_out, value=132.94, interpolation="bezier"),
                ],
            ),
            "0004": _prop("ADBE Geometry2-0004", 100),
            "0007": _prop(
                "ADBE Geometry2-0007",
                keyframes=[
                    _kf(t=t80, value=0.0, interpolation="bezier"),
                    _kf(t=t_out, value=-3.5, interpolation="bezier"),
                ],
            ),
            "0008": _prop("ADBE Geometry2-0008", 100),
        }

    def _adjustment_layer(
        self,
        *,
        name: str,
        in_point: float,
        out_point: float,
        z_index: int,
        text_comp_name: str,
        effects: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "name": str(name),
            "type": "adjustment",
            "in_point": float(in_point),
            "out_point": float(out_point),
            "z_index": int(z_index),
            "text": "",
            "adjustment_layer": True,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                "tf_position": _prop("ADBE Position", [540, 960, 0]),
                "tf_scale": _prop("ADBE Scale", [100, 100, 100]),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "tf_opacity": _prop("ADBE Opacity", 100),
            },
            "effects": effects,
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
            },
        }

    def _base_layer_props(
        self,
        *,
        in_point: float,
        out_point: float,
        profile: LayerMotionProfile,
    ) -> Dict[str, Any]:
        scale_kfs = self._timeline_keyframes(
            points=profile.scale_points,
            in_point=in_point,
            out_point=out_point,
        )
        opacity_kfs = self._timeline_keyframes(
            points=profile.opacity_points,
            in_point=in_point,
            out_point=out_point,
        )
        return {
            "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
            "tf_position": _prop("ADBE Position", [540, 960, 0]),
            "tf_scale": _prop("ADBE Scale", keyframes=scale_kfs),
            "tf_rotation": _prop("ADBE Rotate Z", 0),
            "layer_opacity": _prop("ADBE Opacity", keyframes=opacity_kfs),
        }

    def _base_text_data(
        self,
        *,
        profile: LayerMotionProfile,
        comp_name_target: str,
        use_animator: bool,
    ) -> Dict[str, Any]:
        td: Dict[str, Any] = {
            "layer_meta": {
                "blendingModeCode": "5212",
                "startTime": 0.0,
                "comp_name_target": str(comp_name_target),
                "enabled": True,
                "collapseTransformation": True,
            },
            "layer_styles_enabled": False,
            "text_base": {
                "font": profile.font,
                "fontSize": int(profile.font_size),
                "applyFill": True,
                "fillColor": list(profile.fill_color),
                "applyStroke": False,
                "strokeWidth": 0,
                "strokeColor": None,
                "tracking": -20,
                "leading": int(max(72, profile.font_size + 12)),
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
        }
        if use_animator:
            td["text_animator"] = self._text_animator_cfg()
        else:
            td["no_text_animator"] = True
        return td

    def _build_regular_layer(
        self,
        *,
        seg: Any,
        profile: LayerMotionProfile,
        text_value: str,
        z_index: int,
        text_comp_name: str,
        reveal_kind: str | None,
        name: str | None = None,
        effects: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        in_point = float(seg.in_point)
        out_point = float(seg.out_point)
        use_animator = reveal_kind in {"start", "end"}
        props = self._base_layer_props(in_point=in_point, out_point=out_point, profile=profile)
        if use_animator:
            reveal_kfs = self._reveal_keyframes(seg=seg, text_value=text_value)
            if reveal_kfs:
                if reveal_kind == "end":
                    props["reveal_end"] = _prop("ADBE Text Percent End", keyframes=reveal_kfs)
                else:
                    props["reveal"] = _prop("ADBE Text Percent Start", keyframes=reveal_kfs)
            else:
                use_animator = False
        layer = {
            "name": str(name or seg.segment_id),
            "type": "text",
            "in_point": in_point,
            "out_point": out_point,
            "z_index": z_index,
            "text": text_value,
            "adjustment_layer": False,
            "source_rect": {},
            "props": props,
            "effects": dict(effects or {}),
            "style_instructions": [],
            "text_data": self._base_text_data(
                profile=profile,
                comp_name_target=text_comp_name,
                use_animator=use_animator,
            ),
        }
        return layer

    def _scene_regular_with_adjustment_layers(
        self,
        *,
        seg: Any,
        profile: LayerMotionProfile,
        text_value: str,
        style_tag: str,
        text_comp_name: str,
        z_start: int,
    ) -> List[Dict[str, Any]]:
        t_in = float(seg.in_point)
        t_out = float(seg.out_point)
        adj = self._adjustment_layer(
            name=f"adj_{seg.segment_id}",
            in_point=t_in,
            out_point=t_out,
            z_index=z_start,
            text_comp_name=text_comp_name,
            effects={
                "ADBE Geometry2": self._effect_geometry_scale_anim(
                    t_in=t_in,
                    t_out=t_out,
                    scale_start=85.0,
                    scale_end=100.0,
                )
            },
        )
        text_effects: Dict[str, Any] = {
            "ADBE Turbulent Displace": self._effect_turbulent_displace(),
            "ADBE Posterize Time": self._effect_posterize_time(),
        }
        if style_tag == "TYPE_1":
            text_effects["ADBE Minimax"] = self._effect_minimax_intro(t_in=t_in)
        if style_tag == "TYPE_2":
            text_effects["ADBE Minimax"] = self._effect_minimax_exit(t_out=t_out)

        text_layer = self._build_regular_layer(
            seg=seg,
            profile=profile,
            text_value=text_value,
            z_index=z_start - 1,
            text_comp_name=text_comp_name,
            reveal_kind="start",
            effects=text_effects,
        )
        return [adj, text_layer]

    def _type3_layers(
        self,
        *,
        seg: Any,
        profile: LayerMotionProfile,
        text_value: str,
        text_comp_name: str,
        z_start: int,
    ) -> List[Dict[str, Any]]:
        t_in = float(seg.in_point)
        t_out = float(seg.out_point)
        token_times = self._segment_token_times(seg=seg, text_value=text_value)
        scene_lines = self._scene_lines_words(seg=seg, text_value=text_value)
        layers: List[Dict[str, Any]] = []
        layers.append(
            self._adjustment_layer(
                name=f"adj_{seg.segment_id}",
                in_point=t_in,
                out_point=t_out,
                z_index=z_start,
                text_comp_name=text_comp_name,
                effects={"ADBE Geometry2": self._effect_geometry_type3(t_in=t_in, t_out=t_out)},
            )
        )
        z = z_start - 1
        n = len(token_times)
        if n == 0:
            return layers

        for i in range(n):
            sub_words = [w for (w, _s, _e) in token_times[: i + 1]]
            sub_text = self._words_with_linebreaks(sub_words, scene_lines)
            sub_in = float(token_times[i][1])
            is_last = i == (n - 1)
            if is_last:
                sub_out = t_out
                fade_dur = max((t_out - sub_in) * 0.3, _FRAME_SEC * 5)
                fade_start = max(sub_in, t_out - fade_dur)
                opacity_prop = _prop(
                    "ADBE Opacity",
                    keyframes=[
                        _kf(t=fade_start, value=100, interpolation="bezier"),
                        _kf(t=t_out, value=0, interpolation="bezier"),
                    ],
                )
                effects: Dict[str, Any] = {
                    "ADBE Turbulent Displace": self._effect_turbulent_displace(),
                    "ADBE Posterize Time": self._effect_posterize_time(),
                    "ADBE Box Blur2": self._effect_box_blur(t_start=fade_start, t_end=t_out),
                }
            else:
                next_start = float(token_times[i + 1][1])
                sub_out = min(t_out, max(sub_in + _FRAME_SEC, next_start))
                opacity_prop = _prop("ADBE Opacity", 100)
                effects = {
                    "ADBE Turbulent Displace": self._effect_turbulent_displace(),
                    "ADBE Posterize Time": self._effect_posterize_time(),
                }

            td = self._base_text_data(
                profile=profile,
                comp_name_target=text_comp_name,
                use_animator=False,
            )
            layers.append(
                {
                    "name": f"{seg.segment_id}_{i + 1:02d}",
                    "type": "text",
                    "in_point": sub_in,
                    "out_point": sub_out,
                    "z_index": z,
                    "text": str(sub_text).upper(),
                    "adjustment_layer": False,
                    "source_rect": {},
                    "props": {
                        "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                        "tf_position": _prop("ADBE Position", [540, 960, 0]),
                        "tf_scale": _prop("ADBE Scale", [75, 75, 100]),
                        "tf_rotation": _prop("ADBE Rotate Z", 0),
                        "layer_opacity": opacity_prop,
                    },
                    "effects": effects,
                    "style_instructions": [],
                    "text_data": td,
                }
            )
            z -= 1
        return layers

    def _type5_layers(
        self,
        *,
        seg: Any,
        profile: LayerMotionProfile,
        text_value: str,
        text_comp_name: str,
        z_start: int,
    ) -> List[Dict[str, Any]]:
        t_in = float(seg.in_point)
        t_out = float(seg.out_point)
        reveal_kfs = self._reveal_keyframes(seg=seg, text_value=text_value)
        token_times = self._segment_token_times(seg=seg, text_value=text_value)
        last_token_end = float(token_times[-1][2]) if token_times else t_out
        outline_out = min(t_out, max(t_in + _FRAME_SEC, last_token_end + _FRAME_SEC * 4))
        fade_dur = max((t_out - t_in) * 0.25, _FRAME_SEC * 5)
        fade_start = max(t_in, t_out - fade_dur)

        adj = self._adjustment_layer(
            name=f"adj_{seg.segment_id}",
            in_point=t_in,
            out_point=t_out,
            z_index=z_start,
            text_comp_name=text_comp_name,
            effects={
                "ADBE Geometry2": self._effect_geometry_scale_anim(
                    t_in=t_in,
                    t_out=t_out,
                    scale_start=85.0,
                    scale_end=100.0,
                )
            },
        )

        td_outline = self._base_text_data(
            profile=profile,
            comp_name_target=text_comp_name,
            use_animator=True,
        )
        td_outline["text_base"]["applyFill"] = False
        td_outline["text_base"]["applyStroke"] = True
        td_outline["text_base"]["strokeWidth"] = 5
        td_outline["text_base"]["strokeColor"] = [1, 1, 1]

        outline = {
            "name": f"{seg.segment_id}_outline",
            "type": "text",
            "in_point": t_in,
            "out_point": outline_out,
            "z_index": z_start - 1,
            "text": text_value,
            "adjustment_layer": False,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                "tf_position": _prop("ADBE Position", [540, 960, 0]),
                "tf_scale": _prop("ADBE Scale", [100, 100, 100]),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "tf_opacity": _prop("ADBE Opacity", 100),
                "reveal_end": _prop("ADBE Text Percent End", keyframes=reveal_kfs),
            },
            "effects": {
                "ADBE Turbulent Displace": self._effect_turbulent_displace(),
                "ADBE Posterize Time": self._effect_posterize_time(),
            },
            "style_instructions": [],
            "text_data": td_outline,
        }

        td_fill = self._base_text_data(
            profile=profile,
            comp_name_target=text_comp_name,
            use_animator=True,
        )
        fill = {
            "name": str(seg.segment_id),
            "type": "text",
            "in_point": t_in,
            "out_point": t_out,
            "z_index": z_start - 2,
            "text": text_value,
            "adjustment_layer": False,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                "tf_position": _prop("ADBE Position", [540, 960, 0]),
                "tf_scale": _prop("ADBE Scale", [100, 100, 100]),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "layer_opacity": _prop(
                    "ADBE Opacity",
                    keyframes=[
                        _kf(t=fade_start, value=100, interpolation="bezier"),
                        _kf(t=t_out, value=0, interpolation="bezier"),
                    ],
                ),
                "reveal": _prop("ADBE Text Percent Start", keyframes=reveal_kfs),
            },
            "effects": {
                "ADBE Turbulent Displace": self._effect_turbulent_displace(),
                "ADBE Posterize Time": self._effect_posterize_time(),
                "ADBE Box Blur2": self._effect_box_blur(t_start=fade_start, t_end=t_out),
            },
            "style_instructions": [],
            "text_data": td_fill,
        }
        return [adj, outline, fill]

    def _impulse_clean_text(self, text: str) -> str:
        raw = str(text or "").lower()
        out: List[str] = []
        for i, ch in enumerate(raw):
            if ch in {"'", "-"}:
                prev = raw[i - 1] if i > 0 else " "
                nxt = raw[i + 1] if i < (len(raw) - 1) else " "
                prev_letter = prev.isalpha()
                next_letter = nxt.isalpha()
                if prev_letter and next_letter:
                    out.append(ch)
                continue
            if ch in {".", ",", "!", "?", ";", ":", "\"", "«", "»", "(", ")", "\\", "/", "—", "–"}:
                continue
            out.append(ch)
        return " ".join("".join(out).split())

    def _impulse_expr(self, delay: float) -> str:
        d = f"{float(delay):.4f}"
        return (
            f"delay = {d};\n"
            "myDelay = delay*textIndex;\n"
            "t = (time - inPoint) - myDelay;\n"
            "if (t >= 0){\n"
            "  freq = 2; amplitude = 100; decay = 8.0;\n"
            "  s = amplitude*Math.cos(freq*t*2*Math.PI)/Math.exp(decay*t);\n"
            "  [s,s]\n"
            "} else { value }"
        )

    def _impulse_expr_delay(self, *, track_in: float, out_t: float, chars: int, is_long: bool) -> float:
        if not is_long:
            return 0.05
        if chars <= 1:
            return 0.05
        rough_dur = float(out_t) - float(track_in)
        exit_time = 7.0 / 23.976
        min_hold = 0.10
        available = rough_dur - exit_time - min_hold
        delay = min(0.05, available / float(chars - 1))
        return max(0.005, delay)

    def _impulse_exit_times(self, segs: List[Any]) -> List[float | None]:
        out: List[float | None] = []
        for i, seg in enumerate(segs):
            in_t = float(seg.in_point)
            out_t = float(seg.out_point)
            style = str(seg.style_tag or "").strip().lower()
            exit_t: float | None = None
            if style == "long":
                child_in: float | None = None
                for j, s2 in enumerate(segs):
                    if i == j:
                        continue
                    st2 = str(s2.style_tag or "").strip().lower()
                    s2_in = float(s2.in_point)
                    if st2 == "short" and s2_in > in_t and s2_in < out_t:
                        if child_in is None or s2_in < child_in:
                            child_in = s2_in
                if child_in is not None:
                    reveal_time = 0.05 * float(max(0, len(str(seg.text or "")) - 1))
                    min_exit = in_t + reveal_time + 0.15
                    exit_t = max(child_in, min_exit)
                    exit_t = min(exit_t, out_t - 7.0 / 23.976)
            out.append(exit_t)
        return out

    def _impulse_peak_scale(self, *, text: str, dur: float, comp_width: float = 1080.0) -> float:
        chars = max(1, len(str(text or "")))
        peak_by_dur = round(190 + (1.0 - float(dur)) * 180)
        avg_char_w = 62.0
        safe_width = float(comp_width) * 0.85
        text_width = float(chars) * avg_char_w
        peak_by_char = round((safe_width / max(1.0, text_width)) * 100)
        peak_val = min(peak_by_dur, peak_by_char)
        return float(max(150, peak_val))

    def _impulse_drop_shadows(self) -> Dict[str, Any]:
        return {
            "0001:ADBE Drop Shadow": {
                "0001": _prop("ADBE Drop Shadow-0001", [0, 0, 0, 1]),
                "0002": _prop("ADBE Drop Shadow-0002", 255),
                "0003": _prop("ADBE Drop Shadow-0003", 180),
                "0004": _prop("ADBE Drop Shadow-0004", 3),
                "0005": _prop("ADBE Drop Shadow-0005", 0),
            },
            "0002:ADBE Drop Shadow": {
                "0001": _prop("ADBE Drop Shadow-0001", [0, 0, 0, 1]),
                "0002": _prop("ADBE Drop Shadow-0002", 255),
                "0003": _prop("ADBE Drop Shadow-0003", 180),
                "0004": _prop("ADBE Drop Shadow-0004", 0),
                "0005": _prop("ADBE Drop Shadow-0005", 25),
            },
            "0003:ADBE Drop Shadow": {
                "0001": _prop("ADBE Drop Shadow-0001", [0, 0, 0, 1]),
                "0002": _prop("ADBE Drop Shadow-0002", 127.5),
                "0003": _prop("ADBE Drop Shadow-0003", 180),
                "0004": _prop("ADBE Drop Shadow-0004", 0),
                "0005": _prop("ADBE Drop Shadow-0005", 50),
            },
        }

    def _render_impulse_layers(
        self,
        *,
        flow_plan: SubtitleFlowPlan,
        text_comp_name: str,
    ) -> List[Dict[str, Any]]:
        segs = sorted(flow_plan.segments, key=lambda s: (float(s.in_point), str(s.segment_id)))
        exits = self._impulse_exit_times(segs)
        out: List[Dict[str, Any]] = []
        z = 1000
        prev_out: float | None = None
        fps = 23.976

        for idx, seg in enumerate(segs):
            track_in = float(seg.in_point)
            out_t = float(seg.out_point)
            style = str(seg.style_tag or "").strip().lower()
            is_long = style == "long"
            raw_text = str(seg.text or "")
            clean_text = self._impulse_clean_text(raw_text) or str(seg.segment_id).lower()
            chars = max(1, len(raw_text))
            expr_delay = self._impulse_expr_delay(
                track_in=track_in,
                out_t=out_t,
                chars=chars,
                is_long=is_long,
            )
            reveal_time = expr_delay * float(max(0, chars - 1)) if is_long else 0.0
            in_t = max(0.0, track_in - reveal_time * 0.25)
            if prev_out is not None:
                in_t = max(in_t, float(prev_out))
            if out_t <= in_t:
                out_t = in_t + _FRAME_SEC
            dur = out_t - in_t
            total_f = max(1, int(round(dur * fps)))

            if is_long:
                exit_t = exits[idx]
                if exit_t is not None:
                    scale_exit = min(out_t, max(in_t, float(exit_t)))
                    opacity_exit = min(out_t, max(in_t, float(exit_t) + 3.0 / fps))
                else:
                    scale_exit = min(out_t, in_t + max(0, total_f - 7) / fps)
                    opacity_exit = min(out_t, in_t + max(0, total_f - 4) / fps)
                scale_exit = min(out_t, max(in_t, scale_exit))
                opacity_exit = min(out_t, max(in_t, opacity_exit))
                scale_kfs = [
                    _kf(t=scale_exit, value=[75, 75, 100], interpolation="bezier"),
                    _kf(t=out_t, value=[0, 0, 100], interpolation="bezier"),
                ]
                opacity_kfs = [
                    _kf(t=opacity_exit, value=100, interpolation="bezier"),
                    _kf(t=out_t, value=0, interpolation="bezier"),
                ]
            else:
                peak_f = int(round(total_f * 0.5))
                peak_t = min(out_t, in_t + float(peak_f) / fps)
                peak_val = self._impulse_peak_scale(text=raw_text, dur=dur)
                op_in = max(in_t, out_t - 3.0 / fps)
                scale_kfs = [
                    _kf(t=in_t, value=[75, 75, 100], interpolation="bezier"),
                    _kf(t=peak_t, value=[peak_val, peak_val, 100], interpolation="bezier"),
                    _kf(t=out_t, value=[0, 0, 100], interpolation="bezier"),
                ]
                opacity_kfs = [
                    _kf(t=op_in, value=100, interpolation="bezier"),
                    _kf(t=out_t, value=0, interpolation="bezier"),
                ]

            layer = {
                "name": clean_text,
                "type": "text",
                "in_point": in_t,
                "out_point": out_t,
                "z_index": z,
                "text": clean_text,
                "adjustment_layer": False,
                "source_rect": {},
                "props": {
                    "tf_anchor": _prop("ADBE Anchor Point", [0.564, -23.213, 0]),
                    "tf_position": _prop("ADBE Position", [540, 960, 0]),
                    "tf_scale": _prop("ADBE Scale", keyframes=scale_kfs),
                    "tf_rotation": _prop("ADBE Rotate Z", 0),
                    "layer_opacity": _prop("ADBE Opacity", keyframes=opacity_kfs),
                },
                "effects": self._impulse_drop_shadows(),
                "style_instructions": [],
                "text_data": {
                    "layer_meta": {
                        "blendingModeCode": "5212",
                        "startTime": 0.0,
                        "comp_name_target": str(text_comp_name),
                        "enabled": True,
                        "collapseTransformation": True,
                        "motionBlur": True,
                    },
                    "layer_styles_enabled": False,
                    "text_base": {
                        "font": "Point-Light",
                        "fontSize": 100,
                        "applyFill": True,
                        "fillColor": [1, 1, 1],
                        "applyStroke": True,
                        "strokeWidth": 3,
                        "strokeColor": [1, 1, 1],
                        "tracking": -25,
                        "leading": 250,
                        "autoLeading": False,
                        "justificationCode": "7415",
                        "allCaps": False,
                        "leftIndent": 0,
                        "rightIndent": 0,
                        "firstLineIndent": 0,
                        "spaceBefore": 0,
                        "spaceAfter": 0,
                    },
                    "char_styles_ungrouped": [],
                    "no_layout_pass": True,
                    "text_animator": {
                        "name": "Animator 1",
                        "opacity": 0,
                        "properties": [
                            _prop("ADBE Text Position 3D", [0, 25, 0]),
                            _prop("ADBE Text Scale 3D", [50, 50, 100]),
                            _prop("ADBE Text Rotation", 15),
                            _prop("ADBE Text Blur", [15, 15]),
                        ],
                        "selector": {
                            "name": "Range Selector 1",
                            "advanced": {
                                "units": 1,
                                "basedOn": 1,
                                "mode": 1,
                                "maxAmount": 100,
                                "shape": 1,
                                "smoothness": 100,
                                "hiEase": 0,
                                "loEase": 0,
                                "randomizeOrder": 0,
                            },
                            "percentStart": 0,
                            "percentEnd": 100,
                        },
                        "expressible_selector": {
                            "rangeType2": 1,
                            "amount": _prop(
                                "ADBE Text Expressible Amount",
                                expression=self._impulse_expr(expr_delay),
                            ),
                        },
                    },
                },
            }
            out.append(layer)
            z -= 1
            prev_out = out_t
        return out

    def _type4_layers(
        self,
        *,
        seg: Any,
        profile: LayerMotionProfile,
        text_value: str,
        text_comp_name: str,
        mine_comp_name: str,
        z_start: int,
    ) -> List[Dict[str, Any]]:
        t_in = float(seg.in_point)
        t_out = float(seg.out_point)
        dur = max(_FRAME_SEC, t_out - t_in)
        fade_dur = min(0.5, dur * 0.2)
        mine_in = max(0.0, t_in - 0.3)
        mine_out = t_out + 0.1

        mine_text = {
            "name": "mine",
            "type": "text",
            "in_point": mine_in,
            "out_point": mine_out,
            "z_index": z_start,
            "text": text_value.replace("\r", " "),
            "adjustment_layer": False,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [0, -33.5, 0]),
                "tf_position": _prop("ADBE Position", [540, 960, 0]),
                "tf_scale": _prop("ADBE Scale", [100, 100, 100]),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "layer_opacity": _prop(
                    "ADBE Opacity",
                    keyframes=[
                        _kf(t=max(mine_in, t_out - fade_dur), value=100, interpolation="bezier"),
                        _kf(t=max(mine_in, mine_out - _FRAME_SEC), value=0, interpolation="bezier"),
                    ],
                ),
            },
            "effects": {
                "ADBE Box Blur2": {
                    "0001": _prop(
                        "ADBE Box Blur2-0001",
                        keyframes=[
                            _kf(t=max(mine_in, t_out - fade_dur), value=0, interpolation="bezier"),
                            _kf(t=max(mine_in, mine_out - _FRAME_SEC), value=5, interpolation="bezier"),
                        ],
                    ),
                    "0002": _prop("ADBE Box Blur2-0002", 3),
                }
            },
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0.0,
                    "comp_name_target": mine_comp_name,
                    "enabled": True,
                },
                "layer_styles_enabled": False,
                "text_base": {
                    "font": profile.font,
                    "fontSize": int(profile.font_size),
                    "applyFill": True,
                    "fillColor": list(profile.fill_color),
                    "applyStroke": False,
                    "strokeWidth": 0,
                    "strokeColor": None,
                    "tracking": -20,
                    "leading": int(max(72, profile.font_size + 12)),
                    "autoLeading": False,
                    "justificationCode": "7415",
                    "allCaps": True,
                    "leftIndent": 0,
                    "rightIndent": 0,
                    "firstLineIndent": 0,
                    "spaceBefore": 0,
                    "spaceAfter": 0,
                },
                "char_styles_ungrouped": [
                    {"i": i, "font": profile.font, "fontSize": int(profile.font_size)}
                    for i in range(len(text_value.replace("\r", " ")))
                ],
                "no_text_animator": True,
                "no_layout_pass": True,
            },
        }

        precomp_main = {
            "name": mine_comp_name,
            "type": "precomp",
            "in_point": t_in,
            "out_point": t_out,
            "z_index": z_start - 1,
            "text": "",
            "adjustment_layer": False,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                "tf_position": _prop("ADBE Position", [540, 960, 0]),
                "tf_scale": _prop("ADBE Scale", [100, 100, 100]),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "tf_opacity": _prop("ADBE Opacity", 100),
            },
            "effects": {},
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0.0,
                    "motionBlur": True,
                    "enabled": True,
                    "comp_name_target": text_comp_name,
                },
                "layer_styles_enabled": False,
                "precomp_source": {"comp_name": mine_comp_name},
            },
        }

        precomp_glow = {
            "name": f"{mine_comp_name} glow",
            "type": "precomp",
            "in_point": t_in,
            "out_point": t_out,
            "z_index": z_start - 2,
            "text": "",
            "adjustment_layer": False,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [540, 960, 0]),
                "tf_position": _prop("ADBE Position", [540, 960, 0]),
                "tf_scale": _prop(
                    "ADBE Scale",
                    keyframes=[
                        _kf(t=t_in, value=[150.0, 150.0, 100.0], interpolation="bezier"),
                        _kf(t=min(t_out, t_in + 0.5), value=[250.0, 250.0, 100.0], interpolation="bezier"),
                    ],
                ),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "tf_opacity": _prop("ADBE Opacity", 40),
            },
            "effects": {
                "ADBE Box Blur2": {
                    "0001": _prop("ADBE Box Blur2-0001", 5),
                    "0002": _prop("ADBE Box Blur2-0002", 3),
                }
            },
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0.0,
                    "motionBlur": True,
                    "enabled": True,
                    "comp_name_target": text_comp_name,
                },
                "layer_styles_enabled": False,
                "precomp_source": {"comp_name": mine_comp_name},
            },
        }

        return [mine_text, precomp_main, precomp_glow]

    def render(
        self,
        *,
        flow_plan: SubtitleFlowPlan,
        text_comp_name: str,
        mine_comp_name: str,
    ) -> List[Dict[str, Any]]:
        if self.mode == SUBTITLES_MODE_IMPULSE_2ND:
            return self._render_impulse_layers(
                flow_plan=flow_plan,
                text_comp_name=text_comp_name,
            )

        layers: List[Dict[str, Any]] = []
        z = 1000
        for seg in sorted(flow_plan.segments, key=lambda s: (float(s.in_point), str(s.segment_id))):
            p = self._profile(str(seg.style_tag))
            text_value = self._text_value(seg.model_dump(mode="json", by_alias=True))
            style_tag = str(seg.style_tag).strip().upper()

            if self.mode == SUBTITLES_MODE_SCENES_3RD:
                if style_tag == "TYPE_4":
                    block = self._type4_layers(
                        seg=seg,
                        profile=p,
                        text_value=text_value,
                        text_comp_name=text_comp_name,
                        mine_comp_name=mine_comp_name,
                        z_start=z,
                    )
                    layers.extend(block)
                    z -= len(block)
                    continue
                if style_tag == "TYPE_3":
                    block = self._type3_layers(
                        seg=seg,
                        profile=p,
                        text_value=text_value,
                        text_comp_name=text_comp_name,
                        z_start=z,
                    )
                    layers.extend(block)
                    z -= len(block)
                    continue
                if style_tag == "TYPE_5":
                    block = self._type5_layers(
                        seg=seg,
                        profile=p,
                        text_value=text_value,
                        text_comp_name=text_comp_name,
                        z_start=z,
                    )
                    layers.extend(block)
                    z -= len(block)
                    continue
                if style_tag in {"TYPE_1", "TYPE_2", "TYPE_6"}:
                    block = self._scene_regular_with_adjustment_layers(
                        seg=seg,
                        profile=p,
                        text_value=text_value,
                        style_tag=style_tag,
                        text_comp_name=text_comp_name,
                        z_start=z,
                    )
                    layers.extend(block)
                    z -= len(block)
                    continue

            reveal_kind: str | None = "start"

            layer = self._build_regular_layer(
                seg=seg,
                profile=p,
                text_value=text_value,
                z_index=z,
                text_comp_name=text_comp_name,
                reveal_kind=reveal_kind,
            )
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
