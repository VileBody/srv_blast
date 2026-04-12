from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    SubtitlesMode,
)
from .subtitles_tokens import ClipWindow


class SubtitleFlowToken(BaseModel):
    text: str = Field(min_length=1)
    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)
    focus: bool = False

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
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _check_timing(self) -> "SubtitleFlowSegment":
        if self.out_point <= self.in_point:
            raise ValueError(
                f"segment.out_point must be > segment.in_point (id={self.segment_id!r}, "
                f"{self.in_point}..{self.out_point})"
            )
        if not self.lines:
            self.lines = [self.text]
        if self.reason is not None:
            self.reason = str(self.reason).strip()
            if not self.reason:
                raise ValueError("segment.reason must be non-empty when provided")
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


class Impulse2ndRawWordTiming(BaseModel):
    word: str = Field(min_length=1)
    start: float
    end: float

    @model_validator(mode="after")
    def _check_time(self) -> "Impulse2ndRawWordTiming":
        if self.end <= self.start:
            raise ValueError(f"word_timing.end must be > start (got {self.start}..{self.end})")
        return self


class Impulse2ndRawSegmentPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1)
    in_point: float = Field(alias="in")
    out_point: float = Field(alias="out")
    type: Literal["long", "short"]
    # Why this segment was tagged as long/short (debugging aid for Stage2 decisions).
    reason: Optional[str] = Field(default=None, min_length=1)
    word_timings: List[Impulse2ndRawWordTiming] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_time(self) -> "Impulse2ndRawSegmentPayload":
        if self.out_point <= self.in_point:
            raise ValueError(f"segment.out must be > segment.in (got {self.in_point}..{self.out_point})")
        if self.reason is not None:
            self.reason = str(self.reason).strip()
            if not self.reason:
                raise ValueError("segment.reason must be non-empty when provided")
        return self


class Impulse2ndRawPayload(BaseModel):
    # Absolute full-track anchor for denormalizing normalized impulse timings.
    anchor_in_abs: float
    word_timings: List[Impulse2ndRawWordTiming] = Field(default_factory=list)
    segments: List[Impulse2ndRawSegmentPayload] = Field(min_length=1)

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
    reason: Optional[str] = None
    word_timings: List[SceneWordTimingPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scene(self) -> "Scene3rdPayloadScene":
        if self.end <= self.start:
            raise ValueError(f"scene.end must be > scene.start (id={self.id}, {self.start}..{self.end})")
        if not self.lines:
            self.lines = [list(self.words)]
        if self.reason is not None:
            self.reason = str(self.reason).strip()
            if not self.reason:
                raise ValueError("scene.reason must be non-empty when provided")
        return self


class Scenes3rdPayload(BaseModel):
    clip: ClipWindow
    scenes: List[Scene3rdPayloadScene] = Field(min_length=1)

    @property
    def mode(self) -> str:
        return SUBTITLES_MODE_SCENES_3RD


class Template4WordTimingPayload(BaseModel):
    word: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    focus: bool = Field(
        description="true for emotionally strong key words (at least 1 per every 2 subtitles). These words will be colored red."
    )

    @model_validator(mode="after")
    def _check_time(self) -> "Template4WordTimingPayload":
        if self.end <= self.start:
            raise ValueError(f"word_timing.end must be > start (got {self.start}..{self.end})")
        return self


class Template4SubtitlePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1)
    in_point: float = Field(alias="in", ge=0.0)
    out_point: float = Field(alias="out", ge=0.0)

    @model_validator(mode="after")
    def _check_time(self) -> "Template4SubtitlePayload":
        if self.out_point <= self.in_point:
            raise ValueError(f"subtitle.out must be > subtitle.in (got {self.in_point}..{self.out_point})")
        return self


class Template4Payload(BaseModel):
    word_timings: List[Template4WordTimingPayload] = Field(default_factory=list)
    subtitles: List[Template4SubtitlePayload] = Field(min_length=1)

    @property
    def mode(self) -> str:
        return SUBTITLES_MODE_TEMPLATE_4TH


class Scenes3rdSingleStepPayload(Scenes3rdPayload):
    @property
    def mode(self) -> str:
        return SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP
