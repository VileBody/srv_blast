from __future__ import annotations

from pydantic import BaseModel

from .stage1_plan import FragmentAnalytics, Stage1AudioWindow, Stage1DraftBlocks


class Stage1ScenarioPayload(BaseModel):
    audio: Stage1AudioWindow
    draft_blocks: Stage1DraftBlocks
    fragment_analytics: FragmentAnalytics | None = None
