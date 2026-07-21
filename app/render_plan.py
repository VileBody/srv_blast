from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.subtitles_mode import (
    SUBTITLES_MODE_BRAT_5TH,
    SUBTITLES_MODE_JSX_5TH,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_TRENDY_5TH,
)


RENDER_REQUEST_SCHEMA = "ae-native-renderer.render-request.v1"
RENDER_PLAN_VERSION = "render-plan.v1"
RENDER_PLAN_SCHEMA_VERSION = "render-plan.v1.1"


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


class NativeTuningSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Optional[str] = "builtin:p0p1-readiness"
    overrides: Dict[str, Any] = Field(default_factory=dict)


class ProjectSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    main_comp_name: str = Field(alias="mainCompName", serialization_alias="mainCompName")
    subtitles_mode: str = Field(default="", alias="subtitlesMode", serialization_alias="subtitlesMode")


class CompositionSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    w: int
    h: int
    fps: float
    dur: float
    pixel_aspect: float = Field(default=1.0, alias="pixelAspect", serialization_alias="pixelAspect")
    work_area_start: float = Field(default=0.0, alias="workAreaStart", serialization_alias="workAreaStart")
    work_area_duration: Optional[float] = Field(default=None, alias="workAreaDuration", serialization_alias="workAreaDuration")
    display_start_time: float = Field(default=0.0, alias="displayStartTime", serialization_alias="displayStartTime")
    bg_color: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], alias="bgColor", serialization_alias="bgColor")
    parent_folder_path: Optional[str] = Field(default=None, alias="parentFolderPath", serialization_alias="parentFolderPath")


class KeyframeEaseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed: float
    influence: float


class KeyframeSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float
    v: Any = None
    iit: str = "6613"
    oit: str = "6613"
    ease_in: List[KeyframeEaseV1] = Field(default_factory=list)
    ease_out: List[KeyframeEaseV1] = Field(default_factory=list)


class PropertySpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_name: Optional[str] = None
    value: Any = None
    keyframes: List[KeyframeSpecV1] = Field(default_factory=list)
    expression: Optional[str] = None


class StyleInstructionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    font: str
    size: float
    italic: bool = False


class LayerSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    in_point: float
    out_point: float
    z_index: int
    text: str = ""
    adjustment_layer: bool = False
    comp_id: Optional[int] = None
    comp_name: Optional[str] = None
    source_rect: Dict[str, Any] = Field(default_factory=dict)
    props: Dict[str, PropertySpecV1] = Field(default_factory=dict)
    effects: Dict[str, Dict[str, PropertySpecV1]] = Field(default_factory=dict)
    style_instructions: List[StyleInstructionV1] = Field(default_factory=list)
    text_data: Dict[str, Any] = Field(default_factory=dict)


class RegistryEffectV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    match_name: str = Field(alias="matchName", serialization_alias="matchName")
    params: Dict[str, Any] = Field(default_factory=dict)


class EffectParameterSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str
    default_value: Any = Field(default=None, alias="default", serialization_alias="default")
    minimum: Optional[float] = Field(default=None, alias="min", serialization_alias="min")
    maximum: Optional[float] = Field(default=None, alias="max", serialization_alias="max")
    keyframe: bool = True


class StyleRegistryEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    style_id: str = Field(alias="styleId", serialization_alias="styleId")
    version: str = "v1"
    backend: str = "native_approximation"
    parity: str = "approximate"
    fallback_policy: str = Field(
        default="capability_report",
        alias="fallbackPolicy",
        serialization_alias="fallbackPolicy",
    )
    effect_graph: List[RegistryEffectV1] = Field(
        default_factory=list,
        alias="effectGraph",
        serialization_alias="effectGraph",
    )
    tunables: Dict[str, Any] = Field(default_factory=dict)
    requirements: Dict[str, List[str]] = Field(default_factory=dict)
    supported_backends: List[str] = Field(
        default_factory=lambda: ["native_approximation"],
        alias="supportedBackends",
        serialization_alias="supportedBackends",
    )
    compatibility: Dict[str, Any] = Field(default_factory=lambda: {"renderPlan": "v1"})
    golden_fixtures: List[str] = Field(
        default_factory=list,
        alias="goldenFixtures",
        serialization_alias="goldenFixtures",
    )
    performance_class: str = Field(
        default="medium",
        alias="performanceClass",
        serialization_alias="performanceClass",
    )
    migration_from: List[str] = Field(
        default_factory=list,
        alias="migrationFrom",
        serialization_alias="migrationFrom",
    )


class EffectRegistryEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    stable_id: str = Field(alias="stableId", serialization_alias="stableId")
    ae_match_name: str = Field(alias="aeMatchName", serialization_alias="aeMatchName")
    backend: str
    parity: str = "approximate"
    fallback_policy: str = Field(
        default="capability_report",
        alias="fallbackPolicy",
        serialization_alias="fallbackPolicy",
    )
    plugin_identifier: Optional[str] = Field(
        default=None,
        alias="pluginIdentifier",
        serialization_alias="pluginIdentifier",
    )
    parameter_schema: Dict[str, EffectParameterSpecV1] = Field(
        default_factory=dict,
        alias="parameterSchema",
        serialization_alias="parameterSchema",
    )
    keyframe_support: bool = Field(
        default=True,
        alias="keyframeSupport",
        serialization_alias="keyframeSupport",
    )
    alpha_requirements: str = Field(
        default="straight-rgba8-boundary/premultiplied-float-effects",
        alias="alphaRequirements",
        serialization_alias="alphaRequirements",
    )
    color_requirements: str = Field(
        default="unmanaged-srgb-approximation",
        alias="colorRequirements",
        serialization_alias="colorRequirements",
    )
    deterministic_seed_policy: str = Field(
        default="not_applicable",
        alias="deterministicSeedPolicy",
        serialization_alias="deterministicSeedPolicy",
    )


class RenderPlanV1(BaseModel):
    """Canonical Blast-side render plan transported as render-request.v1.

    The AE JSX route still receives project/comps aliases through `to_ae_payload`,
    but Rust always receives the canonical projectSpec/compsSpec shape.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(
        default=RENDER_PLAN_SCHEMA_VERSION,
        alias="schemaVersion",
        serialization_alias="schemaVersion",
    )
    project_spec: ProjectSpecV1 = Field(alias="projectSpec", serialization_alias="projectSpec")
    comps_spec: List[CompositionSpecV1] = Field(alias="compsSpec", serialization_alias="compsSpec")
    footage_layers: List[LayerSpecV1] = Field(default_factory=list)
    text_layers: List[LayerSpecV1] = Field(default_factory=list)
    visual_ops: List[VisualOperationV1] = Field(default_factory=list, alias="visualOps", serialization_alias="visualOps")
    f3_media: List[Dict[str, str]] = Field(default_factory=list)
    requirements: Dict[str, Any] = Field(default_factory=dict)
    style_registry: List[StyleRegistryEntryV1] = Field(default_factory=list, alias="styleRegistry", serialization_alias="styleRegistry")
    effect_registry: List[EffectRegistryEntryV1] = Field(default_factory=list, alias="effectRegistry", serialization_alias="effectRegistry")
    golden_refs: List[Dict[str, Any]] = Field(default_factory=list, alias="goldenRefs", serialization_alias="goldenRefs")
    tuning_spec: NativeTuningSpecV1 = Field(
        default_factory=NativeTuningSpecV1,
        alias="tuningSpec",
        serialization_alias="tuningSpec",
    )

    @model_validator(mode="after")
    def validate_contract(self) -> "RenderPlanV1":
        if self.schema_version != RENDER_PLAN_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported RenderPlan schemaVersion={self.schema_version!r}; expected {RENDER_PLAN_SCHEMA_VERSION!r}"
            )
        if not any(comp.name == self.project_spec.main_comp_name for comp in self.comps_spec):
            raise ValueError(f"main comp {self.project_spec.main_comp_name!r} is absent from compsSpec")
        return self

    def to_ae_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "project": self.project_spec.model_dump(mode="json", by_alias=True, exclude_none=True),
            "comps": [comp.model_dump(mode="json", by_alias=True, exclude_none=True) for comp in self.comps_spec],
            "footage_layers": [layer.model_dump(mode="json", exclude_none=True) for layer in self.footage_layers],
            "text_layers": [layer.model_dump(mode="json", exclude_none=True) for layer in self.text_layers],
        }
        if self.f3_media:
            payload["f3_media"] = [dict(item) for item in self.f3_media]
        return payload

    def to_ae_overlay_config(self) -> Dict[str, Any]:
        """Compile canonical visual operations into legacy JSX builder inputs.

        The individual JSX builders remain useful compatibility compilers, but
        they must not read production semantics from the original bot config.
        Keeping this adapter on RenderPlan makes visualOps the shared source for
        both the AE and native backends.
        """
        config: Dict[str, Any] = {}
        for operation in self.visual_ops:
            params = dict(operation.params)
            if operation.type in {"subtitle.trendy.v1", "subtitle.brat.v1"}:
                config["subtitles_jsx"] = {
                    "mode": params.get("source_mode"),
                    "word_timings": list(params.get("word_timings") or []),
                    **({"bpm": params["bpm"]} if params.get("bpm") is not None else {}),
                }
            elif operation.type == "hook.f1.sound.v1":
                config["f1"] = {
                    "drop_time": params.get("drop_time"),
                    "seed": params.get("seed"),
                }
            elif operation.type == "hook.f2.object.v1":
                config["f2"] = {
                    "shape": params.get("shape"),
                    "drop_time": params.get("drop_time"),
                    "seed": params.get("seed"),
                }
            elif operation.type == "hook.f3.effect.v1":
                config["f3"] = {
                    "hook": params.get("hook"),
                    "transition": params.get("transition"),
                    "extra": params.get("extra"),
                    "extra_full": bool(params.get("extra_full")),
                    "hook_extend": params.get("hook_extend"),
                    "drop_time": params.get("drop_time"),
                    "assets": dict(params.get("assets") or {}),
                }
            elif operation.type == "hook.f4.motion.v1":
                config["f4"] = {
                    "device": params.get("device"),
                    "bpm": params.get("bpm"),
                    "drop_time": params.get("drop_time"),
                }
            elif operation.type == "hook.f5.cognition.v1":
                config["f5"] = {
                    "drop_rel_sec": params.get("drop_time"),
                    "combo_seed": params.get("seed"),
                }
        return config

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
    style_registry = _style_registry(subtitles_mode, full_edit_config, visual_ops)
    effect_registry = _effect_registry(footage_layers, text_layers, visual_ops)
    return RenderPlanV1(
        projectSpec=project_spec,
        compsSpec=comps,
        footage_layers=footage_layers,
        text_layers=text_layers,
        visualOps=visual_ops,
        f3_media=f3_media,
        requirements=_requirements(footage_layers, text_layers, visual_ops, f3_media),
        styleRegistry=style_registry,
        effectRegistry=effect_registry,
        goldenRefs=_golden_refs(subtitles_mode, full_edit_config),
        tuningSpec=_native_tuning_spec(full_edit_config),
    )


def _native_tuning_spec(full_edit_config: Dict[str, Any]) -> NativeTuningSpecV1:
    configured = full_edit_config.get("native_tuning")
    if configured is None:
        return NativeTuningSpecV1()
    if not isinstance(configured, dict):
        raise ValueError("native_tuning must be an object")
    return NativeTuningSpecV1.model_validate(configured)


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
        _semantic_style_operation(full_edit_config),
        _f3_operation(full_edit_config, f3_media),
        _f2_operation(full_edit_config),
        _f4_operation(full_edit_config),
        _f1_operation(full_edit_config),
        _f5_operation(full_edit_config),
    ):
        if op is not None:
            ops.append(op)
    return ops


def _requirements(
    footage_layers: List[Dict[str, Any]],
    text_layers: List[Dict[str, Any]],
    visual_ops: List[VisualOperationV1],
    f3_media: List[Dict[str, str]],
) -> Dict[str, Any]:
    fonts = sorted(
        {
            str(layer.get("text_data", {}).get("text_base", {}).get("font") or "").strip()
            for layer in text_layers
            if isinstance(layer.get("text_data"), dict)
        }
        - {""}
    )
    required_asset_roles = sorted(
        {
            asset.role
            for op in visual_ops
            for asset in op.assets
            if not asset.optional
        }
    )
    layer_types = sorted(
        {
            str(layer.get("type") or "").strip()
            for layer in [*footage_layers, *text_layers]
            if str(layer.get("type") or "").strip()
        }
    )
    plugins = sorted(
        {
            plugin
            for layer in [*footage_layers, *text_layers]
            for plugin in _plugins_for_layer(layer)
        }
    )
    return {
        "fonts": fonts,
        "asset_roles": required_asset_roles,
        "layer_types": layer_types,
        "plugins": plugins,
        "f3_media_count": len(f3_media),
        "audio_required": "audio" in required_asset_roles or "tts_audio" in required_asset_roles,
        "external_plugins_policy": "unsupported_without_ofx_or_sidecar",
    }


def _style_registry(
    subtitles_mode: str,
    full_edit_config: Dict[str, Any],
    visual_ops: List[VisualOperationV1],
) -> List[StyleRegistryEntryV1]:
    style_ids = []
    semantic = _dict(full_edit_config.get("semantic_style"))
    if _clean(semantic.get("style_id")):
        style_ids.append(_clean(semantic.get("style_id")))
    for op in visual_ops:
        if op.type.startswith("subtitle."):
            style_ids.append(op.type)
        if op.type.startswith("hook."):
            style_ids.append(op.type)
    if subtitles_mode:
        style_ids.append(f"subtitles_mode:{subtitles_mode}")
    out = []
    for style_id in sorted(set(style_ids)):
        effect_graph = _semantic_style_effect_graph(style_id)
        out.append(StyleRegistryEntryV1(
            styleId=style_id,
            effectGraph=effect_graph,
            tunables={
                f"{effect.match_name}.{name}": value
                for effect in effect_graph
                for name, value in effect.params.items()
            },
            requirements={
                "fonts": _style_fonts(style_id),
                "assets": [],
                "plugins": [],
            },
            goldenFixtures=_style_golden_fixtures(style_id),
        ))
    return out


def _effect_registry(
    footage_layers: List[Dict[str, Any]],
    text_layers: List[Dict[str, Any]],
    visual_ops: List[VisualOperationV1],
) -> List[EffectRegistryEntryV1]:
    match_names = {
        _normalize_effect_match_name(effect_name)
        for layer in [*footage_layers, *text_layers]
        for effect_name in _dict(layer.get("effects")).keys()
    }
    match_names.update(_native_effects_for_visual_op(op) for op in visual_ops)
    for op in visual_ops:
        if op.type == "style.semantic.v1":
            style_id = _clean(op.params.get("styleId") or op.params.get("style_id"))
            match_names.update(effect.match_name for effect in _semantic_style_effect_graph(style_id))
    out = []
    for match_name in sorted(name for name in match_names if name):
        out.append(EffectRegistryEntryV1(
            stableId=_stable_effect_id(match_name),
            aeMatchName=match_name,
            backend=_effect_backend(match_name),
            pluginIdentifier=_effect_plugin_identifier(match_name),
            parameterSchema=_effect_parameter_schema(match_name),
            deterministicSeedPolicy=(
                "required_from_render_plan"
                if match_name in {"ANR Analog Glitch", "ANR F3 Stylize", "ANR Shape Overlay"}
                else "not_applicable"
            ),
        ))
    return out


def _semantic_style_operation(cfg: Dict[str, Any]) -> Optional[VisualOperationV1]:
    semantic = _dict(cfg.get("semantic_style"))
    style_id = _clean(semantic.get("style_id") or semantic.get("styleId"))
    if not style_id:
        return None
    return VisualOperationV1(
        id=f"semantic_style_{style_id}",
        kind="style.semantic.v1",
        params={"styleId": style_id, "version": _clean(semantic.get("version")) or "v1"},
    )


def _semantic_style_effect_graph(style_id: str) -> List[RegistryEffectV1]:
    recipes: Dict[str, List[Dict[str, Any]]] = {
        "ftg_al16_default_v1": [
            {"matchName": "ADBE Gaussian Blur 2", "params": {"blurriness": 3.0}},
            {"matchName": "ADBE Geometry2", "params": {"rotation": 0.65, "scale_width": 103.0, "scale_height": 103.0}},
            {"matchName": "ADBE Motion Blur", "params": {"direction": 0.0, "blur_length": 8.0}},
        ],
        "txt_soft_v1": [
            {"matchName": "ADBE Glo2", "params": {"threshold": 120.0, "radius": 28.0, "intensity": 0.55, "operation": "screen"}},
            {"matchName": "ADBE Geometry2", "params": {"rotation": 0.15, "scale_width": 101.0, "scale_height": 101.0}},
        ],
        "txt_punch_v1": [
            {"matchName": "ADBE Geometry2", "params": {"scale_width": 112.0, "scale_height": 88.0}},
            {"matchName": "ADBE Motion Blur", "params": {"direction": 0.0, "blur_length": 32.0}},
        ],
        "txt_drop_v1": [
            {"matchName": "ADBE Glo2", "params": {"threshold": 105.0, "radius": 38.0, "intensity": 0.8, "operation": "add"}},
            {"matchName": "ADBE Geometry2", "params": {"scale_width": 118.0, "scale_height": 82.0}},
            {"matchName": "ADBE Motion Blur", "params": {"direction": 90.0, "blur_length": 46.0}},
        ],
    }
    return [RegistryEffectV1.model_validate(effect) for effect in recipes.get(style_id, [])]


def _style_fonts(style_id: str) -> List[str]:
    if style_id.startswith("txt_") or style_id.startswith("subtitles_mode:"):
        return ["Montserrat"]
    return []


def _style_golden_fixtures(style_id: str) -> List[str]:
    return {
        "txt_soft_v1": ["trendy_5th_real_job"],
        "txt_punch_v1": ["brat_5th_real_job"],
        "txt_drop_v1": ["brat_5th_real_job"],
    }.get(style_id, [])


def _effect_parameter_schema(match_name: str) -> Dict[str, EffectParameterSpecV1]:
    schemas: Dict[str, Dict[str, Dict[str, Any]]] = {
        "ADBE Gaussian Blur 2": {
            "blurriness": {"type": "number", "default": 0.0, "min": 0.0},
            "repeat_edge_pixels": {"type": "boolean", "default": False},
        },
        "ADBE Geometry2": {
            "anchor": {"type": "vec2", "default": [0.0, 0.0]},
            "position": {"type": "vec2", "default": [0.0, 0.0]},
            "scale_width": {"type": "number", "default": 100.0, "min": 0.0},
            "scale_height": {"type": "number", "default": 100.0, "min": 0.0},
            "rotation": {"type": "number", "default": 0.0},
            "opacity": {"type": "number", "default": 100.0, "min": 0.0, "max": 100.0},
        },
        "ADBE Motion Blur": {
            "direction": {"type": "number", "default": 0.0},
            "blur_length": {"type": "number", "default": 0.0, "min": 0.0},
        },
        "ADBE Glo2": {
            "threshold": {"type": "number", "default": 60.0, "min": 0.0, "max": 255.0},
            "radius": {"type": "number", "default": 10.0, "min": 0.0},
            "intensity": {"type": "number", "default": 1.0, "min": 0.0},
            "operation": {"type": "enum", "default": "add", "keyframe": False},
        },
        "ADBE Drop Shadow": {
            "color": {"type": "color", "default": [0.0, 0.0, 0.0, 1.0]},
            "opacity": {"type": "number", "default": 100.0, "min": 0.0},
            "direction": {"type": "number", "default": 135.0},
            "distance": {"type": "number", "default": 5.0},
            "softness": {"type": "number", "default": 0.0, "min": 0.0},
        },
        "ADBE Minimax": {
            "operation": {"type": "enum", "default": "maximum", "keyframe": False},
            "channels": {"type": "enum", "default": "alpha", "keyframe": False},
            "direction": {"type": "enum", "default": "horizontal_and_vertical", "keyframe": False},
            "radius": {"type": "number", "default": 0.0, "min": 0.0},
        },
        "ANR Analog Glitch": {
            "contrast": {"type": "number", "default": 1.0, "min": 0.0},
            "red_gain": {"type": "number", "default": 1.0, "min": 0.0},
            "wave_amplitude": {"type": "number", "default": 0.0},
            "wave_width": {"type": "number", "default": 1.0, "min": 0.0},
            "glow_radius": {"type": "number", "default": 0.0, "min": 0.0},
        },
        "ANR F3 Stylize": {
            "mode": {"type": "enum", "default": "extract", "keyframe": False},
            "amount": {"type": "number", "default": 1.0, "min": 0.0},
            "threshold": {"type": "number", "default": 0.5, "min": 0.0, "max": 1.0},
        },
        "ANR Shape Overlay": {
            "shape": {"type": "enum", "default": "ellipse", "keyframe": False},
            "opacity": {"type": "number", "default": 1.0, "min": 0.0, "max": 1.0},
            "size": {"type": "number", "default": 100.0, "min": 0.0},
            "seed": {"type": "integer", "default": 0, "keyframe": False},
        },
        "ANR Vertical Gradient": {
            "top": {"type": "color", "default": [1.0, 1.0, 1.0, 1.0]},
            "bottom": {"type": "color", "default": [1.0, 1.0, 1.0, 1.0]},
            "brightness": {"type": "number", "default": 1.0, "min": 0.0},
            "start_xy": {"type": "vec2", "default": [0.5, 0.0]},
            "end_xy": {"type": "vec2", "default": [0.5, 1.0]},
        },
    }
    return {
        name: EffectParameterSpecV1.model_validate(spec)
        for name, spec in schemas.get(match_name, {}).items()
    }


def _effect_plugin_identifier(match_name: str) -> Optional[str]:
    if match_name.startswith("S_"):
        return f"sapphire:{match_name}"
    if match_name.startswith("BCC ") or match_name.startswith("BCC6"):
        return f"boris-bcc:{match_name}"
    if match_name.startswith("VISINF "):
        return f"visinf:{match_name}"
    return None


def _golden_refs(subtitles_mode: str, full_edit_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs = []
    if subtitles_mode == SUBTITLES_MODE_TRENDY_5TH:
        refs.append(
            {
                "id": "trendy_5th_real_job",
                "artifactJobId": "9ef2717145c04318927ca738f5882541",
                "family": "Trendy",
                "status": "reference_required",
            }
        )
    if subtitles_mode == SUBTITLES_MODE_BRAT_5TH:
        refs.append(
            {
                "id": "brat_5th_real_job",
                "artifactJobId": "a15d4c02d67843b787402bb27aeb5830",
                "family": "Brat",
                "status": "reference_required",
            }
        )
    if _clean(full_edit_config.get("job_id")):
        refs.append({"id": "source_job", "jobId": _clean(full_edit_config.get("job_id"))})
    return refs


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
        "assets": dict(_dict(f3.get("assets"))),
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
    word_timings = _list(f5.get("word_timings")) or _list(f5.get("words"))
    if word_timings:
        params["word_timings"] = word_timings
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


def _plugins_for_layer(layer: Dict[str, Any]) -> List[str]:
    plugins = []
    for effect_name in _dict(layer.get("effects")).keys():
        match_name = _normalize_effect_match_name(effect_name)
        if match_name in _NATIVE_PROPRIETARY_APPROXIMATIONS:
            continue
        if match_name.startswith("S_"):
            plugins.append("sapphire")
        elif match_name.startswith(("BCC ", "BCC6")):
            plugins.append("boris_bcc")
        elif match_name.startswith("VISINF "):
            plugins.append("visinf")
    return plugins


def _native_effects_for_visual_op(op: VisualOperationV1) -> str:
    mapped = {
        "subtitle.trendy.v1": "ANR Subtitle Trendy",
        "subtitle.brat.v1": "ANR Subtitle Brat",
        "hook.f1.sound.v1": "ANR Hook F1 Sound",
        "hook.f2.object.v1": "ANR Shape Overlay",
        "hook.f3.effect.v1": "ANR F3 Stylize",
        "hook.f4.motion.v1": "ANR Hook F4 Motion",
        "hook.f5.cognition.v1": "ANR Hook F5 Cognition",
    }.get(op.type, "")
    if mapped:
        return mapped
    if op.type.startswith("subtitle.bot."):
        return "ANR Subtitle Bot"
    return ""


def _normalize_effect_match_name(effect_name: str) -> str:
    return str(effect_name or "").split(":", 1)[-1].strip()


def _stable_effect_id(match_name: str) -> str:
    return (
        str(match_name)
        .lower()
        .replace(" ", ".")
        .replace("_", ".")
        .replace("-", ".")
    )


def _effect_backend(match_name: str) -> str:
    if match_name in _NATIVE_PROPRIETARY_APPROXIMATIONS:
        return "native_approximation"
    if match_name.startswith(("S_", "BCC ", "BCC6", "VISINF ")):
        return "unsupported_external_plugin"
    return "native_approximation"


_NATIVE_PROPRIETARY_APPROXIMATIONS = {
    "S_BlurMotion",
    "S_DropShadow",
    "S_Gradient",
    "S_Glow",
    "S_GlowEdges",
    "BCC6LensBlur",
    "BCC Lens Blur",
    "VISINF Grain Implant",
}


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
