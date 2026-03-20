from __future__ import annotations

from core.clip_window import CLIP_WINDOW_RANGE_LABEL
from mlcore.prompts.assemble import (
    build_stage1a_asr_user_prompt,
    build_stage1a_forced_alignment_user_prompt,
)


def test_stage1a_asr_prompt_can_require_selected_fragment_with_target() -> None:
    prompt = build_stage1a_asr_user_prompt(
        schema_name="Stage1AsrPayload",
        require_selected_fragment=True,
        target_fragment="you and me forever",
    )
    assert "SELECT_FRAGMENT_BRANCH=ON" in prompt
    assert "USER_TARGET_FRAGMENT_BRANCH=ON" in prompt
    assert "USER_TARGET_FRAGMENT:\nyou and me forever\n" in prompt
    assert "selected_fragment" in prompt
    assert "duration MUST be >=" in prompt
    assert "duration MAY exceed" in prompt
    assert "ABSOLUTE full-track seconds" in prompt
    assert "relation_to_target must be one of: wider | inside_13_30" in prompt
    assert "chosen_action must be one of: expand | none" in prompt
    assert "keep the full fragment (do NOT narrow/select subfragment)" in prompt


def test_stage1a_forced_prompt_can_require_selected_fragment_without_target() -> None:
    prompt = build_stage1a_forced_alignment_user_prompt(
        reference_text="hello world",
        schema_name="Stage1ForcedAlignmentPayload",
        require_selected_fragment=True,
        target_fragment="",
    )
    assert "SELECT_FRAGMENT_BRANCH=ON" in prompt
    assert "USER_TARGET_FRAGMENT_BRANCH=OFF" in prompt
    assert f"most memorable/expressive {CLIP_WINDOW_RANGE_LABEL}s moment" in prompt
    assert "selected_fragment" in prompt
    assert "ABSOLUTE full-track timeline" in prompt
    assert "mm:ss.mmm strings" in prompt
    assert "EXACTLY 3 digits after dot" in prompt
