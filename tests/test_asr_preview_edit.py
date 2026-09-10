"""ASR preview (веб-визард): пересборка stage1_asr по правкам слов + подсказка фокус-слов."""
from __future__ import annotations

import json
import logging

import pytest

from services.orchestrator import tasks
from services.orchestrator.schemas import AsrWordsUpdateRequest, SendAudioS3Request


def _stage1_asr() -> dict:
    words = [
        {"text": "раз", "t_start": 10.0, "t_end": 10.4},
        {"text": "два", "t_start": 10.5, "t_end": 10.9},
        {"text": "три", "t_start": 12.5, "t_end": 13.0},
    ]
    return {
        "transcript_words": words,
        "pause_spans": [{"text": "[pause]", "t_start": 10.9, "t_end": 12.5}],
        "srt_items": [],
        "selected_fragment": {
            "audio": {"clip_start_abs": 10.0, "clip_end_abs": 14.0},
            "transcript_words": words,
            "pause_spans": [{"text": "[pause]", "t_start": 10.9, "t_end": 12.5}],
            "srt_items": [],
            "fragment_analytics": {
                "target_fragment": "раз два три",
                "working_fragment": "раз два три",
                "working_start_abs": 10.0,
                "working_end_abs": 14.0,
                "working_start_text": "user_clip_start",
                "working_end_text": "user_clip_end",
                "relation_to_target": "inside_13_18",
                "chosen_action": "none",
                "rationale": "local_ctc_user_window_is_source_of_truth",
            },
        },
    }


def test_rebuild_replaces_words_and_rederives_pauses() -> None:
    edited = [
        {"text": "раз", "t_start": 10.1, "t_end": 10.5},
        {"text": "два", "t_start": 10.6, "t_end": 11.0},
        {"text": "три", "t_start": 11.2, "t_end": 11.7},
    ]
    out = tasks.rebuild_stage1_asr_with_words(_stage1_asr(), words=edited, pause_min_gap_sec=1.0)
    assert [w["t_start"] for w in out["transcript_words"]] == [10.1, 10.6, 11.2]
    assert out["selected_fragment"]["transcript_words"] == out["transcript_words"]
    # пауза 10.9..12.5 исчезла — слово «три» подвинули ближе
    assert out["pause_spans"] == []
    assert out["selected_fragment"]["pause_spans"] == []
    # окно фрагмента и аналитика не тронуты
    assert out["selected_fragment"]["audio"] == {"clip_start_abs": 10.0, "clip_end_abs": 14.0}
    assert out["selected_fragment"]["fragment_analytics"]["working_end_abs"] == 14.0


def test_rebuild_keeps_pause_when_gap_stays_wide() -> None:
    edited = [
        {"text": "раз", "t_start": 10.0, "t_end": 10.4},
        {"text": "два", "t_start": 10.5, "t_end": 10.9},
        {"text": "три", "t_start": 13.0, "t_end": 13.5},
    ]
    out = tasks.rebuild_stage1_asr_with_words(_stage1_asr(), words=edited, pause_min_gap_sec=1.0)
    assert out["pause_spans"] == [{"text": "[pause]", "t_start": 10.9, "t_end": 13.0}]


@pytest.mark.parametrize(
    "bad, msg",
    [
        ([{"text": "раз", "t_start": 9.5, "t_end": 10.4}], "outside the clip"),
        ([{"text": "раз", "t_start": 13.8, "t_end": 14.3}], "outside the clip"),
        (
            [
                {"text": "раз", "t_start": 10.0, "t_end": 10.8},
                {"text": "два", "t_start": 10.5, "t_end": 10.9},
            ],
            "before the previous word ends",
        ),
        ([{"text": "раз", "t_start": 10.5, "t_end": 10.5}], "t_end must be > t_start"),
        ([{"text": "  ", "t_start": 10.0, "t_end": 10.5}], "empty text"),
    ],
)
def test_rebuild_rejects_invalid_edits(bad: list, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        tasks.rebuild_stage1_asr_with_words(_stage1_asr(), words=bad, pause_min_gap_sec=1.0)


def test_words_update_request_schema_validates_span() -> None:
    with pytest.raises(ValueError):
        AsrWordsUpdateRequest.model_validate({"words": [{"text": "a", "t_start": 1.0, "t_end": 1.0}]})
    req = AsrWordsUpdateRequest.model_validate({"words": [{"text": "a", "t_start": 1.0, "t_end": 1.2}]})
    assert req.words[0].t_end == 1.2


def test_send_audio_request_accepts_focus_words() -> None:
    req = SendAudioS3Request.model_validate(
        {"audio_s3_url": "s3://b/k.mp3", "user_focus_words": [{"text": "три", "t_start": 12.5}]}
    )
    assert req.user_focus_words[0].text == "три"
    assert SendAudioS3Request.model_validate({"audio_s3_url": "s3://b/k.mp3"}).user_focus_words == []


class _W:
    def __init__(self, text: str, t_start: float) -> None:
        self.text = text
        self.t_start = t_start


def test_focus_hint_matches_by_text_and_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from mlcore.gemini_orchestrator import _user_focus_words_hint

    words = [_W("раз", 10.0), _W("три", 12.5), _W("три", 20.0)]
    monkeypatch.setenv(
        "USER_FOCUS_WORDS",
        json.dumps([{"text": "три", "t_start": 20.1}, {"text": "нет", "t_start": 1.0}]),
    )
    hint = _user_focus_words_hint(words_in_clip=words, logger=logging.getLogger("t"))
    assert "USER_FOCUS_WORDS" in hint
    assert '"три" @ 20.00s' in hint
    assert "12.50s" not in hint
    assert "нет" not in hint


def test_focus_hint_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from mlcore.gemini_orchestrator import _user_focus_words_hint

    monkeypatch.delenv("USER_FOCUS_WORDS", raising=False)
    assert _user_focus_words_hint(words_in_clip=[_W("a", 1.0)], logger=logging.getLogger("t")) == ""
