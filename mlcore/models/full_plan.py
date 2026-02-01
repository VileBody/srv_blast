# mlcore/models/full_plan.py
from __future__ import annotations

from pydantic import BaseModel

from .audio_window import AudioClipPlan
from .subtitles_tokens import BlocksTokensPayload
from .footage_plan import FootageSelectionPayload


class FullPlanPayload(BaseModel):
    """
    Single Gemini call output:
      - audio window (absolute + AE layer params)
      - subtitles tokens payload (absolute token times inside audio window)
      - footage plan (comp timeline clips)
    """
    audio: AudioClipPlan
    subtitles: BlocksTokensPayload
    footage: FootageSelectionPayload
