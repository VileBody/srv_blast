# mlcore/models/full_plan.py
from __future__ import annotations

from pydantic import BaseModel

from .audio_window import AudioClipPlan
from .subtitles_tokens import BlocksTokensPayload
from .footage_plan import FootageSelectionPayload


class FullPlanPayload(BaseModel):
    """
    Single Gemini call output:
      - audio: absolute window on the full track (Step 1)
      - subtitles: absolute token times on the full track, inside that window (Step 2)
      - footage: comp timeline clips (Step 3)

    IMPORTANT:
      - AE audio layer params are NOT produced by Gemini anymore.
        They are derived deterministically in postprocess from audio.clip_start_abs / clip_end_abs.
      - In postprocess we shift subtitles to clip-zero by subtracting clip_start_abs.
    """
    audio: AudioClipPlan
    subtitles: BlocksTokensPayload
    footage: FootageSelectionPayload
