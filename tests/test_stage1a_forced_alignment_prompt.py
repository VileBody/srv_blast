from __future__ import annotations

from mlcore.prompts.assemble import build_stage1a_forced_alignment_user_prompt


def test_stage1a_forced_alignment_prompt_forbids_extra_words() -> None:
    prompt = build_stage1a_forced_alignment_user_prompt(reference_text="Hello world")
    assert "no extra backing/ad-lib words" in prompt
    assert "REFERENCE_TEXT:\nHello world\n" in prompt
