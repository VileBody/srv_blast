# render_v1/models.py
from __future__ import annotations

from typing import List, Optional, Union, Literal

from pydantic import BaseModel

from src.core.config.styles import FootagePresetId


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
    presetId: Optional[FootagePresetId] = None


class TextLayer(BaseLayer):
    type: Literal["text"]
    textDocument: TextDocument


class AdjustmentLayer(BaseLayer):
    type: Literal["adjustment"]


LayerType = Union[RefLayer, TextLayer, AdjustmentLayer]


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


class ProjectStructure(BaseModel):
    projectName: str
    items: List[ItemType]


class Payload(BaseModel):
    project: ProjectStructure
    entryPoint: Optional[str] = None
