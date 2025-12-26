# render_v1/models.py
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union, Literal

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
    scale: Optional[List[float]] = None
    position: Optional[List[float]] = None
    rotation: Optional[float] = None
    opacity: Optional[float] = None


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

    # MVP markers (input): model may set combo + overrides
    textFxComboId: Optional[str] = None
    textFxOverrides: Optional[dict] = None

    # Baked output (after assembler): exact animator config for JSX
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
