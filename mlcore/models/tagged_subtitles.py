from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field, model_validator


SubtitleTag = Literal["long", "short"]


class TaggedSubtitleItem(BaseModel):
    text: str = Field(min_length=1)
    tag: SubtitleTag
    in_abs: float = Field(alias="in")
    out_abs: float = Field(alias="out")

    @model_validator(mode="after")
    def _validate_times(self) -> "TaggedSubtitleItem":
        if float(self.out_abs) <= float(self.in_abs):
            raise ValueError(f"subtitle out must be > in (got {self.in_abs}..{self.out_abs})")
        if not str(self.text).strip():
            raise ValueError("subtitle text must be non-empty after trim")
        return self


class TaggedSubtitlesPayload(BaseModel):
    clip_start_abs: float
    clip_end_abs: float
    subtitles: List[TaggedSubtitleItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_sequence(self) -> "TaggedSubtitlesPayload":
        cs = float(self.clip_start_abs)
        ce = float(self.clip_end_abs)
        if ce <= cs:
            raise ValueError(f"invalid clip window {cs}..{ce}")

        prev_out = None
        seen = set()
        for i, it in enumerate(self.subtitles):
            t_in = float(it.in_abs)
            t_out = float(it.out_abs)
            if t_in < cs - 1e-6 or t_out > ce + 1e-6:
                raise ValueError(
                    f"subtitle[{i}] out of clip window: {t_in}..{t_out} not in {cs}..{ce}"
                )
            key = (round(t_in, 6), round(t_out, 6), str(it.text), str(it.tag))
            if key in seen:
                raise ValueError(f"subtitle[{i}] duplicate segment")
            seen.add(key)
            if prev_out is not None and t_in < prev_out - 1e-6:
                raise ValueError(
                    f"subtitle[{i}] overlaps previous: prev_out={prev_out} in={t_in}"
                )
            prev_out = t_out
        return self

