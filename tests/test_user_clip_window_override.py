from __future__ import annotations

import logging

import pytest

from mlcore import gemini_orchestrator as go
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_plan import Stage1PlanPayload


def _draft_blocks() -> dict:
    return {
        "block_1": {"phrases": ["a"]},
        "block_2": {"p1": {"phrases": ["b"]}, "p2": {"phrases": ["c"]}},
        "block_3": {"phrases": ["d"]},
        "block_4": {"p1": {"phrases": ["e"]}, "p2": {"phrases": ["f"]}},
        "block_5": {
            "slowly_in": {"phrases": ["g"]},
            "fast_reveal": {"phrases": ["h"]},
            "glitch_peak": {"phrases": ["i"]},
            "mine": {"phrases": ["j"]},
        },
        "block_6": {"phrases": ["k"]},
        "block_7": {"part1": {"phrases": ["l"]}, "part2": {"phrases": ["m"]}},
    }


def _stage1_asr_payload() -> Stage1AsrPayload:
    return Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "w1", "t_start": 1.0, "t_end": 2.0},
                {"text": "w2", "t_start": 6.0, "t_end": 7.0},
                {"text": "w3", "t_start": 10.0, "t_end": 11.0},
                {"text": "w4", "t_start": 16.0, "t_end": 17.0},
            ],
            "pause_spans": [
                {"text": "[pause]", "t_start": 7.2, "t_end": 8.0},
                {"text": "[pause]", "t_start": 15.0, "t_end": 15.5},
            ],
            "srt_items": [],
        }
    )


def _stage1_plan_payload() -> Stage1PlanPayload:
    return Stage1PlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": 0.0, "clip_end_abs": 20.0, "moment_of_interest_sec": 2.0},
            "transcript_words": [
                {"text": "w1", "t_start": 1.0, "t_end": 2.0},
                {"text": "w2", "t_start": 6.0, "t_end": 7.0},
                {"text": "w3", "t_start": 10.0, "t_end": 11.0},
                {"text": "w4", "t_start": 16.0, "t_end": 17.0},
            ],
            "pause_spans": [
                {"text": "[pause]", "t_start": 7.2, "t_end": 8.0},
                {"text": "[pause]", "t_start": 15.0, "t_end": 15.5},
            ],
            "draft_blocks": _draft_blocks(),
            "fragment_analytics": {
                "target_fragment": "hello",
                "working_fragment": "hello world",
                "working_start_abs": 1.0,
                "working_end_abs": 3.0,
                "working_start_text": "00:01.000",
                "working_end_text": "00:03.000",
                "relation_to_target": "wider",
                "chosen_action": "expand",
                "rationale": "test",
            },
        }
    )


def test_optional_user_clip_window_env_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_CLIP_START_SEC", "12.5")
    monkeypatch.setenv("USER_CLIP_END_SEC", "33.0")
    out = go._optional_user_clip_window_from_env(logger=logging.getLogger("test.user_clip"))
    assert out == (12.5, 33.0)


def test_optional_user_clip_window_env_requires_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_CLIP_START_SEC", "12.5")
    monkeypatch.delenv("USER_CLIP_END_SEC", raising=False)
    with pytest.raises(RuntimeError, match="must be set together"):
        go._optional_user_clip_window_from_env(logger=logging.getLogger("test.user_clip"))


def test_apply_user_clip_window_updates_stage1_window_and_context() -> None:
    updated = go._apply_user_clip_window_to_stage1(
        stage1=_stage1_plan_payload(),
        stage1_asr=_stage1_asr_payload(),
        start_abs=5.0,
        end_abs=19.0,
        logger=logging.getLogger("test.user_clip"),
    )
    assert abs(float(updated.audio.clip_start_abs) - 5.0) <= 1e-6
    assert abs(float(updated.audio.clip_end_abs) - 19.0) <= 1e-6
    assert updated.fragment_analytics is None
    words = [str(w.text) for w in updated.transcript_words]
    assert words == ["w2", "w3", "w4"]
    pauses = [(float(p.t_start), float(p.t_end)) for p in updated.pause_spans]
    assert pauses == [(7.2, 8.0), (15.0, 15.5)]
