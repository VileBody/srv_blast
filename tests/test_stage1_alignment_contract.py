from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.orchestrator.schemas import SendAudioS3Request


def test_gemini_alignment_is_default_and_backward_compatible() -> None:
    req = SendAudioS3Request(audio_s3_url="s3://bucket/track.mp3")
    assert req.stage1_alignment_backend == "gemini"


def test_local_alignment_requires_target_fragment_and_window() -> None:
    with pytest.raises(ValidationError):
        SendAudioS3Request(
            audio_s3_url="s3://bucket/track.mp3",
            stage1_alignment_backend="local_ctc",
        )
    with pytest.raises(ValidationError):
        SendAudioS3Request(
            audio_s3_url="s3://bucket/track.mp3",
            stage1_alignment_backend="local_ctc",
            target_fragment="точный текст",
        )


def test_local_alignment_accepts_complete_contract() -> None:
    req = SendAudioS3Request(
        audio_s3_url="s3://bucket/track.mp3",
        stage1_alignment_backend="local_ctc",
        target_fragment="точный текст",
        user_clip_start_sec=10.0,
        user_clip_end_sec=20.0,
    )
    assert req.stage1_alignment_backend == "local_ctc"
