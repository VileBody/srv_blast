from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator


class FootageStylePickPayload(BaseModel):
    """
    Stage2B footage style pick returned by Gemini.
    Gemini selects only style coordinates, while concrete clips are selected by code.
    """

    genre: str = Field(min_length=1)
    tag: str = Field(min_length=1)


_ALLOWED_STYLE_MOOD = {"major", "minor"}
_ALLOWED_STYLE_COLOR = {"dark", "light", "warm", "cold", "neutral"}
_ALLOWED_STYLE_PEOPLE = {"none", "girls", "guys", "couple", "crowd", "driver"}


class FootageStyleRawFilters(BaseModel):
    color_priority: List[str] = Field(min_length=1)
    exclude: List[str] = Field(default_factory=list)
    priority_theme_tags: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _normalize(self) -> "FootageStyleRawFilters":
        color_seen: set[str] = set()
        colors: List[str] = []
        for c in list(self.color_priority or []):
            cv = str(c).strip().lower()
            if not cv:
                continue
            if cv not in _ALLOWED_STYLE_COLOR:
                raise ValueError(f"Unsupported filters.color_priority value: {cv!r}")
            if cv not in color_seen:
                color_seen.add(cv)
                colors.append(cv)
        if not colors:
            raise ValueError("filters.color_priority must contain at least one value")
        self.color_priority = [str(c) for c in colors]  # type: ignore[assignment]

        ex_seen: set[str] = set()
        ex_out: List[str] = []
        for p in list(self.exclude or []):
            pv = str(p).strip().lower()
            if pv == "guy":
                pv = "guys"
            if not pv:
                continue
            if pv not in _ALLOWED_STYLE_PEOPLE:
                raise ValueError(f"Unsupported filters.exclude value: {pv!r}")
            if pv not in ex_seen:
                ex_seen.add(pv)
                ex_out.append(pv)
        self.exclude = [str(x) for x in ex_out]  # type: ignore[assignment]

        tag_seen: set[str] = set()
        tags: List[str] = []
        for t in list(self.priority_theme_tags or []):
            tv = " ".join(str(t).strip().lower().split())
            if tv and tv not in tag_seen:
                tag_seen.add(tv)
                tags.append(tv)
        if not tags:
            raise ValueError("filters.priority_theme_tags must contain at least one non-empty tag")
        self.priority_theme_tags = tags
        return self


class FootageStyleRawPayload(BaseModel):
    theme: str = Field(min_length=1)
    mood: str
    filters: FootageStyleRawFilters

    @model_validator(mode="after")
    def _normalize(self) -> "FootageStyleRawPayload":
        self.theme = " ".join(str(self.theme).strip().lower().split())
        if not self.theme:
            raise ValueError("theme must be non-empty")
        mood = str(self.mood).strip().lower()
        if mood not in _ALLOWED_STYLE_MOOD:
            raise ValueError(f"Unsupported mood value: {mood!r}")
        self.mood = mood
        return self
