from __future__ import annotations

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
    assert "13..18" in prompt
    assert "ABSOLUTE full-track seconds" in prompt
    assert "relation_to_target must be one of: wider | narrower | inside_13_18" in prompt
    assert "chosen_action must be one of: expand | select_subfragment | none" in prompt


def test_stage1a_forced_prompt_can_require_selected_fragment_without_target() -> None:
    prompt = build_stage1a_forced_alignment_user_prompt(
        reference_text="hello world",
        schema_name="Stage1ForcedAlignmentPayload",
        require_selected_fragment=True,
        target_fragment="",
    )
    assert "SELECT_FRAGMENT_BRANCH=ON" in prompt
    assert "USER_TARGET_FRAGMENT_BRANCH=OFF" in prompt
    assert "most memorable/expressive 13..18s moment" in prompt
    assert "selected_fragment" in prompt
    assert "ABSOLUTE full-track seconds" in prompt
