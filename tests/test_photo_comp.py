from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.photo_comp import (
    DEFAULT_SEGMENT_FRAMES,
    PHOTO_COMP_H,
    PHOTO_COMP_W,
    build_photo_payload,
    build_photo_segments,
)
from services.orchestrator.render_manifest import collect_media_urls_from_render_payload

_PHOTOS = [
    {"file_name": "a.jpg", "remote_url": "https://s3/photo/a.jpg"},
    {"file_name": "b.jpg", "remote_url": "https://s3/photo/b.jpg"},
    {"file_name": "c.jpg", "remote_url": "s3://bucket/photo/c.jpg"},
]


def test_segments_are_sequential_and_back_to_back() -> None:
    segs = build_photo_segments(_PHOTOS, fps=24.0, segment_frames=36)
    assert len(segs) == 3
    assert segs[0]["in"] == 0.0
    # out of one == in of next (no gaps/overlaps)
    assert segs[0]["out"] == segs[1]["in"]
    assert segs[1]["out"] == segs[2]["in"]
    # 36 frames @ 24fps = 1.5s
    assert segs[0]["out"] == pytest.approx(1.5)
    assert [s["file_name"] for s in segs] == ["a.jpg", "b.jpg", "c.jpg"]


def test_payload_shape_and_geometry() -> None:
    pl = build_photo_payload(_PHOTOS, style="warm", transition="flash")
    job = pl["photo_job"]
    assert job["comp_w"] == PHOTO_COMP_W and job["comp_h"] == PHOTO_COMP_H
    assert job["style"] == "warm" and job["transition"] == "flash"
    assert len(job["segments"]) == 3
    assert pl["project"]["mediaType"] == "photo"
    assert pl["project"]["mainCompName"] == "Photo Render"
    # photo layers carry remote source for the render manifest to download
    assert len(pl["footage_layers"]) == 3
    src0 = pl["footage_layers"][0]["text_data"]["source_footage"]
    assert src0["file_name"] == "a.jpg" and src0["remote_url"].startswith("https://")


def test_payload_dedups_repeated_file_names() -> None:
    photos = _PHOTOS + [{"file_name": "a.jpg", "remote_url": "https://s3/photo/a.jpg"}]
    pl = build_photo_payload(photos)
    # 4 segments (one slot per pick) but 3 unique downloaded media entries
    assert len(pl["photo_job"]["segments"]) == 4
    assert len(pl["footage_layers"]) == 3


def test_night_vision_is_a_supported_full_frame_photo_style() -> None:
    pl = build_photo_payload(_PHOTOS, style="night_vision", transition="flash")
    assert pl["photo_job"]["style"] == "night_vision"

    template = (Path(__file__).resolve().parents[1] / "templates" / "photo_template.j2").read_text(encoding="utf-8")
    assert 'STYLE === "night_vision"' in template
    assert '"NIGHT_VISION_GREEN", COMP_W, COMP_H' in template
    assert 'property("ADBE Geometry2-0004").setValue(115)' in template
    assert '"FLASH", COMP_W, COMP_H' in template

def test_payload_rejects_empty_and_bad_enums() -> None:
    with pytest.raises(RuntimeError):
        build_photo_payload([])
    with pytest.raises(RuntimeError):
        build_photo_payload(_PHOTOS, style="neon_dreams")
    with pytest.raises(RuntimeError):
        build_photo_payload(_PHOTOS, transition="teleport")


def test_payload_rejects_photo_without_remote() -> None:
    with pytest.raises(RuntimeError):
        build_photo_payload([{"file_name": "x.jpg"}])


def test_render_manifest_collects_photos_into_media_video(tmp_path: Path) -> None:
    pl = build_photo_payload(_PHOTOS)
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(pl, ensure_ascii=False), encoding="utf-8")
    media = collect_media_urls_from_render_payload(p, audio_url="")
    rels = {m["relpath"] for m in media}
    assert rels == {"media/video/a.jpg", "media/video/b.jpg", "media/video/c.jpg"}


_FOOTAGE_CFG = {
    "layers": [
        {"type": "audio_only", "file_name": "audio_source.mp3", "in_point": 0.0, "out_point": 10.0},
        {"type": "footage", "file_name": "a.jpg", "file_path": "s3://b/photo/a.jpg", "in_point": 0.0, "out_point": 1.5},
        {"type": "footage", "file_name": "b.jpg", "file_path": "s3://b/photo/b.jpg", "in_point": 1.5, "out_point": 3.2},
        {"type": "overlay", "file_name": "ov.mp4", "file_path": "s3://b/ov.mp4", "in_point": 0.0, "out_point": 3.0},
        {"type": "footage", "file_name": "a.jpg", "file_path": "s3://b/photo/a.jpg", "in_point": 3.2, "out_point": 4.7},
    ]
}


def test_extract_photos_and_segments_from_footage_cfg() -> None:
    from app.photo_comp import extract_photos_and_segments_from_footage_cfg

    photos, segments = extract_photos_and_segments_from_footage_cfg(_FOOTAGE_CFG)
    # only type=footage layers; audio_only + overlay ignored
    assert [s["file_name"] for s in segments] == ["a.jpg", "b.jpg", "a.jpg"]
    assert segments[1]["in"] == 1.5 and segments[1]["out"] == 3.2
    # photos deduped, remote from file_path
    assert [p["file_name"] for p in photos] == ["a.jpg", "b.jpg"]
    assert photos[0]["remote_url"] == "s3://b/photo/a.jpg"


def test_extract_raises_without_footage_layers() -> None:
    with pytest.raises(RuntimeError):
        from app.photo_comp import extract_photos_and_segments_from_footage_cfg

        extract_photos_and_segments_from_footage_cfg({"layers": [{"type": "audio_only", "file_name": "a"}]})


def test_payload_uses_explicit_segments_from_stage2() -> None:
    photos, segments = (
        [{"file_name": "a.jpg", "remote_url": "s3://b/a.jpg"}, {"file_name": "b.jpg", "remote_url": "s3://b/b.jpg"}],
        [{"in": 0.0, "out": 1.5, "file_name": "a.jpg"}, {"in": 1.5, "out": 3.2, "file_name": "b.jpg"}],
    )
    pl = build_photo_payload(photos, style="cold", transition="slide", segments=segments)
    # explicit (stage2-aligned) timing is kept verbatim, not regenerated
    assert pl["photo_job"]["segments"] == segments
    assert pl["entry_comp"] == "Photo Render"


def test_schema_accepts_photo_bg_and_selections() -> None:
    from services.orchestrator.schemas import SendAudioS3Request

    req = SendAudioS3Request(
        audio_s3_url="https://x/a.mp3",
        bg_mode="photo",
        photo_style="night_vision",
        photo_transition="zoom",
    )
    assert req.bg_mode == "photo"
    assert req.photo_style == "night_vision" and req.photo_transition == "zoom"


def test_schema_rejects_bad_photo_selections() -> None:
    from pydantic import ValidationError

    from services.orchestrator.schemas import SendAudioS3Request

    with pytest.raises(ValidationError):
        SendAudioS3Request(audio_s3_url="https://x/a.mp3", bg_mode="photo", photo_style="neon")
    with pytest.raises(ValidationError):
        SendAudioS3Request(audio_s3_url="https://x/a.mp3", photo_transition="warp")


def test_build_photo_project_emits_jsx_and_payload(tmp_path: Path) -> None:
    from app.project_builder import build_photo_project

    repo_root = Path(__file__).resolve().parents[1]
    out_json, out_jsx = build_photo_project(
        repo_root=repo_root, photos=_PHOTOS, out_dir=tmp_path,
        style="bw", transition="zoom", audio_file_name="audio_source.mp3",
    )
    assert out_json.exists() and out_jsx.exists()
    jsx = out_jsx.read_text(encoding="utf-8")
    assert "Photo Render" in jsx
    assert "1920" in jsx and "1440" in jsx
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["photo_job"]["style"] == "bw"
    assert payload["photo_job"]["transition"] == "zoom"
    assert payload["photo_job"]["config"]["grow"] == 10
    assert payload["photo_job"]["audio"]["file_name"] == "audio_source.mp3"
    assert payload["footage_layers"][0]["file_name"] == "audio_source.mp3"
