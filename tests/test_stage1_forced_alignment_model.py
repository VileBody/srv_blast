from __future__ import annotations

import pytest
from pydantic import ValidationError

from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload


def test_stage1_forced_alignment_model_allows_non_monotonic_timestamps() -> None:
    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": [
                {"text": "hello", "t_start": "00:10.000", "t_end": "00:10.400"},
                {"text": "world", "t_start": "00:10.500", "t_end": "00:10.900"},
                {"text": "again", "t_start": "00:09.800", "t_end": "00:10.200"},
            ],
            "pause_spans": [{"text": "[pause]", "t_start": "00:10.900", "t_end": "00:12.000"}],
        }
    )
    assert len(payload.aligned_words) == 3
    assert len(payload.pause_spans) == 1


def test_stage1_forced_alignment_model_rejects_non_positive_word_duration() -> None:
    with pytest.raises(ValidationError):
        Stage1ForcedAlignmentPayload.model_validate(
            {
                "aligned_words": [
                    {"text": "bad", "t_start": "00:01.000", "t_end": "00:01.000"},
                ]
            }
        )


def test_stage1_forced_alignment_model_rejects_invalid_timecode_format() -> None:
    with pytest.raises(ValidationError):
        Stage1ForcedAlignmentPayload.model_validate(
            {
                "aligned_words": [
                    {"text": "bad", "t_start": "1.000", "t_end": "00:01.200"},
                ]
            }
        )


def test_stage1_forced_alignment_model_accepts_selected_fragment_timecodes() -> None:
    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": [
                {"text": "hello", "t_start": "00:10.000", "t_end": "00:10.400"},
            ],
            "selected_fragment": {
                "audio": {
                    "clip_start_abs": "01:00.000",
                    "clip_end_abs": "01:13.500",
                    "moment_of_interest_sec": "01:00.000",
                },
                "transcript_words": [
                    {"text": "hello", "t_start": "01:00.100", "t_end": "01:00.300"},
                    {"text": "world", "t_start": "01:11.000", "t_end": "01:11.400"},
                ],
                "pause_spans": [
                    {"text": "[pause]", "t_start": "01:00.300", "t_end": "01:01.500"},
                ],
                "srt_items": [
                    {"start": "01:00.100", "end": "01:11.400", "text": "hello world"},
                ],
            },
        }
    )
    assert payload.selected_fragment is not None
    assert payload.selected_fragment.audio.clip_start_abs_sec == pytest.approx(60.0)
    assert payload.selected_fragment.transcript_words[1].t_start_sec == pytest.approx(71.0)


def test_stage1_forced_alignment_model_rejects_decimal_selected_fragment_timecodes() -> None:
    with pytest.raises(ValidationError):
        Stage1ForcedAlignmentPayload.model_validate(
            {
                "aligned_words": [
                    {"text": "hello", "t_start": "00:10.000", "t_end": "00:10.400"},
                ],
                "selected_fragment": {
                    "audio": {"clip_start_abs": "1.11", "clip_end_abs": "1.23"},
                    "transcript_words": [
                        {"text": "hello", "t_start": "1.11", "t_end": "1.12"},
                    ],
                },
            }
        )
