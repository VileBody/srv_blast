from __future__ import annotations

import json
from pathlib import Path

from services.orchestrator.render_manifest import collect_media_urls_from_render_payload


def test_audio_relpath_uses_name_from_render_payload(tmp_path: Path) -> None:
    payload = {
        "footage_layers": [
            {
                "type": "footage",
                "text_data": {
                    "layer_meta": {"audioEnabled": True},
                    "source_footage": {
                        "file_name": "expected_audio_name.mp3",
                        "file_path": "",
                    },
                },
            }
        ]
    }
    p = tmp_path / "render_payload.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    media = collect_media_urls_from_render_payload(
        p,
        audio_url="https://example.com/raw_audio/another_name_123.mp3?sig=abc",
    )

    assert media[0]["relpath"] == "media/audio/expected_audio_name.mp3"


def test_extra_f5_audio_layer_is_downloaded_into_media_audio(tmp_path: Path) -> None:
    """F5 («Мысль») TTS is a SECOND audio layer carrying a remote_url.

    The main track has no remote source on its layer (fetched via audio_url),
    so it must be skipped here, while the F5 wav must be collected into
    media/audio/<file_name> so the render node downloads it for AE.
    """
    payload = {
        "footage_layers": [
            {
                "type": "footage",
                "text_data": {
                    "layer_meta": {"audioEnabled": True},
                    "source_footage": {"file_name": "track.mp3", "file_path": ""},
                },
            },
            {
                "type": "footage",
                "text_data": {
                    "layer_meta": {"audioEnabled": True},
                    "source_footage": {
                        "file_name": "f5_hook_punchline.wav",
                        "file_path": "",
                        "remote_url": "s3://blast-assets/f5_hooks/job-1/f5_hook_punchline.wav",
                    },
                },
            },
            {
                "type": "footage",
                "text_data": {
                    "layer_meta": {"audioEnabled": False},
                    "source_footage": {
                        "file_name": "clip01.mp4",
                        "file_path": "",
                        "remote_url": "s3://blast-assets/footage/clip01.mp4",
                    },
                },
            },
        ]
    }
    p = tmp_path / "render_payload.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    media = collect_media_urls_from_render_payload(
        p, audio_url="s3://blast-audio/raw/track.mp3"
    )

    by_relpath = {m["relpath"]: m["url"] for m in media}
    # Track fetched via audio_url, keyed by the audio layer file_name.
    assert by_relpath["media/audio/track.mp3"] == "s3://blast-audio/raw/track.mp3"
    # F5 TTS wav collected from its remote_url into media/audio.
    assert (
        by_relpath["media/audio/f5_hook_punchline.wav"]
        == "s3://blast-assets/f5_hooks/job-1/f5_hook_punchline.wav"
    )
    # Video footage still goes to media/video.
    assert by_relpath["media/video/clip01.mp4"] == "s3://blast-assets/footage/clip01.mp4"
    # No duplicate track entry from the audio layer.
    assert sum(1 for r in by_relpath if r == "media/audio/track.mp3") == 1
