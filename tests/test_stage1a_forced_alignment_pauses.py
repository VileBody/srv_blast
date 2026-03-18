from __future__ import annotations

import pytest

from mlcore import gemini_orchestrator as go
from mlcore.models.stage1_asr import Stage1AsrSelectedFragment
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload


def test_stage1a_forced_alignment_derives_pause_spans_from_gaps() -> None:
    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": [
                {"text": "hello", "t_start": "00:10.000", "t_end": "00:10.400"},
                {"text": "world", "t_start": "00:11.800", "t_end": "00:12.200"},
            ]
        }
    )

    stage1_asr = go._stage1_asr_from_forced_alignment(payload)
    assert len(stage1_asr.pause_spans) == 1
    p = stage1_asr.pause_spans[0]
    assert p.text == "[pause]"
    assert p.t_start == pytest.approx(10.4)
    assert p.t_end == pytest.approx(11.8)


def test_selected_fragment_pause_spans_must_stay_inside_clip() -> None:
    with pytest.raises(ValueError, match="selected_fragment.pause_spans item out of clip"):
        Stage1AsrSelectedFragment.model_validate(
            {
                "audio": {"clip_start_abs": 10.0, "clip_end_abs": 24.0},
                "transcript_words": [{"text": "ok", "t_start": 11.0, "t_end": 11.4}],
                "pause_spans": [{"text": "[pause]", "t_start": 24.1, "t_end": 24.9}],
            }
        )


def test_stage1a_forced_alignment_selected_fragment_timecodes_are_converted_to_seconds() -> None:
    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": [
                {"text": "hello", "t_start": "01:00.100", "t_end": "01:00.300"},
                {"text": "world", "t_start": "01:11.000", "t_end": "01:11.300"},
            ],
            "selected_fragment": {
                "audio": {
                    "clip_start_abs": "01:00.000",
                    "clip_end_abs": "01:13.000",
                },
                "transcript_words": [
                    {"text": "hello", "t_start": "01:00.100", "t_end": "01:00.300"},
                    {"text": "world", "t_start": "01:11.000", "t_end": "01:11.300"},
                ],
            },
        }
    )

    stage1_asr = go._stage1_asr_from_forced_alignment(payload)
    assert stage1_asr.selected_fragment is not None
    assert stage1_asr.selected_fragment.audio.clip_start_abs == pytest.approx(60.0)
    assert stage1_asr.selected_fragment.audio.clip_end_abs == pytest.approx(73.0)
    assert stage1_asr.selected_fragment.transcript_words[-1].t_start == pytest.approx(71.0)
