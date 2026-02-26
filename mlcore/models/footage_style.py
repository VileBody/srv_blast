from __future__ import annotations

from pydantic import BaseModel, Field


class FootageStylePickPayload(BaseModel):
    """
    Stage2B footage style pick returned by Gemini.
    Gemini selects only style coordinates, while concrete clips are selected by code.
    """

    genre: str = Field(min_length=1)
    tag: str = Field(min_length=1)
