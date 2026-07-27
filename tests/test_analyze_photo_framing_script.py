from __future__ import annotations

import json
from pathlib import Path

from scripts import analyze_photo_framing as script


class _FakeDetector:
    def __init__(self, _path: Path) -> None:
        pass


def test_enrich_snapshot_writes_new_file(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "123.jpg"
    image.write_bytes(b"image")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "video_key": "123.jpg",
                    "theme_tags": ["car"],
                    "people_type": "none",
                }
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "framed.json"
    monkeypatch.setattr(script, "OpenCvYoloXDetector", _FakeDetector)
    monkeypatch.setattr(
        script,
        "analyze_photo_framing",
        lambda *_args, **_kwargs: {"focus_x": 0.5, "focus_y": 0.7},
    )
    monkeypatch.setattr(script, "attach_photo_quality", lambda framing, _path: framing)
    summary = script.enrich_snapshot(
        snapshot_path=snapshot,
        images_dir=tmp_path,
        output_path=output,
        model_path=tmp_path / "model.onnx",
    )
    assert summary["analyzed"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))[0]["framing"]["focus_y"] == 0.7
