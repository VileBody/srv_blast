from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union, Literal

from pydantic import BaseModel, Field, ConfigDict


# -----------------------------
# Keyframe / property primitives
# -----------------------------

class TemporalEase(BaseModel):
    speed: float
    influence: float


class KeyTemplate(BaseModel):
    """Reusable template applied to a keyframe via templateRef."""

    description: Optional[str] = None

    inInterpolationType: Optional[int] = None
    outInterpolationType: Optional[int] = None

    inTemporalEase: Optional[List[TemporalEase]] = None
    outTemporalEase: Optional[List[TemporalEase]] = None

    temporalAutoBezier: Optional[bool] = None
    temporalContinuous: Optional[bool] = None


class Keyframe(BaseModel):
    time: float
    value: Any

    # Template reference (preferred)
    templateRef: Optional[str] = None

    # Optional per-key overrides (override template values)
    inInterpolationType: Optional[int] = None
    outInterpolationType: Optional[int] = None
    inTemporalEase: Optional[List[TemporalEase]] = None
    outTemporalEase: Optional[List[TemporalEase]] = None
    temporalAutoBezier: Optional[bool] = None
    temporalContinuous: Optional[bool] = None


class KeyframedValue(BaseModel):
    keys: List[Keyframe]


# Note: We deliberately keep PropertyValue permissive (AE has a zoo of value types).
PropertyValue = Union[
    int,
    float,
    str,
    bool,
    List[Any],
    Dict[str, Any],
    KeyframedValue,
]


# -----------------------------
# Generic AE property-tree node
# -----------------------------

class PropertyTreeNode(BaseModel):
    """Tree that mirrors AE's matchName hierarchy.

    This is used for:
      - text animators (ADBE Text Animators)
      - transform group (ADBE Transform Group)
      - effects (ADBE Effect Parade) if you decide to add later
    """

    matchName: str
    name: Optional[str] = None

    # key: child property matchName, value: scalar / {keys:[...]} / etc.
    properties: Optional[Dict[str, Any]] = None

    children: Optional[List["PropertyTreeNode"]] = None


# -----------------------------
# Layer / Item models
# -----------------------------

class ItemType(str, Enum):
    FOOTAGE = "footage"
    COMP = "comp"


class LayerType(str, Enum):
    REF = "ref"
    TEXT = "text"
    ADJUSTMENT = "adjustment"


class FitPolicy(str, Enum):
    COVER = "cover"
    CONTAIN = "contain"
    STRETCH = "stretch"


class BaseLayer(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: LayerType
    name: Optional[str] = None
    inPoint: Optional[float] = None
    outPoint: Optional[float] = None
    startTime: Optional[float] = None
    enabled: Optional[bool] = None
    audioEnabled: Optional[bool] = None

    # Legacy (simple transform dict used by job_template.jsx)
    transform: Optional[Dict[str, Any]] = None

    # New: matchName-based tree (full fidelity)
    transformTree: Optional[PropertyTreeNode] = None


class RefLayer(BaseLayer):
    type: Literal[LayerType.REF] = LayerType.REF
    refId: str
    fitPolicy: Optional[FitPolicy] = None
    presetId: Optional[str] = None


class TextLayer(BaseLayer):
    type: Literal[LayerType.TEXT] = LayerType.TEXT
    styleId: Optional[str] = None  # keep for trace/debug
    content: Optional[str] = None  # keep for trace/debug

    # Motion preset references (resolved in assembler)
    animId: Optional[str] = None
    transformId: Optional[str] = None
    overrides: Optional[Dict[str, Any]] = None

    # TextDocument settings are applied via template engine.
    textDocument: Dict[str, Any]

    # New: serialized Text > ADBE Text Animators tree
    textAnimTree: Optional[PropertyTreeNode] = None


class AdjustmentLayer(BaseLayer):
    type: Literal[LayerType.ADJUSTMENT] = LayerType.ADJUSTMENT

    # Optional: future-proof if you decide to dump effects as property trees
    effects: Optional[List[PropertyTreeNode]] = None


Layer = Union[RefLayer, TextLayer, AdjustmentLayer]


class FootageItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: Literal[ItemType.FOOTAGE] = ItemType.FOOTAGE
    name: str
    path: str
    isRef: Optional[bool] = None


class CompItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: Literal[ItemType.COMP] = ItemType.COMP
    name: str

    width: int
    height: int
    duration: float
    fps: float

    pixelAspect: Optional[float] = 1.0
    layers: List[Layer] = Field(default_factory=list)

    bgColor: Optional[List[float]] = None


Item = Union[FootageItem, CompItem]


class Project(BaseModel):
    projectName: str
    items: List[Item]


class Libraries(BaseModel):
    # TemplateRef -> KeyTemplate
    keyTemplates: Dict[str, KeyTemplate] = Field(default_factory=dict)


class Payload(BaseModel):
    project: Project
    entryPoint: Optional[str] = None

    # Optional libraries injected for the JSX engine runtime
    libraries: Optional[Libraries] = None
