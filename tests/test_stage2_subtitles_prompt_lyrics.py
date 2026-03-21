from __future__ import annotations

import json

from mlcore.prompts.assemble import build_stage2_subtitles_user_prompt


def test_stage2_subtitles_prompt_contains_lyrics_text_and_filtered_words() -> None:
    stage1_json = {
        "audio": {"clip_start_abs": 1.0, "clip_end_abs": 3.0},
        "draft_blocks": {"block_1": {"phrases": ["a"]}},
        "transcript_words": [
            {"text": "before", "t_start": 0.0, "t_end": 0.5},
            {"text": "in", "t_start": 1.2, "t_end": 1.6},
            {"text": "in2", "t_start": 2.0, "t_end": 2.5},
            {"text": "after", "t_start": 3.1, "t_end": 3.2},
        ],
        "pause_spans": [
            {"text": "[pause]", "t_start": 0.6, "t_end": 1.1},
            {"text": "[pause]", "t_start": 1.7, "t_end": 1.9},
            {"text": "[pause]", "t_start": 3.2, "t_end": 3.6},
        ],
        "lyrics_text": "REAL LYRICS",
    }

    prompt = build_stage2_subtitles_user_prompt(stage1_json=stage1_json)
    marker = "STAGE1_SUBTITLES_CONTEXT_JSON:\n"
    assert marker in prompt

    ctx = json.loads(prompt.split(marker, 1)[1])
    assert ctx["lyrics_text"] == "REAL LYRICS"
    assert [w["text"] for w in ctx["transcript_words"]] == ["in", "in2"]
    assert len(ctx["pause_spans"]) == 1
    assert ctx["pause_spans"][0]["t_start"] == 1.7


def test_stage2_subtitles_prompt_contains_target_fragment_when_present() -> None:
    stage1_json = {
        "audio": {"clip_start_abs": 1.0, "clip_end_abs": 2.5},
        "draft_blocks": {"block_1": {"phrases": ["a"]}},
        "transcript_words": [
            {"text": "in", "t_start": 1.2, "t_end": 1.6},
        ],
        "lyrics_text": "REAL LYRICS",
        "target_fragment": "SHE IS NOT MY LOVER",
        "fragment_analytics": {"target_fragment": "SHE IS NOT MY LOVER"},
    }

    prompt = build_stage2_subtitles_user_prompt(stage1_json=stage1_json)
    marker = "STAGE1_SUBTITLES_CONTEXT_JSON:\n"
    ctx = json.loads(prompt.split(marker, 1)[1])

    assert ctx["target_fragment"] == "SHE IS NOT MY LOVER"
    assert isinstance(ctx.get("fragment_analytics"), dict)
