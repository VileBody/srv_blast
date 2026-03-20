from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator

from .stage1_plan import FragmentAnalytics


FORCED_TIMECODE_PATTERN = r"^\d+:[0-5]\d\.\d{3}$"
_FORCED_TIMECODE_RE = re.compile(FORCED_TIMECODE_PATTERN)


def parse_forced_timecode_mmss_mmm(value: str) -> float:
    raw = str(value or "").strip()
    m = _FORCED_TIMECODE_RE.fullmatch(raw)
    if not m:
        raise ValueError(f"timecode must match mm:ss.mmm (got {value!r})")
    mins_str, rest = raw.split(":", 1)
    secs_str, ms_str = rest.split(".", 1)
    mins = int(mins_str)
    secs = int(secs_str)
    millis = int(ms_str)
    return float(mins * 60 + secs + millis / 1000.0)


class ForcedPauseSpan(BaseModel):
    text: str = Field(default="[pause]", min_length=1)
    t_start: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)
    t_end: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)

    @field_validator("t_start", "t_end", mode="before")
    @classmethod
    def _strip_timecode(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def t_start_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.t_start)

    @property
    def t_end_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.t_end)

    @model_validator(mode="after")
    def _check(self) -> "ForcedPauseSpan":
        if self.t_end_sec <= self.t_start_sec:
            raise ValueError(f"t_end must be > t_start (got {self.t_start}..{self.t_end})")
        return self


class ForcedAlignedWord(BaseModel):
    text: str = Field(min_length=1)
    t_start: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)
    t_end: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)

    @field_validator("t_start", "t_end", mode="before")
    @classmethod
    def _strip_timecode(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def t_start_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.t_start)

    @property
    def t_end_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.t_end)

    @model_validator(mode="after")
    def _check(self) -> "ForcedAlignedWord":
        if self.t_end_sec <= self.t_start_sec:
            raise ValueError(f"t_end must be > t_start (got {self.t_start}..{self.t_end})")
        return self


class ForcedSrtItem(BaseModel):
    start: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)
    end: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)
    text: str = Field(min_length=1)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _strip_timecode(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def start_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.start)

    @property
    def end_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.end)

    @model_validator(mode="after")
    def _check(self) -> "ForcedSrtItem":
        if self.end_sec <= self.start_sec:
            raise ValueError(f"srt end must be > start (got {self.start}..{self.end})")
        return self


class ForcedClipWindow(BaseModel):
    clip_start_abs: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)
    clip_end_abs: str = Field(min_length=1, pattern=FORCED_TIMECODE_PATTERN)
    moment_of_interest_sec: str | None = Field(default=None, pattern=FORCED_TIMECODE_PATTERN)

    @field_validator("clip_start_abs", "clip_end_abs", mode="before")
    @classmethod
    def _strip_required_timecodes(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("moment_of_interest_sec", mode="before")
    @classmethod
    def _strip_optional_timecode(cls, value: object) -> str | None:
        if value is None:
            return None
        raw = str(value or "").strip()
        return raw or None

    @property
    def clip_start_abs_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.clip_start_abs)

    @property
    def clip_end_abs_sec(self) -> float:
        return parse_forced_timecode_mmss_mmm(self.clip_end_abs)

    @property
    def moment_of_interest_sec_value(self) -> float | None:
        if self.moment_of_interest_sec is None:
            return None
        return parse_forced_timecode_mmss_mmm(self.moment_of_interest_sec)

    @model_validator(mode="after")
    def _check(self) -> "ForcedClipWindow":
        if self.clip_end_abs_sec <= self.clip_start_abs_sec:
            raise ValueError(
                f"clip_end_abs must be > clip_start_abs (got {self.clip_start_abs}..{self.clip_end_abs})"
            )
        moi = self.moment_of_interest_sec_value
        if moi is not None and moi < self.clip_start_abs_sec - 1e-6:
            raise ValueError(
                f"moment_of_interest_sec must be >= clip_start_abs (got {self.moment_of_interest_sec} < {self.clip_start_abs})"
            )
        return self


class ForcedSelectedFragment(BaseModel):
    audio: ForcedClipWindow
    transcript_words: List[ForcedAlignedWord] = Field(min_length=1)
    pause_spans: List[ForcedPauseSpan] = Field(default_factory=list)
    srt_items: List[ForcedSrtItem] = Field(default_factory=list)
    fragment_analytics: FragmentAnalytics | None = None

    @model_validator(mode="after")
    def _check(self) -> "ForcedSelectedFragment":
        cs = float(self.audio.clip_start_abs_sec)
        ce = float(self.audio.clip_end_abs_sec)
        for w in self.transcript_words:
            if float(w.t_start_sec) < cs - 1e-6 or float(w.t_end_sec) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.transcript_words item out of clip "
                    f"({w.text!r}, {w.t_start}..{w.t_end} not in {self.audio.clip_start_abs}..{self.audio.clip_end_abs})"
                )
        for p in self.pause_spans:
            if float(p.t_start_sec) < cs - 1e-6 or float(p.t_end_sec) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.pause_spans item out of clip "
                    f"({p.t_start}..{p.t_end} not in {self.audio.clip_start_abs}..{self.audio.clip_end_abs})"
                )
        for it in self.srt_items:
            if float(it.start_sec) < cs - 1e-6 or float(it.end_sec) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.srt_items item out of clip "
                    f"({it.start}..{it.end} not in {self.audio.clip_start_abs}..{self.audio.clip_end_abs})"
                )
        return self


class Stage1ForcedAlignmentPayload(BaseModel):
    aligned_words: List[ForcedAlignedWord] = Field(min_length=1)
    pause_spans: List[ForcedPauseSpan] = Field(default_factory=list)
    selected_fragment: ForcedSelectedFragment | None = None
