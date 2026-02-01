# mlcore/models/subtitles_tokens.py
from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, model_validator

Trailing = Literal[" ", "\r", ""]


class ClipWindow(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_clip(self) -> "ClipWindow":
        if self.end <= self.start:
            raise ValueError(f"clip.end must be > clip.start (got {self.start}..{self.end})")
        dur = self.end - self.start
        if dur < 15.0 or dur > 25.0:
            raise ValueError(f"clip duration must be 15..25 seconds (got {dur})")
        return self


class Token(BaseModel):
    text: str = Field(min_length=1)
    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)
    trailing: Trailing = " "

    @model_validator(mode="after")
    def _check_time(self) -> "Token":
        if self.t_end <= self.t_start:
            raise ValueError(f"t_end must be > t_start (got {self.t_start}..{self.t_end})")
        return self


class Segment(BaseModel):
    phrase: str = Field(min_length=1)
    tokens: List[Token] = Field(min_length=1)


class MineDrop(BaseModel):
    text: str = Field(min_length=1)
    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_time(self) -> "MineDrop":
        if self.t_end <= self.t_start:
            raise ValueError(f"mine_drop.t_end must be > t_start (got {self.t_start}..{self.t_end})")
        if (" " in self.text) or ("\r" in self.text) or ("\n" in self.text) or ("\t" in self.text):
            raise ValueError(f"mine_drop.text must be a single word (got {self.text!r})")
        return self


class Block1Intro(BaseModel):
    phrase: str = Field(min_length=1)
    tokens: List[Token] = Field(min_length=1)


class Block2Waltz(BaseModel):
    p1: Segment
    p2: Segment


class Block3Photo(BaseModel):
    phrase: str = Field(min_length=1)
    tokens: List[Token] = Field(min_length=1)


class Block4Baby(BaseModel):
    p1: Segment
    p2: Segment


class Block5Glitch(BaseModel):
    slowly_in: Segment
    fast_reveal: Segment
    glitch_peak: Segment
    mine_drop: MineDrop

    @model_validator(mode="after")
    def _mine_drop_matches_last_token_if_possible(self) -> "Block5Glitch":
        if self.glitch_peak.tokens:
            last = self.glitch_peak.tokens[-1]
            if last.text != self.mine_drop.text:
                raise ValueError(
                    "mine_drop.text must equal glitch_peak last token text exactly. "
                    f"mine_drop={self.mine_drop.text!r} last={last.text!r}"
                )
            if abs(float(last.t_start) - float(self.mine_drop.t_start)) > 1e-6 or abs(float(last.t_end) - float(self.mine_drop.t_end)) > 1e-6:
                raise ValueError(
                    "mine_drop timings must equal glitch_peak last token timings. "
                    f"mine_drop={self.mine_drop.t_start}..{self.mine_drop.t_end} last={last.t_start}..{last.t_end}"
                )
        return self


class Block6DualTruth(BaseModel):
    phrase: str = Field(min_length=1)
    tokens: List[Token] = Field(min_length=1)


class Block7Finale(BaseModel):
    part1: Segment
    part2: Segment


class BlocksTokensPayload(BaseModel):
    clip: ClipWindow

    block_1: Block1Intro
    block_2: Block2Waltz
    block_3: Block3Photo
    block_4: Block4Baby
    block_5: Block5Glitch
    block_6: Block6DualTruth
    block_7: Block7Finale

    @model_validator(mode="after")
    def _all_tokens_inside_clip(self) -> "BlocksTokensPayload":
        cs = float(self.clip.start)
        ce = float(self.clip.end)

        def _check_tokens(tokens: List[Token], where: str) -> None:
            for t in tokens:
                if t.t_start < cs - 1e-6 or t.t_end > ce + 1e-6:
                    raise ValueError(
                        f"Token out of clip window in {where}: "
                        f"{t.text!r} {t.t_start}..{t.t_end} not in [{cs}..{ce}]"
                    )

        _check_tokens(self.block_1.tokens, "block_1")
        _check_tokens(self.block_2.p1.tokens, "block_2.p1")
        _check_tokens(self.block_2.p2.tokens, "block_2.p2")
        _check_tokens(self.block_3.tokens, "block_3")
        _check_tokens(self.block_4.p1.tokens, "block_4.p1")
        _check_tokens(self.block_4.p2.tokens, "block_4.p2")
        _check_tokens(self.block_5.slowly_in.tokens, "block_5.slowly_in")
        _check_tokens(self.block_5.fast_reveal.tokens, "block_5.fast_reveal")
        _check_tokens(self.block_5.glitch_peak.tokens, "block_5.glitch_peak")

        md = self.block_5.mine_drop
        if md.t_start < cs - 1e-6 or md.t_end > ce + 1e-6:
            raise ValueError(f"mine_drop out of clip: {md.t_start}..{md.t_end} not in [{cs}..{ce}]")

        _check_tokens(self.block_6.tokens, "block_6")
        _check_tokens(self.block_7.part1.tokens, "block_7.part1")
        _check_tokens(self.block_7.part2.tokens, "block_7.part2")

        return self
