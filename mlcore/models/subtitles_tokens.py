# mlcore/models/subtitles_tokens.py
from __future__ import annotations

from typing import List, Literal
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
        if dur < 13.0 or dur > 18.0:
            raise ValueError(f"clip duration must be 13..18 seconds (got {dur})")
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


class MineSegment(BaseModel):
    """
    NEW CONTRACT (variant A):
    Mine is a dedicated drop segment inside block_5.
    It is NOT a subset of glitch_peak anymore.

    Constraints:
      - exactly 1 token
      - token.trailing == ""
      - token.text is a single "word" (no spaces, no \\r/\\n/\\t)
      - phrase must equal token.text OR "\\r" + token.text (to allow line-break feel)
      - phrase must NOT contain "\\n"
    """
    phrase: str = Field(min_length=1)
    tokens: List[Token] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def _check_mine(self) -> "MineSegment":
        if not self.tokens or len(self.tokens) != 1:
            raise ValueError("mine.tokens must contain exactly 1 token")
        tok = self.tokens[0]

        if tok.trailing != "":
            raise ValueError(f"mine token trailing must be '' (got {tok.trailing!r})")

        if (" " in tok.text) or ("\r" in tok.text) or ("\n" in tok.text) or ("\t" in tok.text):
            raise ValueError(f"mine token text must be a single word (got {tok.text!r})")

        if "\n" in self.phrase:
            raise ValueError("mine.phrase must not contain \\n")

        # allow either "ты!" or "\rты!"
        p = self.phrase
        if p.startswith("\r"):
            p2 = p[1:]
        else:
            p2 = p

        if p2 != tok.text:
            raise ValueError(
                "mine.phrase must equal mine token text (optionally prefixed with '\\r'). "
                f"phrase={self.phrase!r} token={tok.text!r}"
            )

        # also forbid spaces / tabs / newlines inside phrase (except leading \r)
        if (" " in p2) or ("\r" in p2) or ("\n" in p2) or ("\t" in p2):
            raise ValueError(f"mine.phrase must be a single word (got {self.phrase!r})")

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
    """
    NEW CONTRACT (variant A):
      - mine is REQUIRED and lives separately from glitch_peak
      - glitch_peak MUST NOT include mine token at all
    """
    slowly_in: Segment
    fast_reveal: Segment
    glitch_peak: Segment
    mine: MineSegment

    @model_validator(mode="after")
    def _no_mine_token_inside_glitch_peak(self) -> "Block5Glitch":
        mine_tok = self.mine.tokens[0]

        # IMPORTANT:
        # Repeated words across segments are normal (e.g. "не сошлись, не сошлись...").
        # We only want to forbid the *same timed token* being present in both glitch_peak and mine,
        # i.e. overlap in time (which would imply an index/span overlap in our materialization).
        ms = float(mine_tok.t_start)
        me = float(mine_tok.t_end)
        eps = 1e-6

        for i, t in enumerate(self.glitch_peak.tokens):
            gs = float(t.t_start)
            ge = float(t.t_end)
            overlaps = (ms < ge - eps) and (gs < me - eps)
            if overlaps:
                raise ValueError(
                    "mine token must NOT overlap in time with any glitch_peak token. "
                    f"mine={mine_tok.text!r} {ms}..{me} overlaps glitch_peak.tokens[{i}]={t.text!r} {gs}..{ge}"
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
        _check_tokens(self.block_5.mine.tokens, "block_5.mine")

        _check_tokens(self.block_6.tokens, "block_6")
        _check_tokens(self.block_7.part1.tokens, "block_7.part1")
        _check_tokens(self.block_7.part2.tokens, "block_7.part2")

        return self
