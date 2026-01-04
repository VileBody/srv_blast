"""
Сборник системных промптов для Gemini.

Экспортируем прежние имена, чтобы внешние импорты не ломались:
- DESCRIBE_VIDEO_SYSTEM
- SELECT_AUDIO_HIGHLIGHTS_SYSTEM
- PLAN_VISUALS_SYSTEM
- SUBTITLES_SYSTEM
- COMBINED_PLANNER_SYSTEM
- build_ae_project_system_prompt
"""

from __future__ import annotations

from .base import (
    DESCRIBE_VIDEO_SYSTEM,
    PLAN_VISUALS_SYSTEM,
    SELECT_AUDIO_HIGHLIGHTS_SYSTEM,
    SUBTITLES_SYSTEM,
)
from .combined_planner import COMBINED_PLANNER_SYSTEM
from .ae_project.builder import build_ae_project_system_prompt

__all__ = [
    "DESCRIBE_VIDEO_SYSTEM",
    "SELECT_AUDIO_HIGHLIGHTS_SYSTEM",
    "PLAN_VISUALS_SYSTEM",
    "SUBTITLES_SYSTEM",
    "COMBINED_PLANNER_SYSTEM",
    "build_ae_project_system_prompt",
]
