# src/ae_plan_models.py
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator

from src.config.styles import SubtitleStyle


class SubtitleLine(BaseModel):
    index: int
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    text: str
    style: SubtitleStyle = SubtitleStyle.DEFAULT

    @field_validator("end_sec")
    @classmethod
    def check_time_order(cls, v: float, info):
        start = info.data.get("start_sec")
        if start is not None and v <= start:
            raise ValueError("end_sec must be greater than start_sec")
        return v


class VisualShot(BaseModel):
    index: int
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    asset_prefix: str
    style_id: str | None = None

    @field_validator("end_sec")
    @classmethod
    def check_time_order(cls, v: float, info):
        start = info.data.get("start_sec")
        if start is not None and v <= start:
            raise ValueError("end_sec must be greater than start_sec")
        return v


class AeSegment(BaseModel):
    index: int
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    mood: str = ""
    description: str = ""
    shots: List[VisualShot]

    @field_validator("end_sec")
    @classmethod
    def check_time_order(cls, v: float, info):
        start = info.data.get("start_sec")
        if start is not None and v <= start:
            raise ValueError("end_sec must be greater than start_sec")
        return v


class AeEditPlan(BaseModel):
    """
    То, что возвращает Gemini одним проходом:
      - общая длительность ролика,
      - сегменты с shot’ами,
      - субтитры со стилем (default / highlight).
    Это потом превращается в PROJECT_DATA для engine_template.jsx.
    """

    total_duration_sec: float = Field(gt=0)
    segments: List[AeSegment]
    subtitles: List[SubtitleLine]

    @field_validator("segments")
    @classmethod
    def check_segments_non_empty(cls, segments: List[AeSegment]):
        if not segments:
            raise ValueError("segments must be a non-empty list")
        return segments

    @field_validator("subtitles")
    @classmethod
    def check_subtitles_non_empty(cls, subtitles: List[SubtitleLine]):
        if not subtitles:
            raise ValueError("subtitles must be a non-empty list")
        return subtitles
