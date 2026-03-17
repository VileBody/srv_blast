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
