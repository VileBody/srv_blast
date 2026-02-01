# mlcore/models/audio_window.py
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, model_validator


class AudioClipPlan(BaseModel):
    """
    Step 1 output:
      - absolute audio window on full track
      - AE audio layer params (startTime/inPoint/outPoint) on comp timeline
    """
    clip_start_abs: float = Field(ge=0.0)
    clip_end_abs: float = Field(ge=0.0)

    layer_start_time: float
    layer_in_point: float = Field(ge=0.0)
    layer_out_point: float = Field(ge=0.0)

    moment_of_interest_sec: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "AudioClipPlan":
        if self.clip_end_abs <= self.clip_start_abs:
            raise ValueError("clip_end_abs must be > clip_start_abs")
        if self.layer_out_point <= self.layer_in_point:
            raise ValueError("layer_out_point must be > layer_in_point")

        dur_abs = float(self.clip_end_abs) - float(self.clip_start_abs)
        dur_layer = float(self.layer_out_point) - float(self.layer_in_point)

        if abs(dur_abs - dur_layer) > 0.10:
            raise ValueError(
                "Duration mismatch: (clip_end_abs-clip_start_abs) must match (layer_out_point-layer_in_point). "
                f"clip_dur={dur_abs} layer_dur={dur_layer}"
            )

        expected = -float(self.clip_start_abs) + float(self.layer_in_point)
        if abs(float(self.layer_start_time) - expected) > 0.35:
            raise ValueError(
                "layer_start_time inconsistent with clip_start_abs/layer_in_point. "
                f"expected≈{expected} got={self.layer_start_time}"
            )
        return self
