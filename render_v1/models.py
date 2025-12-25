# render_v1/models.py
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union, Literal

# Generic value payload for AE properties.
# It may be a raw scalar/array, or an object like {"value":..., "keys":[...]} for keyframes.
ValueData = Union[float, int, List[float], List[int], Dict[str, Any]]

from pydantic import BaseModel


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

class Transform(BaseModel):
    scale: Optional[ValueData] = None
    position: Optional[ValueData] = None
    rotation: Optional[ValueData] = None
    opacity: Optional[ValueData] = None


class TextDocument(BaseModel):
    text: str
    font: Optional[str] = "Arial-BoldMT"
    fontSize: float = 50
    applyFill: Optional[bool] = None
    fillColor: Optional[List[float]] = None
    applyStroke: Optional[bool] = None
    strokeColor: Optional[List[float]] = None
    strokeWidth: Optional[float] = None
    tracking: Optional[float] = None
    leading: Optional[float] = None
    justification: Optional[int] = None


# --- Layers ---

class BaseLayer(BaseModel):
    name: Optional[str] = None
    startTime: float
    inPoint: float
    outPoint: float
    enabled: bool = True
    audioEnabled: Optional[bool] = None
    transform: Optional[Transform] = None


class RefLayer(BaseLayer):
    type: Literal["ref"]
    refId: str
    # cover/contain — обрабатывается в движке job_template.jsx
    fitPolicy: Optional[FitPolicy] = None


class TextLayer(BaseLayer):
    type: Literal["text"]
    textDocument: TextDocument
    # Optional effect stack directly on the text layer (Effect Parade)
    effects: Optional[List[dict]] = None
    # Text animators (Animator + selectors) applied in job_template.jsx
    textAnimators: Optional[List[dict]] = None


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


ItemType = Union[FootageItem, CompItem]


# --- Root ---

class ProjectStructure(BaseModel):
    projectName: str
    items: List[ItemType]


class Payload(BaseModel):
    project: ProjectStructure
    entryPoint: Optional[str] = None
