from __future__ import annotations

from mlcore.prompts.assemble import build_stage1a_forced_alignment_user_prompt


def test_stage1a_forced_alignment_prompt_forbids_extra_words() -> None:
    prompt = build_stage1a_forced_alignment_user_prompt(reference_text="Hello world")
    assert "no extra backing/ad-lib words" in prompt
    assert "pause_spans" in prompt
    assert "silence gap between neighboring words is > 1.0s" in prompt
    assert "mm:ss.mmm" in prompt
    assert "Structural markers like [pause], [bridge], [hook], [verse] are not spoken words." in prompt
    assert "REFERENCE_TEXT:\nHello world\n" in prompt
