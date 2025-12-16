from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

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


OverrideValue = Union[
    int,
    float,
    str,
    bool,
    List[Any],
    Dict[str, Any],
    KeyframedValue,
]


class BaseLayer(BaseModel):
    model_config = ConfigDict(extra="allow")

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

    @model_validator(mode="after")
    def _validate_required_overrides(self) -> "TextLayer":
        if self.animId == TextAnimPresetId.REVEAL_OPACITY:
            v = self.overrides.get("selector_start")
            if not isinstance(v, dict) or "keys" not in v:
                raise ValueError(
                    "anim_reveal_opacity requires overrides.selector_start = {keys:[...]}"
                )
            keys = v.get("keys") or []
            if not isinstance(keys, list) or len(keys) < 2:
                raise ValueError("selector_start must have at least 2 keyframes")
            for k in keys:
                if not isinstance(k, dict) or "time" not in k or "value" not in k:
                    raise ValueError("selector_start keyframes must include time and value")
                tr = k.get("templateRef")
                if tr is not None and not str(tr).startswith("tpl_"):
                    raise ValueError("templateRef must be 'tpl_*' from text_motion_library.json")
        return self


class AdjustmentLayer(BaseLayer):
    type: Literal["adjustment"] = "adjustment"


Layer = Union[RefLayer, TextLayer, AdjustmentLayer]


class FootageItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: Literal["footage"] = "footage"
    path: str
    name: Optional[str] = None
    isRef: Optional[bool] = None


class CompItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: Literal["comp"] = "comp"
    name: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    pixelAspect: Optional[float] = None
    duration: Optional[float] = None
    layers: List[Layer] = Field(default_factory=list)


Item = Union[FootageItem, CompItem]


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    defaults: Optional[Dict[str, Any]] = None
    global_start_sec: Optional[float] = None
    global_end_sec: Optional[float] = None
    fitPolicy: Optional[str] = None
    audioRefId: Optional[str] = None
    stylePack: Optional[str] = None


class AeComposition(BaseModel):
    model_config = ConfigDict(extra="allow")

    projectSettings: Optional[ProjectSettings] = None
    entryPoint: Optional[str] = None
    items: List[Item] = Field(default_factory=list)
