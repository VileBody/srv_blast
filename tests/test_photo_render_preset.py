from __future__ import annotations

import json
from pathlib import Path

import pytest


_PHOTOS = [{"file_name": "a.jpg", "remote_url": "s3://bucket/a.jpg"}]


@pytest.mark.parametrize("preset_name", ["vertical", "wide", "square"])
def test_photo_project_stays_4_by_3_independent_of_render_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preset_name: str,
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
    assert (payload["photo_job"]["comp_w"], payload["photo_job"]["comp_h"]) == (1920, 1440)


def test_vertical_photo_request_does_not_override_photo_geometry(
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
    assert (payload["photo_job"]["comp_w"], payload["photo_job"]["comp_h"]) == (1920, 1440)
