from __future__ import annotations

import re
from typing import Any, Dict, List

from core.video_timing import AE_FPS, frame_duration_s, frames_to_seconds
from mlcore.models.subtitles_flow import SubtitleFlowPlan


_FPS = float(AE_FPS)
_FRAME_SEC = frame_duration_s(_FPS)
_FADE_FRAMES = 2.0
_FADE_DUR = frames_to_seconds(_FADE_FRAMES, fps=_FPS)
_ANTICIPATION_SEC = _FRAME_SEC * 2  # 2 кадра предраскрытия

_FONT_NAME = "Montserrat-BoldItalic"
_FONT_SIZE = 60
_TRACKING = -25
_LEADING = 80
_POSITION = [540, 960, 0]
_WHITE = [1.0, 1.0, 1.0]
_FOCUS_RED = [0.898, 0.082, 0.082]  # #E51515

_CLEAN_RE = re.compile(r"[^\w\u0400-\u04FF]+", flags=re.UNICODE)


def _prop(match_name: str, value: Any = None, keyframes: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "match_name": match_name,
        "value": value if not keyframes else None,
        "keyframes": list(keyframes or []),
    }


def _kf(t: float, value: Any, interpolation: str = "bezier") -> Dict[str, Any]:
    code = "6613" if str(interpolation).lower() != "linear" else "6612"
    return {
        "t": float(t),
        "v": value,
        "iit": code,
        "oit": code,
        "ease_in": [{"speed": 0.0, "influence": 16.666666667}],
        "ease_out": [{"speed": 0.0, "influence": 16.666666667}],
    }


def _text_animator_cfg() -> Dict[str, Any]:
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
                "smoothness": 100,
                "hiEase": 0,
                "loEase": 0,
                "randomizeOrder": 0,
            },
            "percentEnd": 100,
        },
    }


def _effects() -> Dict[str, Any]:
    return {
        "ADBE Glo2": {
            "0001": _prop("ADBE Glo2-0001", 2),
            "0002": _prop("ADBE Glo2-0002", 153),
            "0003": _prop("ADBE Glo2-0003", 35),
            "0004": _prop("ADBE Glo2-0004", 0.75),
            "0005": _prop("ADBE Glo2-0005", 2),
            "0006": _prop("ADBE Glo2-0006", 3),
            "0007": _prop("ADBE Glo2-0007", 1),
            "0008": _prop("ADBE Glo2-0008", 3),
            "0009": _prop("ADBE Glo2-0009", 1),
            "0010": _prop("ADBE Glo2-0010", 0),
            "0011": _prop("ADBE Glo2-0011", 0.5),
            "0014": _prop("ADBE Glo2-0014", 1),
        },
        "ADBE Drop Shadow": {
            "0001": _prop("ADBE Drop Shadow-0001", [0, 0, 0, 1]),
            "0002": _prop("ADBE Drop Shadow-0002", 255),
            "0003": _prop("ADBE Drop Shadow-0003", 135),
            "0004": _prop("ADBE Drop Shadow-0004", 3),
            "0005": _prop("ADBE Drop Shadow-0005", 5),
            "0006": _prop("ADBE Drop Shadow-0006", 0),
        },
        "0002:ADBE Drop Shadow": {
            "0001": _prop("ADBE Drop Shadow-0001", [0, 0, 0, 1]),
            "0002": _prop("ADBE Drop Shadow-0002", 255),
            "0003": _prop("ADBE Drop Shadow-0003", 135),
            "0004": _prop("ADBE Drop Shadow-0004", 3),
            "0005": _prop("ADBE Drop Shadow-0005", 15),
            "0006": _prop("ADBE Drop Shadow-0006", 0),
        },
    }


def _norm_word(raw: str) -> str:
    return _CLEAN_RE.sub("", str(raw or "").lower()).strip()


def _focus_word_indices(*, text: str, tokens: List[Any]) -> set[int]:
    text_words = [w for w in str(text).split(" ") if w]
    token_words = sorted(tokens, key=lambda t: (float(t.t_start), float(t.t_end), str(t.text)))
    out: set[int] = set()
    tok_i = 0
    for wi, w in enumerate(text_words):
        wn = _norm_word(w)
        if not wn:
            continue
        for j in range(tok_i, len(token_words)):
            tw = token_words[j]
            if _norm_word(str(tw.text)) != wn:
                continue
            if bool(getattr(tw, "focus", False)):
                out.add(wi)
            tok_i = j + 1
            break
    return out



def _char_styles(*, text: str, focus_word_indices: set[int]) -> List[Dict[str, Any]]:
    styles: List[Dict[str, Any]] = []
    word_idx = 0
    for i, ch in enumerate(text):
        if ch == " ":
            word_idx += 1
            continue
        if ch == "\r":
            continue
        is_focus = word_idx in focus_word_indices
        styles.append(
            {
                "i": i,
                "font": _FONT_NAME,
                "fillColor": list(_FOCUS_RED if is_focus else _WHITE),
            }
        )
    return styles


def _reveal_keyframes(*, seg: Any, text: str) -> List[Dict[str, Any]]:
    in_t = float(seg.in_point)
    out_t = float(seg.out_point)
    dur = max(_FRAME_SEC, out_t - in_t)
    words = [w for w in str(text).split(" ") if w]
    n_words = max(1, len(words))
    tokens = sorted(list(seg.tokens or []), key=lambda t: (float(t.t_start), float(t.t_end), str(t.text)))
    if len(tokens) >= 2:
        kfs: List[Dict[str, Any]] = []
        for idx, tok in enumerate(tokens):
            pct = (float(idx) / float(n_words)) * 100.0
            t = max(in_t, min(float(tok.t_start) - _ANTICIPATION_SEC, out_t))
            kfs.append(_kf(t=t, value=pct, interpolation="bezier"))
        last_end = max(in_t, min(float(tokens[-1].t_end), out_t))
        final_t = min(out_t, max(last_end, out_t - _FADE_DUR))
        if final_t <= in_t:
            final_t = in_t + min(dur * 0.7, dur - _FRAME_SEC)
        kfs.append(_kf(t=final_t, value=100.0, interpolation="bezier"))
        return sorted(kfs, key=lambda x: float(x["t"]))

    reveal_end = out_t - _FADE_DUR
    if reveal_end <= in_t:
        reveal_end = in_t + dur * 0.7
    if reveal_end <= in_t:
        reveal_end = min(out_t, in_t + _FRAME_SEC)
    return [
        _kf(t=in_t, value=0.0, interpolation="bezier"),
        _kf(t=reveal_end, value=100.0, interpolation="bezier"),
    ]


def _fade_keyframes(*, seg: Any) -> List[Dict[str, Any]]:
    in_t = float(seg.in_point)
    out_t = float(seg.out_point)
    fade_start = out_t - _FADE_DUR
    if fade_start <= in_t:
        fade_start = in_t + (out_t - in_t) * 0.8
    if fade_start <= in_t:
        fade_start = in_t
    return [
        _kf(t=fade_start, value=100, interpolation="bezier"),
        _kf(t=out_t, value=0, interpolation="bezier"),
    ]


def build_template_4th_reference_layers(
    *,
    flow_plan: SubtitleFlowPlan,
    text_comp_name: str,
    mine_comp_name: str,
) -> List[Dict[str, Any]]:
    layers: List[Dict[str, Any]] = []
    z = 1000
    for seg in sorted(flow_plan.segments, key=lambda s: (float(s.in_point), str(s.segment_id))):
        text = " ".join(str(seg.text or "").strip().split()).upper()
        if not text:
            continue
        focus_words = _focus_word_indices(text=text, tokens=list(seg.tokens or []))

        # --- Основной слой субтитра в Text-компе ---
        layer = {
            "name": text,
            "type": "text",
            "in_point": float(seg.in_point),
            "out_point": float(seg.out_point),
            "z_index": z,
            "text": text,
            "adjustment_layer": False,
            "source_rect": {},
            "props": {
                "tf_anchor": _prop("ADBE Anchor Point", [0, 0, 0]),
                "tf_position": _prop("ADBE Position", list(_POSITION)),
                "tf_scale": _prop("ADBE Scale", [100, 100, 100]),
                "tf_rotation": _prop("ADBE Rotate Z", 0),
                "layer_opacity": _prop("ADBE Opacity", keyframes=_fade_keyframes(seg=seg)),
                "reveal": _prop("ADBE Text Percent Start", keyframes=_reveal_keyframes(seg=seg, text=text)),
                "anim_opacity": _prop("ADBE Opacity", 0),
            },
            "effects": _effects(),
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0.0,
                    "comp_name_target": str(text_comp_name),
                    "enabled": True,
                    "collapseTransformation": True,
                    "motionBlur": False,
                },
                "layer_styles_enabled": False,
                "text_base": {
                    "font": _FONT_NAME,
                    "fontSize": _FONT_SIZE,
                    "applyFill": True,
                    "fillColor": list(_WHITE),
                    "applyStroke": False,
                    "strokeWidth": 0,
                    "strokeColor": None,
                    "tracking": _TRACKING,
                    "leading": _LEADING,
                    "autoLeading": False,
                    "justificationCode": "7415",
                    "allCaps": True,
                    "leftIndent": 0,
                    "rightIndent": 0,
                    "firstLineIndent": 0,
                    "spaceBefore": 0,
                    "spaceAfter": 0,
                },
                "char_styles_ungrouped": _char_styles(text=text, focus_word_indices=focus_words),
                "text_animator": _text_animator_cfg(),
                "box_text": [900, 160],
            },
        }
        layers.append(layer)
        z -= 1

    return layers
