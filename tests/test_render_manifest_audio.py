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
