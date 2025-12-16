from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.config.styles import TextAnimPresetId, TransformPresetId


class TextStyleId(str, Enum):
    MAIN = "main_subtitle"
    HIGHLIGHT = "highlight_subtitle"


class Keyframe(BaseModel):
    time: float
    value: Any
    templateRef: Optional[str] = None


class KeyframedValue(BaseModel):
    keys: List[Keyframe]


class ProceduralSelectorRampAbs(BaseModel):
    """Procedural shorthand for selector_start reveal: 2-key ramp in ABS comp time."""

    kind: Literal["selector_ramp_abs"] = "selector_ramp_abs"
    t_start: float
    t_end: float
    from_value: float = Field(0, alias="from")
    to: float = 100
    tpl_start: Optional[str] = None
    tpl_end: Optional[str] = None

    @model_validator(mode="after")
    def _validate_order(self) -> "ProceduralSelectorRampAbs":
        if self.t_end < self.t_start:
            raise ValueError("t_end must be >= t_start for selector_ramp_abs")
        return self


class ProceduralSelectorSteps3Abs(BaseModel):
    """Procedural shorthand for selector_start step reveal (t0/t1/t2) in ABS comp time."""

    kind: Literal["selector_steps_3_abs"] = "selector_steps_3_abs"
    t0: float
    t1: float
    t2: float
    v0: float = 25
    v1: float = 50
    v2: float = 100
    tpl0: Optional[str] = None
    tpl1: Optional[str] = None
    tpl2: Optional[str] = None

    @model_validator(mode="after")
    def _validate_order(self) -> "ProceduralSelectorSteps3Abs":
        if not (self.t0 <= self.t1 <= self.t2):
            raise ValueError("t0 <= t1 <= t2 is required for selector_steps_3_abs")
        return self


class ProceduralOpacityFadeAbs(BaseModel):
    """Procedural shorthand for transform opacity fade (t_start/t_end) in ABS comp time."""

    kind: Literal["opacity_fade_abs"] = "opacity_fade_abs"
    t_start: float
    t_end: float
    from_value: float = Field(100, alias="from")
    to: float = 0
    tpl_start: Optional[str] = "tpl_fade_out"
    tpl_end: Optional[str] = "tpl_fade_in_stop"

    @model_validator(mode="after")
    def _validate_order(self) -> "ProceduralOpacityFadeAbs":
        if self.t_end < self.t_start:
            raise ValueError("t_end must be >= t_start for opacity_fade_abs")
        return self


ProceduralSpec = Annotated[
    Union[ProceduralSelectorRampAbs, ProceduralSelectorSteps3Abs, ProceduralOpacityFadeAbs],
    Field(discriminator="kind"),
]


class ProceduralValue(BaseModel):
    procedural: ProceduralSpec


OverrideValue = Union[
    int,
    float,
    str,
    bool,
    List[Any],
    # Keyframed value payload: {keys:[{time,value,templateRef?}, ...]}
    KeyframedValue,
    # Procedural shorthand payload: {procedural:{kind:..., ...}} -> baked to keyframes in render_v1
    ProceduralValue,
    # Fallback for rarely-used complex payloads (kept for backwards compatibility)
    Dict[str, Any],
]


# ---------------------------------------------------------------------------
# High-level param overrides (LLM-friendly)
#
# LLM should NOT output low-level keyframes for common text presets.
# Instead it outputs small param objects (absolute comp time), and the assembler
# converts them into procedural overrides for the motion resolver.
# ---------------------------------------------------------------------------


class AnimRevealRampAbs(BaseModel):
    kind: Literal["reveal_ramp_abs"] = "reveal_ramp_abs"
    t_start: float
    t_end: float
    # optional tuning knobs (defaults match text_motion_library.json expectations)
    from_value: float = Field(default=0.0, alias="from")
    to: float = 100.0
    tpl_start: str = "tpl_ease_explosive"
    tpl_end: str = "tpl_opacity_fade_end_fast"


class AnimRevealSteps3Abs(BaseModel):
    kind: Literal["reveal_steps_3_abs"] = "reveal_steps_3_abs"
    t0: float
    t1: float
    t2: float
    v0: float = 25.0
    v1: float = 50.0
    v2: float = 100.0
    tpl0: str = "tpl_linear_hold"
    tpl1: str = "tpl_ease_explosive"
    tpl2: str = "tpl_ease_explosive"


AnimParams = Annotated[
    Union[AnimRevealRampAbs, AnimRevealSteps3Abs],
    Field(discriminator="kind"),
]


class TransformOpacityFadeAbs(BaseModel):
    kind: Literal["opacity_fade_abs"] = "opacity_fade_abs"
    t_start: float
    t_end: float
    from_value: float = Field(default=100.0, alias="from")
    to: float = 0.0
    tpl_start: str = "tpl_fade_out"
    tpl_end: str = "tpl_fade_in_stop"


TransformParams = Annotated[
    Union[TransformOpacityFadeAbs],
    Field(discriminator="kind"),
]


class BaseLayer(BaseModel):
    # We want the LLM contract to be strict: unknown fields should fail fast.
    # This also reduces "Union explosion" noise when a payload is malformed.
    model_config = ConfigDict(extra="forbid")

    type: str
    name: Optional[str] = None
    inPoint: Optional[float] = None
    outPoint: Optional[float] = None
    startTime: Optional[float] = None
    enabled: Optional[bool] = True
    audioEnabled: Optional[bool] = None


class RefLayer(BaseLayer):
    type: Literal["ref"] = "ref"
    refId: str
    presetId: Optional[str] = None
    fitPolicy: Optional[str] = None


class TextLayer(BaseLayer):
    type: Literal["text"] = "text"

    styleId: TextStyleId
    content: str

    # HARD CONTRACT: must be present for every subtitle/text layer
    animId: TextAnimPresetId
    transformId: TransformPresetId
    overrides: Dict[str, OverrideValue] = Field(default_factory=dict)
    # New (preferred): high-level preset params (ABS comp time).
    # Assembler will convert these into overrides.* procedural blocks.
    animParams: Optional[AnimParams] = None
    transformParams: Optional[TransformParams] = None

    @model_validator(mode="after")
    def _validate_required_overrides(self) -> "TextLayer":
        def _assert_tpl(tpl: Optional[str], field: str) -> None:
            if tpl is None:
                return
            if not isinstance(tpl, str) or not tpl.startswith("tpl_"):
                raise ValueError(f"{field}: templateRef must be 'tpl_*' from text_motion_library.json")

        def _assert_time_in_range(t: float, field: str) -> None:
            # IMPORTANT: all times are ABSOLUTE comp time (seconds)
            if self.inPoint is not None and t < float(self.inPoint) - 1e-4:
                raise ValueError(f"{field}: time {t} is before inPoint {self.inPoint}")
            if self.outPoint is not None and t > float(self.outPoint) + 1e-4:
                raise ValueError(f"{field}: time {t} is after outPoint {self.outPoint}")

        def _assert_percent(v: float, field: str) -> None:
            try:
                fv = float(v)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"{field}: value must be a number") from exc
            if fv < -1e-4 or fv > 100.0 + 1e-4:
                raise ValueError(f"{field}: value {fv} out of range 0..100")

        def _validate_keyframes(keys: List[Keyframe], field_prefix: str, *, require_min: int = 2) -> None:
            if len(keys) < require_min:
                raise ValueError(f"{field_prefix} must have at least {require_min} keyframes")
            for i, k in enumerate(keys):
                _assert_time_in_range(float(k.time), f"{field_prefix}.keys[{i}].time")
                if k.templateRef is not None:
                    _assert_tpl(k.templateRef, f"{field_prefix}.keys[{i}].templateRef")

        if self.animId == TextAnimPresetId.REVEAL_OPACITY:
            v = self.overrides.get("selector_start")
            if v is None and self.animParams is None:
                raise ValueError(
                    "anim_reveal_opacity requires either animParams (preferred) or overrides.selector_start"
                )

            # Preferred path: animParams
            if v is None and self.animParams is not None:
                ap = self.animParams
                if isinstance(ap, AnimRevealRampAbs):
                    _assert_time_in_range(float(ap.t_start), "animParams.t_start")
                    _assert_time_in_range(float(ap.t_end), "animParams.t_end")
                    _assert_percent(ap.from_value, "animParams.from")
                    _assert_percent(ap.to, "animParams.to")
                    _assert_tpl(ap.tpl_start, "animParams.tpl_start")
                    _assert_tpl(ap.tpl_end, "animParams.tpl_end")
                    return self

                if isinstance(ap, AnimRevealSteps3Abs):
                    _assert_time_in_range(float(ap.t0), "animParams.t0")
                    _assert_time_in_range(float(ap.t1), "animParams.t1")
                    _assert_time_in_range(float(ap.t2), "animParams.t2")
                    _assert_percent(ap.v0, "animParams.v0")
                    _assert_percent(ap.v1, "animParams.v1")
                    _assert_percent(ap.v2, "animParams.v2")
                    _assert_tpl(ap.tpl0, "animParams.tpl0")
                    _assert_tpl(ap.tpl1, "animParams.tpl1")
                    _assert_tpl(ap.tpl2, "animParams.tpl2")
                    return self

            # Back-compat: raw overrides.selector_start (keys/procedural) are still accepted
            # and validated below (existing logic in this module handles it).
            # If we get here, keep existing validation behavior.

            # Variant A: raw keyframes (ABS time)
            if isinstance(v, KeyframedValue):
                _validate_keyframes(v.keys, "overrides.selector_start", require_min=2)
                for i, k in enumerate(v.keys):
                    _assert_percent(k.value, f"overrides.selector_start.keys[{i}].value")
                return self

            # Variant B: procedural shorthands (ABS time)
            if isinstance(v, ProceduralValue):
                spec = v.procedural
                if spec.kind == "selector_ramp_abs":
                    _assert_time_in_range(float(spec.t_start), "overrides.selector_start.procedural.t_start")
                    _assert_time_in_range(float(spec.t_end), "overrides.selector_start.procedural.t_end")
                    _assert_percent(spec.from_value, "overrides.selector_start.procedural.from")
                    _assert_percent(spec.to, "overrides.selector_start.procedural.to")
                    _assert_tpl(spec.tpl_start, "overrides.selector_start.procedural.tpl_start")
                    _assert_tpl(spec.tpl_end, "overrides.selector_start.procedural.tpl_end")
                    return self

                if spec.kind == "selector_steps_3_abs":
                    _assert_time_in_range(float(spec.t0), "overrides.selector_start.procedural.t0")
                    _assert_time_in_range(float(spec.t1), "overrides.selector_start.procedural.t1")
                    _assert_time_in_range(float(spec.t2), "overrides.selector_start.procedural.t2")
                    _assert_percent(spec.v0, "overrides.selector_start.procedural.v0")
                    _assert_percent(spec.v1, "overrides.selector_start.procedural.v1")
                    _assert_percent(spec.v2, "overrides.selector_start.procedural.v2")
                    _assert_tpl(spec.tpl0, "overrides.selector_start.procedural.tpl0")
                    _assert_tpl(spec.tpl1, "overrides.selector_start.procedural.tpl1")
                    _assert_tpl(spec.tpl2, "overrides.selector_start.procedural.tpl2")
                    return self

                raise ValueError(
                    f"overrides.selector_start procedural kind {spec.kind!r} is not allowed for anim_reveal_opacity"
                )

            # Backwards-compat: tolerate dict {keys:[...]} (ABS time), even if union picked Dict[str,Any]
            if isinstance(v, dict) and "keys" in v:
                try:
                    kv = KeyframedValue.model_validate(v)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError("overrides.selector_start must be {keys:[...]}") from exc
                _validate_keyframes(kv.keys, "overrides.selector_start", require_min=2)
                for i, k in enumerate(kv.keys):
                    _assert_percent(k.value, f"overrides.selector_start.keys[{i}].value")
                return self

            raise ValueError(
                "anim_reveal_opacity requires overrides.selector_start as {keys:[...]} "
                "or {procedural:{kind:selector_ramp_abs|selector_steps_3_abs,...}}"
            )

        # Optional: transform opacity fade as high-level params
        if self.transformParams is not None:
            tp = self.transformParams
            if isinstance(tp, TransformOpacityFadeAbs):
                _assert_time_in_range(float(tp.t_start), "transformParams.t_start")
                _assert_time_in_range(float(tp.t_end), "transformParams.t_end")
                _assert_percent(tp.from_value, "transformParams.from")
                _assert_percent(tp.to, "transformParams.to")
                _assert_tpl(tp.tpl_start, "transformParams.tpl_start")
                _assert_tpl(tp.tpl_end, "transformParams.tpl_end")

        # Optional: allow procedural ABS fade for transform opacity (tf_subtitle_base exposes 'opacity')
        op = self.overrides.get("opacity")
        if isinstance(op, ProceduralValue):
            spec = op.procedural
            if spec.kind != "opacity_fade_abs":
                raise ValueError(
                    f"overrides.opacity procedural kind {spec.kind!r} is not allowed (expected 'opacity_fade_abs')"
                )
            _assert_time_in_range(float(spec.t_start), "overrides.opacity.procedural.t_start")
            _assert_time_in_range(float(spec.t_end), "overrides.opacity.procedural.t_end")
            _assert_percent(spec.from_value, "overrides.opacity.procedural.from")
            _assert_percent(spec.to, "overrides.opacity.procedural.to")
            _assert_tpl(spec.tpl_start, "overrides.opacity.procedural.tpl_start")
            _assert_tpl(spec.tpl_end, "overrides.opacity.procedural.tpl_end")

        if isinstance(op, KeyframedValue):
            _validate_keyframes(op.keys, "overrides.opacity", require_min=2)
            for i, k in enumerate(op.keys):
                _assert_percent(k.value, f"overrides.opacity.keys[{i}].value")

        return self


class AdjustmentLayer(BaseLayer):
    type: Literal["adjustment"] = "adjustment"


Layer = Annotated[
    Union[RefLayer, TextLayer, AdjustmentLayer],
    Field(discriminator="type"),
]


class FootageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["footage"] = "footage"
    path: str
    name: Optional[str] = None
    isRef: Optional[bool] = None


class CompItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["comp"] = "comp"
    name: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    pixelAspect: Optional[float] = None
    duration: Optional[float] = None
    layers: List[Layer] = Field(default_factory=list)


Item = Annotated[
    Union[FootageItem, CompItem],
    Field(discriminator="type"),
]


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    defaults: Optional[Dict[str, Any]] = None
    global_start_sec: Optional[float] = None
    global_end_sec: Optional[float] = None
    fitPolicy: Optional[str] = None
    audioRefId: Optional[str] = None
    stylePack: Optional[str] = None


class AeComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectSettings: Optional[ProjectSettings] = None
    entryPoint: Optional[str] = None
    items: List[Item] = Field(default_factory=list)
