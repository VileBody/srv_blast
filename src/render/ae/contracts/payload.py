# src/render/ae/contracts/payload.py
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel
from pydantic.config import ConfigDict


# --- Enums ---

class FitPolicy(str, Enum):
    """
    Политика вписывания футажа в композицию.

    COVER   — масштабировать так, чтобы меньшая сторона кадра была равна
              соответствующей стороне композиции (обрезка по большей стороне).
    CONTAIN — вписать целиком, оставляя поля; пока не используем, но оставляем для будущего.
    """
    COVER = "cover"
    CONTAIN = "contain"


# --- Sub-components ---


class Keyframe(BaseModel):
    """
    AE keyframe payload is verbose. We only require `time` and `value`.
    Everything else (eases, interpolation, labels) is allowed but not enforced.
    """

    model_config = ConfigDict(extra="allow")

    time: float
    value: Any


class ValueData(BaseModel):
    """
    Generic AE "value data" carrier:
      - constant value: { value: ... }
      - expression: { expressionEnabled: true, expression: "...", value: ... }
      - keyframed: { expressionEnabled: false, keys: [ {time, value, ...}, ... ] }
    """

    model_config = ConfigDict(extra="allow")

    expressionEnabled: Optional[bool] = None
    expression: Optional[str] = None
    value: Optional[Any] = None
    keys: Optional[List[Keyframe]] = None


ScalarOrValueData = Union[float, int, ValueData]
VecOrValueData = Union[List[float], ValueData]


class Transform(BaseModel):
    # Allow animated transforms (ValueData) in addition to plain numbers.
    scale: Optional[VecOrValueData] = None
    position: Optional[VecOrValueData] = None
    rotation: Optional[ScalarOrValueData] = None
    opacity: Optional[ScalarOrValueData] = None


class TextDocument(BaseModel):
    text: str
    font: Optional[str] = "Arial-BoldMT"
    fontSize: Union[float, int, ValueData] = 50
    applyFill: Optional[bool] = None
    fillColor: Optional[Union[List[float], ValueData]] = None
    applyStroke: Optional[bool] = None
    strokeColor: Optional[Union[List[float], ValueData]] = None
    strokeWidth: Optional[Union[float, int, ValueData]] = None
    tracking: Optional[Union[float, int, ValueData]] = None
    leading: Optional[Union[float, int, ValueData]] = None
    justification: Optional[Union[int, ValueData]] = None


# --- Layers ---

class BaseLayer(BaseModel):
    name: Optional[str] = None
    startTime: float
    inPoint: float
    outPoint: float
    enabled: bool = True
    audioEnabled: Optional[bool] = None
    transform: Optional[Transform] = None
    threeDLayer: Optional[bool] = None


class RefLayer(BaseLayer):
    type: Literal["ref"]
    refId: str
    # cover/contain — обрабатывается в движке job_template.jsx
    fitPolicy: Optional[FitPolicy] = None


class TextLayer(BaseLayer):
    type: Literal["text"]
    textDocument: TextDocument

    # MVP markers (input): model may set combo + overrides
    textFxComboId: Optional[str] = None
    textFxOverrides: Optional[Dict[str, Any]] = None

    # Baked output (after assembler): exact animator/effect config for JSX
    textAnimators: Optional[List[Dict[str, Any]]] = None
    effects: Optional[List[Dict[str, Any]]] = None


class AdjustmentLayer(BaseLayer):
    type: Literal["adjustment"]
    effects: Optional[List[dict]] = None


LayerType = Union[RefLayer, TextLayer, AdjustmentLayer]


# --- Items ---

class FootageItem(BaseModel):
    id: str
    type: Literal["footage"]
    name: str
    path: str
    isRef: bool = False


class CompItem(BaseModel):
    id: str
    type: Literal["comp"]
    name: str
    width: int
    height: int
    duration: float
    fps: float
    pixelAspect: float
    layers: List[LayerType] = []


FolderItem = FootageItem
ItemType = Union[FootageItem, CompItem]


# --- Root ---

class ProjectStructure(BaseModel):
    projectName: str
    items: List[ItemType]


class Payload(BaseModel):
    project: ProjectStructure
    entryPoint: Optional[str] = None
