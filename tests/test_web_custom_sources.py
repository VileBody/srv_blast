from __future__ import annotations

import pytest

from mlcore.custom_sources import apply_custom_sources
from web_app.backend.app.batch_geometry import selected_geometry


def test_custom_sources_preserve_user_order_and_cover_timeline() -> None:
    config = {
        "main_comp_w": 1080,
        "main_comp_h": 1920,
        "layers": [
            {"type": "footage", "layer_id": "library"},
            {"type": "audio_only", "layer_id": "audio_ref"},
        ],
    }
    apply_custom_sources(
        config,
        [
            {"url": "s3://assets/user/first.mp4", "width": 1080, "height": 1920, "duration": 4.0},
            {"url": "s3://assets/user/second.mp4", "width": 720, "height": 1280, "duration": 8.0},
        ],
        10.0,
    )

    footage = [layer for layer in config["layers"] if layer["type"] == "footage"]
    assert [layer["file_path"] for layer in footage] == [
        "s3://assets/user/first.mp4",
        "s3://assets/user/second.mp4",
    ]
    assert [(layer["in_point"], layer["out_point"]) for layer in footage] == [(0.0, 4.0), (4.0, 10.0)]
    assert config["layers"][-1]["layer_id"] == "audio_ref"


def test_custom_sources_fail_instead_of_looping_or_cropping() -> None:
    config = {"main_comp_w": 1920, "main_comp_h": 1080, "layers": []}
    with pytest.raises(ValueError, match="geometry"):
        apply_custom_sources(
            config,
            [{"url": "s3://assets/user/vertical.mp4", "width": 1080, "height": 1920, "duration": 20}],
            10,
        )
    with pytest.raises(ValueError, match="too short"):
        apply_custom_sources(
            config,
            [{"url": "s3://assets/user/wide.mp4", "width": 1920, "height": 1080, "duration": 3}],
            10,
        )


def test_batch_geometry_rejects_mixed_library_and_upload_plans() -> None:
    catalogs = {
        "footage": {
            "Вертикаль": {"selector": {"renderPreset": "vertical"}},
            "Кино": {"selector": {"renderPreset": "wide"}},
        },
        "photo": {"Портрет": {"selector": {"renderPreset": "vertical"}}},
    }
    with pytest.raises(ValueError, match="Один батч"):
        selected_geometry(
            {"background": {"footage": ["Вертикаль", "Кино"], "photo": [], "uploads": []}},
            catalogs,
        )
    with pytest.raises(ValueError, match="свои исходники или библиотеку"):
        selected_geometry(
            {"background": {"footage": ["Вертикаль"], "photo": [], "uploads": ["src-1"], "sourceFormat": "9:16"}},
            catalogs,
        )
    assert selected_geometry(
        {"background": {"footage": [], "photo": [], "uploads": ["src-1"], "sourceFormat": "16:9"}},
        catalogs,
    ) == "16:9"
