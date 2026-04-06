from __future__ import annotations

from services.orchestrator.schemas import SendAudioS3Request


def test_send_audio_schema_lyrics_default_empty() -> None:
    req = SendAudioS3Request(audio_s3_url="s3://bucket/raw/audio.mp3")
    assert req.lyrics_text == ""
    assert req.target_fragment == ""
    assert req.subtitles_mode == "legacy_blocks"
    assert req.user_clip_start_sec is None
    assert req.user_clip_end_sec is None


def test_send_audio_schema_lyrics_explicit() -> None:
    req = SendAudioS3Request(
        audio_s3_url="s3://bucket/raw/audio.mp3",
        mode="with_gemini",
        lyrics_text="Hello world",
    )
    assert req.lyrics_text == "Hello world"


def test_send_audio_schema_target_fragment_explicit() -> None:
    req = SendAudioS3Request(
        audio_s3_url="s3://bucket/raw/audio.mp3",
        mode="with_gemini",
        lyrics_text="Hello world",
        target_fragment="and no one's gonna save you",
    )
    assert req.target_fragment == "and no one's gonna save you"


def test_send_audio_schema_subtitles_mode_explicit() -> None:
    req = SendAudioS3Request(
        audio_s3_url="s3://bucket/raw/audio.mp3",
        subtitles_mode="scenes_3rd",
    )
    assert req.subtitles_mode == "scenes_3rd"


def test_send_audio_schema_user_clip_window_explicit() -> None:
    req = SendAudioS3Request(
        audio_s3_url="s3://bucket/raw/audio.mp3",
        user_clip_start_sec=12.5,
        user_clip_end_sec=36.0,
    )
    assert req.user_clip_start_sec == 12.5
    assert req.user_clip_end_sec == 36.0


def test_send_audio_schema_user_clip_requires_both_bounds() -> None:
    try:
        SendAudioS3Request(
            audio_s3_url="s3://bucket/raw/audio.mp3",
            user_clip_start_sec=5.0,
        )
        assert False, "expected validation error for incomplete user clip window"
    except Exception as e:
        assert "must be provided together" in str(e)


def test_send_audio_schema_user_clip_requires_positive_duration() -> None:
    try:
        SendAudioS3Request(
            audio_s3_url="s3://bucket/raw/audio.mp3",
            user_clip_start_sec=30.0,
            user_clip_end_sec=20.0,
        )
        assert False, "expected validation error for invalid user clip window"
    except Exception as e:
        assert "must be >" in str(e)
