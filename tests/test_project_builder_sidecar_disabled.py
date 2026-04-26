from __future__ import annotations

import json
from pathlib import Path

from app import project_builder


def test_adjustment_sidecar_is_not_inlined_for_botapi_color_grade(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_BOT", "botapi")
    monkeypatch.setenv("JOB_ID", "sidecar_disabled_test")
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
                "job_id": "sidecar_disabled_test",
                "color_grade": "cold",
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

    assert payload["adjustment_sidecar_source"] is None
    assert "var ADJUSTMENT_SIDECAR_SOURCE = null;" in jsx
    assert "apply_adjustment_effects_cold" not in jsx
    assert "S_Glow-0050" not in jsx
    assert "MB LookSuite3-0013" not in jsx
