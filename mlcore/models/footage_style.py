from __future__ import annotations

from typing import List, Optional

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
    exclude_tags: List[str] = Field(default_factory=list)
    require_people: Optional[str] = None
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

        ex_tag_seen: set[str] = set()
        ex_tags: List[str] = []
        for t in list(self.exclude_tags or []):
            tv = " ".join(str(t).strip().lower().split())
            if tv and tv not in ex_tag_seen:
                ex_tag_seen.add(tv)
                ex_tags.append(tv)
        self.exclude_tags = ex_tags  # type: ignore[assignment]

        if self.require_people is not None:
            rp = str(self.require_people).strip().lower()
            if rp == "guy":
                rp = "guys"
            if rp and rp not in _ALLOWED_STYLE_PEOPLE:
                raise ValueError(f"Unsupported filters.require_people value: {rp!r}")
            self.require_people = rp or None  # type: ignore[assignment]

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
    tags_group: Optional[str] = None
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


class FootageStyleRotation(BaseModel):
    """
    Ordered list of 2-3 subgroups for rotation within a single job.
    Clip intervals are split into equal blocks; each block uses the next subgroup's filters.
    All subgroups must share the same theme and mood.
    """

    subgroups: List[FootageStyleRawPayload] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _check_consistency(self) -> "FootageStyleRotation":
        if len(self.subgroups) < 2:
            return self
        first_theme = self.subgroups[0].theme
        first_mood = self.subgroups[0].mood
        for i, sg in enumerate(self.subgroups[1:], start=1):
            if sg.theme != first_theme:
                raise ValueError(
                    f"subgroups[{i}].theme={sg.theme!r} differs from subgroups[0].theme={first_theme!r}; "
                    "all subgroups must belong to the same theme"
                )
            if sg.mood != first_mood:
                raise ValueError(
                    f"subgroups[{i}].mood={sg.mood!r} differs from subgroups[0].mood={first_mood!r}"
                )
        # Deduplicate by tags_group name — prevents LLM from returning the same subgroup twice
        seen_groups: set[str] = set()
        deduped: List[FootageStyleRawPayload] = []
        for sg in self.subgroups:
            key = str(sg.tags_group or "").strip().lower() or str(sg.filters.priority_theme_tags)
            if key not in seen_groups:
                seen_groups.add(key)
                deduped.append(sg)
        self.subgroups = deduped
        return self
