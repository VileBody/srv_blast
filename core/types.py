from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class KeyframeEase:
    speed: float
    influence: float


@dataclass
class KeyframeData:
    t: float
    v: Any
    iit: str = "6613"  # 6613 Bezier, 6612 Linear, 6614 Hold
    oit: str = "6613"
    ease_in: List[KeyframeEase] = field(default_factory=list)
    ease_out: List[KeyframeEase] = field(default_factory=list)


@dataclass
class PropertyData:
    match_name: str
    value: Any = None
    keyframes: List[KeyframeData] = field(default_factory=list)
    expression: Optional[str] = None


@dataclass
class StyleInstruction:
    start: int
    end: int
    font: str
    size: float
    italic: bool = False



@dataclass
class LayerBlueprint:
    name: str
    type: str
    in_point: float
    out_point: float
    z_index: int

    text: str = ""
    adjustment_layer: bool = False
    comp_id: Optional[int] = None
    comp_name: Optional[str] = None  # ✅ NEW (для precomp/video by-name)

    source_rect: Dict[str, float] = field(default_factory=dict)
    props: Dict[str, PropertyData] = field(default_factory=dict)
    effects: Dict[str, Dict[str, PropertyData]] = field(default_factory=dict)
    style_instructions: List[StyleInstruction] = field(default_factory=list)
    text_data: Dict[str, Any] = field(default_factory=dict)
