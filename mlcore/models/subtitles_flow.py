from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_SCENES_3RD,
    SubtitlesMode,
)
from .subtitles_tokens import ClipWindow


class SubtitleFlowToken(BaseModel):
    text: str = Field(min_length=1)
    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_time(self) -> "SubtitleFlowToken":
        if self.t_end <= self.t_start:
            raise ValueError(f"token.t_end must be > token.t_start (got {self.t_start}..{self.t_end})")
        return self


class SubtitleFlowSegment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    segment_id: str = Field(min_length=1, alias="id")
    text: str = Field(min_length=1)
    in_point: float = Field(ge=0.0)
    out_point: float = Field(ge=0.0)
    style_tag: str = Field(min_length=1)
    lines: List[str] = Field(default_factory=list)
    tokens: List[SubtitleFlowToken] = Field(default_factory=list)
    focus_word: Optional[str] = None
    focus_style: Optional[str] = None

    @model_validator(mode="after")
    def _check_timing(self) -> "SubtitleFlowSegment":
        if self.out_point <= self.in_point:
            raise ValueError(
                f"segment.out_point must be > segment.in_point (id={self.segment_id!r}, "
                f"{self.in_point}..{self.out_point})"
            )
        if not self.lines:
            self.lines = [self.text]
        return self


class SubtitleFlowPlan(BaseModel):
    mode: SubtitlesMode
    clip: ClipWindow
    segments: List[SubtitleFlowSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_contract(self) -> "SubtitleFlowPlan":
        cs = float(self.clip.start)
        ce = float(self.clip.end)
        prev_in = -1.0
        prev_out = -1.0
        for idx, seg in enumerate(self.segments):
            if seg.in_point < cs - 1e-6 or seg.out_point > ce + 1e-6:
                raise ValueError(
                    f"segment out of clip window (id={seg.segment_id!r}, "
                    f"{seg.in_point}..{seg.out_point} not in {cs}..{ce})"
                )
            if idx > 0 and seg.in_point < prev_in - 1e-6:
                raise ValueError(
                    f"segments must be monotonic by in_point (idx={idx}, {seg.in_point} < {prev_in})"
                )
            if idx > 0 and seg.in_point < prev_out - 1e-6:
                raise ValueError(
                    f"segments critically overlap (idx={idx}, {seg.in_point} < prev_out={prev_out})"
                )
            prev_in = float(seg.in_point)
            prev_out = float(seg.out_point)
            for t in seg.tokens:
                if t.t_start < cs - 1e-6 or t.t_end > ce + 1e-6:
                    raise ValueError(
                        f"token out of clip window (segment={seg.segment_id!r}, token={t.text!r}, "
                        f"{t.t_start}..{t.t_end} not in {cs}..{ce})"
                    )
        return self


class ImpulseWordTiming(BaseModel):
    word: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_time(self) -> "ImpulseWordTiming":
        if self.end <= self.start:
            raise ValueError(f"word_timing.end must be > start (got {self.start}..{self.end})")
        return self


class Impulse2ndSegmentPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1)
    in_point: float = Field(alias="in", ge=0.0)
    out_point: float = Field(alias="out", ge=0.0)
    type: Literal["long", "short"]
    word_timings: List[ImpulseWordTiming] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_time(self) -> "Impulse2ndSegmentPayload":
        if self.out_point <= self.in_point:
            raise ValueError(f"segment.out must be > segment.in (got {self.in_point}..{self.out_point})")
        return self


class Impulse2ndPayload(BaseModel):
    clip: ClipWindow
    segments: List[Impulse2ndSegmentPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_contract(self) -> "Impulse2ndPayload":
        cs = float(self.clip.start)
        ce = float(self.clip.end)
        for seg in self.segments:
            if seg.in_point < cs - 1e-6 or seg.out_point > ce + 1e-6:
                raise ValueError(
                    f"segment out of clip (text={seg.text!r}, {seg.in_point}..{seg.out_point} not in {cs}..{ce})"
                )
        return self

    @property
    def mode(self) -> str:
        return SUBTITLES_MODE_IMPULSE_2ND


class SceneWordTimingPayload(BaseModel):
    word: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_time(self) -> "SceneWordTimingPayload":
        if self.end <= self.start:
            raise ValueError(f"word_timing.end must be > start (got {self.start}..{self.end})")
        return self


class Scene3rdPayloadScene(BaseModel):
    id: int = Field(ge=1)
    type: Literal["TYPE_1", "TYPE_2", "TYPE_3", "TYPE_4", "TYPE_5", "TYPE_6"]
    words: List[str] = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    lines: List[List[str]] = Field(default_factory=list)
    focus_word: Optional[str] = None
    focus_style: Optional[Literal["italic", "red"]] = None
    word_timings: List[SceneWordTimingPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scene(self) -> "Scene3rdPayloadScene":
        if self.end <= self.start:
            raise ValueError(f"scene.end must be > scene.start (id={self.id}, {self.start}..{self.end})")
        if not self.lines:
            self.lines = [list(self.words)]
        return self


class Scenes3rdPayload(BaseModel):
    clip: ClipWindow
    scenes: List[Scene3rdPayloadScene] = Field(min_length=1)

    @property
    def mode(self) -> str:
        return SUBTITLES_MODE_SCENES_3RD
