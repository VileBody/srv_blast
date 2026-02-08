from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator

from .stage1_plan import TranscriptWord


class SrtItem(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "SrtItem":
        if self.end <= self.start:
            raise ValueError(f"srt end must be > start (got {self.start}..{self.end})")
        return self


class Stage1AsrPayload(BaseModel):
    transcript_words: List[TranscriptWord] = Field(min_length=1)
    srt_items: List[SrtItem] = Field(default_factory=list)

