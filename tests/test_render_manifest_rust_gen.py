from __future__ import annotations

import json
from pathlib import Path

from services.orchestrator.render_manifest import build_rust_gen_job_payload


def test_build_rust_gen_manifest_presigns_inputs_and_preserves_output_contract(tmp_path: Path) -> None:
    payload_path = tmp_path / "render.json"
    payload_path.write_text(
        json.dumps(
            {
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
                            "layer_meta": {"audioEnabled": False},
                            "source_footage": {
                                "file_name": "clip.mp4",
                                "remote_url": "s3://footage/clips/clip.mp4",
                            },
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    downloads: list[tuple[str, str, int]] = []
    uploads: list[tuple[str, str, int]] = []

    out = build_rust_gen_job_payload(
        job_id="job-1",
        render_payload_path=payload_path,
        audio_url="s3://raw/track.mp3",
        output_s3_bucket="rendered",
        presign_ttl_s=42,
        presign_download=lambda bucket, key, ttl: downloads.append((bucket, key, ttl)) or f"https://get/{bucket}/{key}",
        presign_upload=lambda bucket, key, ttl: uploads.append((bucket, key, ttl)) or f"https://put/{bucket}/{key}",
    )

    assert out["schema"] == "ae-native-renderer.manager-request.v1"
    assert out["input"]["kind"] == "native_request"
    assert out["input"]["inline"]["footage_layers"][1]["text_data"]["source_footage"]["file_name"] == "clip.mp4"
    assert downloads == [("raw", "track.mp3", 42), ("footage", "clips/clip.mp4", 42)]
    assert out["assets"][0]["destination"] == "media/audio/track.mp3"
    assert out["assets"][1]["destination"] == "media/video/clip.mp4"
    assert out["uploads"]["video"]["artifact_ref"] == "s3://rendered/renders/job-1/output.mp4"
    assert out["uploads"]["manifest"]["artifact_ref"] == "s3://rendered/renders/job-1/rust-gen/output-manifest.json"
    assert len(uploads) == 4
