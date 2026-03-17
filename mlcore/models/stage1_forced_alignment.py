from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator

from .stage1_asr import Stage1AsrSelectedFragment


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


class Stage1ForcedAlignmentPayload(BaseModel):
    aligned_words: List[ForcedAlignedWord] = Field(min_length=1)
    pause_spans: List[ForcedPauseSpan] = Field(default_factory=list)
    selected_fragment: Stage1AsrSelectedFragment | None = None
