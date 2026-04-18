# mlcore/models/footage_plan.py
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


FitMode = Literal["cover", "contain", "stretch"]


class FootageAsset(BaseModel):
    file_name: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    src_w: int = Field(ge=1)
    src_h: int = Field(ge=1)


class FootageClipPick(BaseModel):
    """
    Footage clip contract (produced by deterministic picker code):
      - clip timings are ABSOLUTE seconds on the FULL TRACK timeline,
        and MUST lie inside [audio.clip_start_abs .. audio.clip_end_abs]
      - start_time MUST equal in_point - source_offset_sec
        (when source_offset_sec=0, this equals in_point, same as before)
    Postprocess:
      - we shift to clip-zero by subtracting clip_start_abs
      - then it becomes COMP timeline 0..duration for AE
      - source_offset_sec is preserved so AE layer.startTime stays negative enough
        to play from an internal point of the source file
    """
    file_name: str = Field(min_length=1)
    fit_mode: FitMode = "cover"

    # ABSOLUTE full-track seconds (not comp seconds)
    in_point: float = Field(ge=0.0)
    out_point: float = Field(ge=0.0)

    # MUST equal in_point - source_offset_sec
    start_time: float

    # How many seconds into the source file to start playback (0 = from the beginning)
    source_offset_sec: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "FootageClipPick":
        if self.out_point <= self.in_point:
            raise ValueError("out_point must be > in_point")
        expected = float(self.in_point) - float(self.source_offset_sec)
        if abs(float(self.start_time) - expected) > 1e-4:
            raise ValueError(
                f"start_time must equal in_point - source_offset_sec "
                f"(expected {expected:.6f}, got {self.start_time:.6f})"
            )
        return self


class FootageSelectionPayload(BaseModel):
    """
    Footage selection payload:
      - list of clips (absolute times)
      - allow_gaps: if false, postprocess will enforce continuous coverage
      - color_grade: optional color grade hint resolved from the selected
        footage subgroup's color_priority filter. Maps to an adjustment-effects
        sidecar JSX applied at render time (cold/warm). None disables the
        sidecar (neutral / unresolved).
      - allow_mirror: whether the uniqueness pass is allowed to horizontally
        flip footage layers. Resolved from the selected subgroup's
        filters.require_people: {"none", "crowd"} → True, else False.
        None means "not resolved" — treated as False by downstream.
    """
    clips: List[FootageClipPick] = Field(min_length=1)
    allow_gaps: bool = False
    color_grade: Optional[Literal["cold", "warm"]] = None
    allow_mirror: Optional[bool] = None
