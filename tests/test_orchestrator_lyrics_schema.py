from __future__ import annotations

from services.orchestrator.schemas import SendAudioS3Request


def test_send_audio_schema_lyrics_default_empty() -> None:
    req = SendAudioS3Request(audio_s3_url="s3://bucket/raw/audio.mp3")
    assert req.lyrics_text == ""
    assert req.target_fragment == ""
    assert req.subtitles_mode == "legacy_blocks"


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
