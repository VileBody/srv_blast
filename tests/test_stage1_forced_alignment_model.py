from __future__ import annotations

import pytest
from pydantic import ValidationError

from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload


def test_stage1_forced_alignment_model_allows_non_monotonic_timestamps() -> None:
    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": [
                {"text": "hello", "t_start": 10.0, "t_end": 10.4},
                {"text": "world", "t_start": 10.5, "t_end": 10.9},
                {"text": "again", "t_start": 9.8, "t_end": 10.2},
            ]
        }
    )
    assert len(payload.aligned_words) == 3


def test_stage1_forced_alignment_model_rejects_non_positive_word_duration() -> None:
    with pytest.raises(ValidationError):
        Stage1ForcedAlignmentPayload.model_validate(
            {
                "aligned_words": [
                    {"text": "bad", "t_start": 1.0, "t_end": 1.0},
                ]
            }
        )
