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
