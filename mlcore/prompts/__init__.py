# mlcore/prompts/__init__.py
from __future__ import annotations

from .assemble import (
    build_system_instruction,
    build_user_prompt,
    build_stage1_system_instruction,
    build_stage1_user_prompt,
    build_stage1a_asr_system_instruction,
    build_stage1a_asr_user_prompt,
    build_stage1b_scenario_system_instruction,
    build_stage1b_scenario_user_prompt,
    build_stage2_subtitles_system_instruction,
    build_stage2_subtitles_user_prompt,
    build_stage2_footage_system_instruction,
    build_stage2_footage_user_prompt,
)

__all__ = [
    "build_system_instruction",
    "build_user_prompt",
    "build_stage1_system_instruction",
    "build_stage1_user_prompt",
    "build_stage1a_asr_system_instruction",
    "build_stage1a_asr_user_prompt",
    "build_stage1b_scenario_system_instruction",
    "build_stage1b_scenario_user_prompt",
    "build_stage2_subtitles_system_instruction",
    "build_stage2_subtitles_user_prompt",
    "build_stage2_footage_system_instruction",
    "build_stage2_footage_user_prompt",
]
