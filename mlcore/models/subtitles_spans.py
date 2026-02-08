from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ClipWindow(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_clip(self) -> "ClipWindow":
        if self.end <= self.start:
            raise ValueError(f"clip.end must be > clip.start (got {self.start}..{self.end})")
        return self


class TokenSpan(BaseModel):
    # Inclusive indices in stage1.transcript_words (0-based).
    start_idx: int = Field(ge=0)
    end_idx: int = Field(ge=0)
    # Model-side hint; required to keep "short phrase" signal explicit.
    char_count_hint: int = Field(ge=1, le=120)

    @model_validator(mode="after")
    def _check(self) -> "TokenSpan":
        if self.end_idx < self.start_idx:
            raise ValueError("end_idx must be >= start_idx")
        return self


class Block2Spans(BaseModel):
    p1: TokenSpan
    p2: TokenSpan


class Block4Spans(BaseModel):
    p1: TokenSpan
    p2: TokenSpan


class Block5Spans(BaseModel):
    slowly_in: TokenSpan
    fast_reveal: TokenSpan
    glitch_peak: TokenSpan
    mine: TokenSpan

    @model_validator(mode="after")
    def _mine_single_word(self) -> "Block5Spans":
        if self.mine.start_idx != self.mine.end_idx:
            raise ValueError("block_5.mine must span exactly one token")
        return self


class Block7Spans(BaseModel):
    part1: TokenSpan
    part2: TokenSpan


class BlocksTokenSpansPayload(BaseModel):
    clip: ClipWindow

    block_1: TokenSpan
    block_2: Block2Spans
    block_3: TokenSpan
    block_4: Block4Spans
    block_5: Block5Spans
    block_6: TokenSpan
    block_7: Block7Spans
