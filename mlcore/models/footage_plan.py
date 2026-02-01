# mlcore/models/footage_plan.py
from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field, model_validator


FitMode = Literal["cover", "contain", "stretch"]


class FootageAsset(BaseModel):
    file_name: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    src_w: int = Field(ge=1)
    src_h: int = Field(ge=1)


class FootageClipPick(BaseModel):
    file_name: str = Field(min_length=1)
    fit_mode: FitMode = "cover"
    in_point: float = Field(ge=0.0)
    out_point: float = Field(ge=0.0)
    start_time: float

    @model_validator(mode="after")
    def _check(self) -> "FootageClipPick":
        if self.out_point <= self.in_point:
            raise ValueError("out_point must be > in_point")
        if abs(float(self.start_time) - float(self.in_point)) > 1e-6:
            raise ValueError("start_time must equal in_point exactly")
        return self


class FootageSelectionPayload(BaseModel):
    clips: List[FootageClipPick] = Field(min_length=1)
    allow_gaps: bool = False
