# mlcore/models/audio_window.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AudioClipPlan(BaseModel):
    """
    Step 1 output (simplified contract):
      - absolute audio window on full track ONLY
      - AE layer params are derived deterministically in postprocess:
          startTime = -clip_start_abs
          inPoint   = 0
          outPoint  = clip_end_abs - clip_start_abs
    """
    clip_start_abs: float = Field(ge=0.0)
    clip_end_abs: float = Field(ge=0.0)

    moment_of_interest_sec: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "AudioClipPlan":
        if self.clip_end_abs <= self.clip_start_abs:
            raise ValueError("clip_end_abs must be > clip_start_abs")

        dur = float(self.clip_end_abs) - float(self.clip_start_abs)
        # keep it consistent with current Stage1 rule (13..18 sec)
        if dur < 13.0 or dur > 18.0:
            raise ValueError(f"clip duration must be 13..18 seconds (got {dur})")

        return self
