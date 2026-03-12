# mlcore/models/full_plan.py
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from core.subtitles_mode import (
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_VALUES,
    SubtitlesMode,
)
from .audio_window import AudioClipPlan
from .subtitles_flow import SubtitleFlowPlan
from .subtitles_tokens import BlocksTokensPayload
from .footage_plan import FootageSelectionPayload


class FullPlanPayload(BaseModel):
    """
    Final merged payload for render:
      - audio: absolute window on the full track (Stage1)
      - subtitles: absolute token times on the full track, inside that window (Stage2A)
      - footage: absolute clips selected by deterministic picker (Stage2B style + code picker)

    IMPORTANT:
      - AE audio layer params are NOT produced by Gemini anymore.
        They are derived deterministically in postprocess from audio.clip_start_abs / clip_end_abs.
      - In postprocess we shift subtitles to clip-zero by subtracting clip_start_abs.
    """
    audio: AudioClipPlan
    subtitles_mode: SubtitlesMode = Field(default=SUBTITLES_MODE_LEGACY_BLOCKS)
    subtitles: BlocksTokensPayload | SubtitleFlowPlan
    footage: FootageSelectionPayload

    @model_validator(mode="after")
    def _check_subtitles_mode_contract(self) -> "FullPlanPayload":
        mode = str(self.subtitles_mode)
        if mode not in SUBTITLES_MODE_VALUES:
            raise ValueError(f"unknown subtitles_mode={mode!r}")

        if mode == SUBTITLES_MODE_LEGACY_BLOCKS:
            if not isinstance(self.subtitles, BlocksTokensPayload):
                raise ValueError("subtitles_mode=legacy_blocks requires BlocksTokensPayload")
            return self

        if not isinstance(self.subtitles, SubtitleFlowPlan):
            raise ValueError(f"subtitles_mode={mode} requires SubtitleFlowPlan payload")
        if str(self.subtitles.mode) != mode:
            raise ValueError(
                f"subtitles.mode mismatch: payload={self.subtitles.mode!r} expected={mode!r}"
            )
        return self
