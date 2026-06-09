from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field, model_validator

from ._cjson_compat import restore_cjson_empty_lists
from .stage1_plan import FragmentAnalytics, PauseSpan, Stage1AudioWindow, TranscriptWord


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
    pause_spans: List[PauseSpan] = Field(default_factory=list)
    srt_items: List[SrtItem] = Field(default_factory=list)
    fragment_analytics: FragmentAnalytics | None = None

    @model_validator(mode="before")
    @classmethod
    def _restore_cjson_empty_lists(cls, data: Any) -> Any:
        return restore_cjson_empty_lists(cls, data)

    @model_validator(mode="after")
    def _check(self) -> "Stage1AsrSelectedFragment":
        cs = float(self.audio.clip_start_abs)
        ce = float(self.audio.clip_end_abs)
        # Allow items that partially overlap the clip boundary (LLM may place
        # the clip edge in the middle of a word).  Reject only items that are
        # entirely outside the clip window.
        for w in self.transcript_words:
            if float(w.t_end) < cs - 1e-6 or float(w.t_start) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.transcript_words item out of clip ({w.text!r}, "
                    f"{w.t_start}..{w.t_end} not in {cs}..{ce})"
                )
        for it in self.srt_items:
            if float(it.end) < cs - 1e-6 or float(it.start) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.srt_items item out of clip "
                    f"({it.start}..{it.end} not in {cs}..{ce})"
                )
        for p in self.pause_spans:
            if float(p.t_end) < cs - 1e-6 or float(p.t_start) > ce + 1e-6:
                raise ValueError(
                    f"selected_fragment.pause_spans item out of clip "
                    f"({p.t_start}..{p.t_end} not in {cs}..{ce})"
                )
        return self


class Stage1AsrPayload(BaseModel):
    transcript_words: List[TranscriptWord] = Field(min_length=1)
    pause_spans: List[PauseSpan] = Field(default_factory=list)
    srt_items: List[SrtItem] = Field(default_factory=list)
    selected_fragment: Stage1AsrSelectedFragment | None = None

    @model_validator(mode="before")
    @classmethod
    def _restore_cjson_empty_lists(cls, data: Any) -> Any:
        return restore_cjson_empty_lists(cls, data)
