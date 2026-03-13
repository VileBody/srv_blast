from __future__ import annotations

import json
from pathlib import Path

from mlcore.prompts.assemble import build_stage2_subtitles_user_prompt
from mlcore.prompts.stage2_subtitles_impulse_2nd import IMPULSE_PROMPT_BODY


def test_impulse_prompt_body_is_literal_reference_copy() -> None:
    ref = Path("2nd_template/impulse_prompt.md").read_text(encoding="utf-8")
    assert IMPULSE_PROMPT_BODY == ref


def test_impulse_user_prompt_contains_raw_adapter_context() -> None:
    stage1_json = {
        "audio": {"clip_start_abs": 10.0, "clip_end_abs": 12.0},
        "draft_blocks": {},
        "transcript_words": [
            {"text": "hello", "t_start": 10.2, "t_end": 10.5},
            {"text": "world", "t_start": 10.6, "t_end": 11.0},
            {"text": "outside", "t_start": 12.2, "t_end": 12.4},
        ],
    }
    prompt = build_stage2_subtitles_user_prompt(
        stage1_json=stage1_json,
        schema_name="Impulse2ndRawPayload",
        subtitles_mode="impulse_2nd",
    )
    marker = "STAGE1_SUBTITLES_CONTEXT_JSON:\n"
    ctx = json.loads(prompt.split(marker, 1)[1])
    impulse_ctx = ctx.get("impulse_raw_context")
    assert isinstance(impulse_ctx, dict)
    assert abs(float(impulse_ctx["anchor_in_abs"]) - 10.2) < 1e-6
    word_timings = impulse_ctx.get("word_timings")
    assert isinstance(word_timings, list) and len(word_timings) == 2
    assert word_timings[0]["word"] == "hello"
    assert abs(float(word_timings[0]["start"]) - 0.0) < 1e-6
    assert abs(float(word_timings[1]["start"]) - 0.4) < 1e-6

