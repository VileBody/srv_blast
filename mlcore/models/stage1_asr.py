from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator

from .stage1_plan import FragmentAnalytics, Stage1AudioWindow, TranscriptWord


class SrtItem(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "SrtItem":
        if self.end <= self.start:
            raise ValueError(f"srt end must be > start (got {self.start}..{self.end})")
        return self


class Stage1AsrSelectedFragment(BaseModel):
    audio: Stage1AudioWindow
    transcript_words: List[TranscriptWord] = Field(min_length=1)
    srt_items: List[SrtItem] = Field(default_factory=list)
    fragment_analytics: FragmentAnalytics | None = None

    @model_validator(mode="after")
    def _check(self) -> "Stage1AsrSelectedFragment":
        cs = float(self.audio.clip_start_abs)
        ce = float(self.audio.clip_end_abs)
        for w in self.transcript_words:
            if float(w.t_start) < cs - 1e-6 or float(w.t_end) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.transcript_words item out of clip ({w.text!r}, "
                    f"{w.t_start}..{w.t_end} not in {cs}..{ce})"
                )
        for it in self.srt_items:
            if float(it.start) < cs - 1e-6 or float(it.end) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.srt_items item out of clip "
                    f"({it.start}..{it.end} not in {cs}..{ce})"
                )
        return self


class Stage1AsrPayload(BaseModel):
    transcript_words: List[TranscriptWord] = Field(min_length=1)
    srt_items: List[SrtItem] = Field(default_factory=list)
    selected_fragment: Stage1AsrSelectedFragment | None = None
