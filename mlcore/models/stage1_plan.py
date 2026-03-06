from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class TranscriptWord(BaseModel):
    text: str = Field(min_length=1)
    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "TranscriptWord":
        if self.t_end <= self.t_start:
            raise ValueError(f"t_end must be > t_start (got {self.t_start}..{self.t_end})")
        return self


class Stage1AudioWindow(BaseModel):
    clip_start_abs: float = Field(ge=0.0)
    clip_end_abs: float = Field(ge=0.0)
    moment_of_interest_sec: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "Stage1AudioWindow":
        if self.clip_end_abs <= self.clip_start_abs:
            raise ValueError("clip_end_abs must be > clip_start_abs")
        dur = float(self.clip_end_abs) - float(self.clip_start_abs)
        if dur < 13.0 or dur > 18.0:
            raise ValueError(f"clip duration must be 13..18 seconds (got {dur})")
        return self


class FragmentAnalytics(BaseModel):
    target_fragment: str = Field(min_length=1)
    working_fragment: str = Field(min_length=1)

    # Must mirror selected audio window in Stage1B output.
    working_start_abs: float = Field(ge=0.0)
    working_end_abs: float = Field(ge=0.0)

    # Text labels for traceability in logs/UI.
    working_start_text: str = Field(min_length=1)
    working_end_text: str = Field(min_length=1)

    # Relation between requested fragment and selected 13..18s window.
    relation_to_target: Literal["wider", "narrower", "inside_13_18"]
    chosen_action: Literal["expand", "select_subfragment", "none"]
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "FragmentAnalytics":
        if self.working_end_abs <= self.working_start_abs:
            raise ValueError("working_end_abs must be > working_start_abs")
        return self


class DraftSegmentList(BaseModel):
    phrases: List[str] = Field(min_length=1)


class DraftBlock2(BaseModel):
    p1: DraftSegmentList
    p2: DraftSegmentList


class DraftBlock4(BaseModel):
    p1: DraftSegmentList
    p2: DraftSegmentList


class DraftBlock5(BaseModel):
    slowly_in: DraftSegmentList
    fast_reveal: DraftSegmentList
    glitch_peak: DraftSegmentList
    mine: DraftSegmentList


class DraftBlock7(BaseModel):
    part1: DraftSegmentList
    part2: DraftSegmentList


class Stage1DraftBlocks(BaseModel):
    block_1: DraftSegmentList
    block_2: DraftBlock2
    block_3: DraftSegmentList
    block_4: DraftBlock4
    block_5: DraftBlock5
    block_6: DraftSegmentList
    block_7: DraftBlock7


class Stage1PlanPayload(BaseModel):
    audio: Stage1AudioWindow
    transcript_words: List[TranscriptWord] = Field(min_length=1)
    draft_blocks: Stage1DraftBlocks
    fragment_analytics: Optional[FragmentAnalytics] = None
