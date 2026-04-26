from __future__ import annotations

import json
from pathlib import Path

from app import project_builder


def test_project_builder_does_not_emit_sidecar_or_uniqueness_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(project_builder, "build_footage_layers", lambda **_: [])
    monkeypatch.setattr(project_builder, "build_text_layers", lambda **_: [])

    full_edit_config = tmp_path / "full_edit.json"
    footage_config = tmp_path / "footage_config.json"
    out_dir = tmp_path / "out"

    full_edit_config.write_text(
        json.dumps(
            {
                "composition": {"dur": 2.0, "fps": 29.97},
                "subtitles_mode": "legacy_blocks",
            }
        ),
        encoding="utf-8",
    )
    footage_config.write_text(
        json.dumps(
            {
                "job_id": "release_no_sidecar_test",
                "color_grade": "cold",
                "allow_mirror": True,
                "layers": [],
            }
        ),
        encoding="utf-8",
    )

    out_json, out_jsx = project_builder.build_full_project(
        repo_root=Path.cwd(),
        full_edit_config_path=full_edit_config,
        footage_config_path=footage_config,
        out_dir=out_dir,
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    jsx = out_jsx.read_text(encoding="utf-8")

    assert "adjustment_sidecar_source" not in payload
    assert "uniqueness_pass_source" not in payload
    assert "ADJUSTMENT_SIDECAR_SOURCE" not in jsx
    assert "UNIQUENESS_PASS_SOURCE" not in jsx
    assert "apply_adjustment_effects" not in jsx
    assert "S_Glow" not in jsx
