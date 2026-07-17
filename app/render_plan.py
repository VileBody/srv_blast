from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from core.subtitles_mode import (
    SUBTITLES_MODE_BRAT_5TH,
    SUBTITLES_MODE_JSX_5TH,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_TRENDY_5TH,
)


RENDER_REQUEST_SCHEMA = "ae-native-renderer.render-request.v1"
RENDER_PLAN_VERSION = "render-plan.v1"


class VisualOperationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: Optional[str] = None
    layer: Optional[str] = None
    place: Optional[str] = None


class VisualOperationTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Optional[float] = None
    duration: Optional[float] = None
    end: Optional[float] = None
    anchor: Optional[str] = None
    offset: Optional[float] = None


class VisualOperationAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    path: str
    optional: bool = False


class VisualOperationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: Optional[str] = None
    type: str = Field(alias="kind", serialization_alias="type")
    target: VisualOperationTarget = Field(default_factory=VisualOperationTarget)
    timing: VisualOperationTiming = Field(default_factory=VisualOperationTiming)
    params: Dict[str, Any] = Field(default_factory=dict)
    assets: List[VisualOperationAsset] = Field(default_factory=list)
    required: bool = True


class RenderPlanV1(BaseModel):
    """Canonical Blast-side render plan transported as render-request.v1.

    The AE JSX route still receives project/comps aliases through `to_ae_payload`,
    but Rust always receives the canonical projectSpec/compsSpec shape.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_spec: Dict[str, Any] = Field(alias="projectSpec", serialization_alias="projectSpec")
    comps_spec: List[Dict[str, Any]] = Field(alias="compsSpec", serialization_alias="compsSpec")
    footage_layers: List[Dict[str, Any]] = Field(default_factory=list)
    text_layers: List[Dict[str, Any]] = Field(default_factory=list)
    visual_ops: List[VisualOperationV1] = Field(default_factory=list, alias="visualOps", serialization_alias="visualOps")
    f3_media: List[Dict[str, str]] = Field(default_factory=list)

    def to_ae_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "project": dict(self.project_spec),
            "comps": [dict(comp) for comp in self.comps_spec],
            "footage_layers": [dict(layer) for layer in self.footage_layers],
            "text_layers": [dict(layer) for layer in self.text_layers],
        }
        if self.f3_media:
            payload["f3_media"] = [dict(item) for item in self.f3_media]
        return payload

    def to_native_request(
        self,
        *,
        request_id: Optional[str] = None,
        output_directory: str = "out",
        output_video: str = "output.mp4",
        on_unsupported: str = "report",
    ) -> Dict[str, Any]:
        request = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        request.update(
            {
                "schema": RENDER_REQUEST_SCHEMA,
                "payloadVersion": RENDER_PLAN_VERSION,
                "action": "render",
                "assetsSpec": {"root": "."},
                "outputSpec": {
                    "directory": output_directory,
                    "video": output_video,
                    "writeScene": True,
                },
                "policy": {"onUnsupported": on_unsupported},
            }
        )
        if request_id:
            request["requestId"] = str(request_id)
        return request


def build_render_plan_v1(
    *,
    main_comp_name: str,
    subtitles_mode: str,
    comps: List[Dict[str, Any]],
    footage_layers: List[Dict[str, Any]],
    text_layers: List[Dict[str, Any]],
    full_edit_config: Dict[str, Any],
    f3_media: List[Dict[str, str]],
) -> RenderPlanV1:
    project_spec = {"mainCompName": main_comp_name, "subtitlesMode": subtitles_mode}
    visual_ops = build_visual_ops(
        subtitles_mode=subtitles_mode,
        full_edit_config=full_edit_config,
        f3_media=f3_media,
    )
    return RenderPlanV1(
        projectSpec=project_spec,
        compsSpec=comps,
        footage_layers=footage_layers,
        text_layers=text_layers,
        visualOps=visual_ops,
        f3_media=f3_media,
    )


def build_visual_ops(
    *,
    subtitles_mode: str,
    full_edit_config: Dict[str, Any],
    f3_media: List[Dict[str, str]],
) -> List[VisualOperationV1]:
    ops: List[VisualOperationV1] = []
    subtitle = _subtitle_operation(subtitles_mode, full_edit_config)
    if subtitle is not None:
        ops.append(subtitle)

    for op in (
        _f3_operation(full_edit_config, f3_media),
        _f2_operation(full_edit_config),
        _f4_operation(full_edit_config),
        _f1_operation(full_edit_config),
        _f5_operation(full_edit_config),
    ):
        if op is not None:
            ops.append(op)
    return ops


def _subtitle_operation(mode: str, cfg: Dict[str, Any]) -> Optional[VisualOperationV1]:
    if mode == SUBTITLES_MODE_TRENDY_5TH:
        block = _dict(cfg.get("subtitles_jsx"))
        return VisualOperationV1(
            id="subtitles_trendy_5th",
            kind="subtitle.trendy.v1",
            params={
                "source_mode": mode,
                "word_timings": list(block.get("word_timings") or []),
                "fill": _subtitle_fill_rgb01(),
                "blend": _subtitle_blend_mode(),
            },
        )
    if mode == SUBTITLES_MODE_BRAT_5TH:
        block = _dict(cfg.get("subtitles_jsx"))
        params: Dict[str, Any] = {
            "source_mode": mode,
            "word_timings": list(block.get("word_timings") or []),
            "fill": _subtitle_fill_rgb01(),
            "blend": _subtitle_blend_mode(),
        }
        if block.get("bpm") is not None:
            params["bpm"] = float(block["bpm"])
        return VisualOperationV1(id="subtitles_brat_5th", kind="subtitle.brat.v1", params=params)

    # Legacy is intentionally out of native scope, but we still preserve it as
    # a required operation so Rust reports not_implemented instead of dropping it.
    if mode == SUBTITLES_MODE_LEGACY_BLOCKS:
        return VisualOperationV1(
            id="subtitles_legacy_blocks",
            kind="subtitle.bot.legacy_blocks.v1",
            params={"source_mode": mode, "segments": _subtitle_segments(mode, cfg)},
        )

    if mode:
        return VisualOperationV1(
            id=f"subtitles_{mode}",
            kind=f"subtitle.bot.{mode}.v1",
            params={"source_mode": mode, "segments": _subtitle_segments(mode, cfg)},
        )
    return None


def _f3_operation(cfg: Dict[str, Any], f3_media: List[Dict[str, str]]) -> Optional[VisualOperationV1]:
    f3 = _dict(cfg.get("f3"))
    hook = _clean(f3.get("hook"))
    transition = _clean(f3.get("transition"))
    extra = _clean(f3.get("extra"))
    if not (hook or transition or extra):
        return None
    ids = [value for value in (hook, transition, extra) if value]
    params: Dict[str, Any] = {
        "detected_effect_ids": ids,
        "hook": hook,
        "transition": transition,
        "extra": extra,
        "extra_full": bool(f3.get("extra_full")),
    }
    if f3.get("drop_time") is not None:
        params["drop_time"] = float(f3["drop_time"])
    if _clean(f3.get("hook_extend")):
        params["hook_extend"] = _clean(f3.get("hook_extend"))
    assets = [
        VisualOperationAsset(
            role=("audio" if item.get("relpath", "").startswith("media/audio/") else "overlay"),
            path=str(item.get("relpath") or "").strip().strip("/"),
            optional=True,
        )
        for item in f3_media
        if str(item.get("relpath") or "").strip()
    ]
    return VisualOperationV1(id="hook_f3_effect", kind="hook.f3.effect.v1", params=params, assets=assets)


def _f2_operation(cfg: Dict[str, Any]) -> Optional[VisualOperationV1]:
    f2 = _dict(cfg.get("f2"))
    shape = _clean(f2.get("shape"))
    if not shape:
        return None
    params = {"shape": shape}
    if f2.get("drop_time") is not None:
        params["drop_time"] = float(f2["drop_time"])
    if f2.get("seed") is not None:
        params["seed"] = int(f2["seed"])
    color = _clean(os.environ.get("F2_SHAPE_COLOR_HEX"))
    if color:
        params["shape_fill"] = color
    return VisualOperationV1(id="hook_f2_object", kind="hook.f2.object.v1", params=params)


def _f4_operation(cfg: Dict[str, Any]) -> Optional[VisualOperationV1]:
    f4 = _dict(cfg.get("f4"))
    device = _clean(f4.get("device"))
    if not device:
        return None
    params: Dict[str, Any] = {"device": device}
    if f4.get("bpm") is not None:
        params["bpm"] = float(f4["bpm"])
    timing = VisualOperationTiming()
    if f4.get("drop_time") is not None:
        params["drop_time"] = float(f4["drop_time"])
        timing.anchor = "drop"
        timing.start = float(f4["drop_time"])
    return VisualOperationV1(id="hook_f4_motion", kind="hook.f4.motion.v1", timing=timing, params=params)


def _f1_operation(cfg: Dict[str, Any]) -> Optional[VisualOperationV1]:
    f1 = _dict(cfg.get("f1"))
    sound_url = _clean(f1.get("sound_url"))
    if not sound_url:
        return None
    drop_time = float(f1.get("drop_time") or 0.0)
    start = max(0.5, 0.0)
    end = max(start, drop_time - 0.5)
    duration = max(0.0, end - start)
    params: Dict[str, Any] = {
        "drop_time": drop_time,
        "impactAt": start,
        "duration": duration,
        "fadeOut": 0.1,
        "duck": {"amountDb": -12.0, "attack": 0.05, "release": 0.25},
    }
    if _clean(f1.get("text")):
        params["subtitle_text"] = _clean(f1.get("text"))
    if f1.get("seed") is not None:
        params["seed"] = int(f1["seed"])
    return VisualOperationV1(
        id="hook_f1_sound",
        kind="hook.f1.sound.v1",
        timing=VisualOperationTiming(start=start, duration=duration, anchor="drop"),
        params=params,
        assets=[VisualOperationAsset(role="audio", path=_audio_local_path(sound_url), optional=False)],
    )


def _f5_operation(cfg: Dict[str, Any]) -> Optional[VisualOperationV1]:
    f5 = _dict(cfg.get("f5"))
    audio_url = _clean(f5.get("audio_url"))
    if not f5:
        return None
    params: Dict[str, Any] = {
        "device": _clean(f5.get("device") or f5.get("chosen_device")),
        "tts_text": _clean(f5.get("tts_text")),
        "duck": {"amountDb": -18.0, "attack": 0.2, "release": 0.4},
    }
    timing = VisualOperationTiming()
    if f5.get("drop_rel_sec") is not None:
        params["drop_time"] = float(f5["drop_rel_sec"])
        timing.start = float(f5["drop_rel_sec"])
        timing.anchor = "drop"
    if f5.get("focal_start_ms") is not None:
        params["focal_start_ms"] = int(f5["focal_start_ms"])
    if f5.get("audio_duration_ms") is not None:
        timing.duration = max(0.0, float(f5["audio_duration_ms"]) / 1000.0)
    if f5.get("combo_seed") is not None:
        params["seed"] = int(f5["combo_seed"])
    assets = []
    if audio_url:
        assets.append(VisualOperationAsset(role="tts_audio", path=_audio_local_path(audio_url), optional=False))
    return VisualOperationV1(
        id="hook_f5_cognition",
        kind="hook.f5.cognition.v1",
        timing=timing,
        params={k: v for k, v in params.items() if v not in ("", None)},
        assets=assets,
        required=bool(audio_url),
    )


def _subtitle_segments(mode: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = _dict(cfg.get("subtitle_flow_plan")) or _dict(cfg.get("subtitle_payload"))
    raw_segments = (
        _list(source.get("segments"))
        or _list(source.get("scenes"))
        or _list(source.get("subtitles"))
    )
    global_words = _list(source.get("word_timings"))
    segments: List[Dict[str, Any]] = []
    for index, segment in enumerate(raw_segments):
        if not isinstance(segment, dict):
            continue
        start = _number(segment, "start", "in", "in_point")
        end = _number(segment, "end", "out", "out_point")
        if start is None or end is None or end <= start:
            continue
        words = (
            _list(segment.get("words"))
            or _list(segment.get("tokens"))
            or _list(segment.get("word_timings"))
            or _words_in_window(global_words, start, end)
        )
        normalized_words = _normalize_words(words, start, end)
        text = _clean(segment.get("text")) or " ".join(word["word"] for word in normalized_words)
        if not text:
            continue
        segments.append(
            {
                "id": segment.get("id", index + 1),
                "text": text,
                "start": start,
                "end": end,
                "lines": segment.get("lines") or [],
                "words": normalized_words,
                "type": segment.get("type") or segment.get("style_tag") or mode,
                "focusWord": segment.get("focusWord") or segment.get("focus_word"),
                "focusStyle": segment.get("focusStyle") or segment.get("focus_style"),
            }
        )
    return segments


def _normalize_words(words: List[Any], start: float, end: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    fallback = max((end - start) / max(len(words), 1), 1.0 / 60.0)
    for index, raw in enumerate(words):
        if isinstance(raw, str):
            text = raw.strip()
            word_start = start + index * fallback
            word_end = min(end, word_start + fallback)
            focus = False
        elif isinstance(raw, dict):
            text = _clean(raw.get("word") or raw.get("text") or raw.get("w"))
            word_start = _number(raw, "start", "t_start", "s")
            word_end = _number(raw, "end", "t_end", "e")
            if word_start is None:
                word_start = start + index * fallback
            if word_end is None:
                word_end = min(end, word_start + fallback)
            focus = bool(raw.get("focus") or raw.get("voice"))
        else:
            continue
        if not text:
            continue
        out.append(
            {
                "word": text,
                "start": max(start, float(word_start)),
                "end": min(end, max(float(word_end), float(word_start) + 1.0 / 600.0)),
                "focus": focus,
            }
        )
    return out


def _words_in_window(words: List[Any], start: float, end: float) -> List[Any]:
    out = []
    for word in words:
        if not isinstance(word, dict):
            continue
        word_start = _number(word, "start", "t_start")
        word_end = _number(word, "end", "t_end")
        if word_start is not None and word_end is not None and word_start >= start - 1e-6 and word_end <= end + 1e-6:
            out.append(word)
    return out


def _subtitle_fill_rgb01() -> List[float]:
    raw = _clean(os.environ.get("SUBTITLES_FORCE_FILL_HEX"))
    if not raw:
        return [1.0, 1.0, 1.0, 1.0]
    rgb = _hex_to_rgb01(raw)
    return [*rgb, 1.0] if rgb else [1.0, 1.0, 1.0, 1.0]


def _subtitle_blend_mode() -> Optional[str]:
    return "difference" if _clean(os.environ.get("BG_MODE")).lower() == "solid_strobe" else None


def _hex_to_rgb01(value: str) -> Optional[List[float]]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return [int(raw[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None


def _audio_local_path(url: str) -> str:
    raw_name = (str(url).split("?", 1)[0].rstrip("/").split("/")[-1] or "audio.wav").strip()
    file_name = unquote(raw_name) or raw_name
    return f"media/audio/{Path(file_name).name}"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _number(source: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        try:
            value = source.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None
