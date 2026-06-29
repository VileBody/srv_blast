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


def test_build_photo_project_emits_jsx_and_payload(tmp_path: Path) -> None:
    from app.project_builder import build_photo_project

    repo_root = Path(__file__).resolve().parents[1]
    out_json, out_jsx = build_photo_project(
        repo_root=repo_root, photos=_PHOTOS, out_dir=tmp_path,
        style="bw", transition="zoom",
    )
    assert out_json.exists() and out_jsx.exists()
    jsx = out_jsx.read_text(encoding="utf-8")
    assert "Photo Render" in jsx
    assert "1920" in jsx and "1440" in jsx
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["photo_job"]["style"] == "bw"
    assert payload["photo_job"]["transition"] == "zoom"
    assert payload["photo_job"]["config"]["grow"] == 10
