from __future__ import annotations

import json
from pathlib import Path

import pytest


_PHOTOS = [{"file_name": "a.jpg", "remote_url": "s3://bucket/a.jpg"}]


@pytest.mark.parametrize(
    ("preset_name", "expected_size"),
    [
        ("vertical", (1080, 1920)),
        ("wide", (1920, 1080)),
        ("square", (1080, 1080)),
    ],
)
def test_photo_project_uses_the_requested_render_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preset_name: str,
    expected_size: tuple[int, int],
) -> None:
    from app.project_builder import build_photo_project

    monkeypatch.setenv("RENDER_PRESET", preset_name)
    repo_root = Path(__file__).resolve().parents[1]
    out_json, _ = build_photo_project(
        repo_root=repo_root,
        photos=_PHOTOS,
        out_dir=tmp_path,
        audio_file_name="audio_source.mp3",
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert (payload["photo_job"]["comp_w"], payload["photo_job"]["comp_h"]) == expected_size


def test_vertical_photo_request_cannot_silently_render_as_4_by_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.project_builder import build_photo_project

    monkeypatch.setenv("RENDER_PRESET", "vertical")
    repo_root = Path(__file__).resolve().parents[1]
    out_json, _ = build_photo_project(
        repo_root=repo_root,
        photos=_PHOTOS,
        out_dir=tmp_path,
        audio_file_name="audio_source.mp3",
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert (payload["photo_job"]["comp_w"], payload["photo_job"]["comp_h"]) == (1080, 1920)
    assert (payload["photo_job"]["comp_w"], payload["photo_job"]["comp_h"]) != (1920, 1440)
