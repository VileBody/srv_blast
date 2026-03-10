from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator


class ForcedAlignedWord(BaseModel):
    text: str = Field(min_length=1)
    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "ForcedAlignedWord":
        if self.t_end <= self.t_start:
            raise ValueError(f"t_end must be > t_start (got {self.t_start}..{self.t_end})")
        return self


class Stage1ForcedAlignmentPayload(BaseModel):
    aligned_words: List[ForcedAlignedWord] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_monotonic(self) -> "Stage1ForcedAlignmentPayload":
        prev_start = -1.0
        prev_end = -1.0
        for i, w in enumerate(self.aligned_words):
            ts = float(w.t_start)
            te = float(w.t_end)
            if i > 0 and ts < prev_start:
                raise ValueError(f"aligned_words must be monotonic by t_start (idx={i}, {ts} < {prev_start})")
            if i > 0 and te < prev_end:
                raise ValueError(f"aligned_words must be monotonic by t_end (idx={i}, {te} < {prev_end})")
            prev_start = ts
            prev_end = te
        return self

