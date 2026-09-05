from __future__ import annotations

import pytest

from mlcore.custom_sources import apply_custom_sources
from web_app.backend.app.batch_geometry import selected_geometry
from web_app.backend.app.render_job import build_render_job


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


def test_batch_preserves_each_variations_own_geometry() -> None:
    catalogs = {
        "footage": {
            "Вертикаль": {"selector": {"renderPreset": "vertical"}},
            "Кино": {"selector": {"renderPreset": "wide"}},
        },
        "photo": {"Портрет": {"selector": {"renderPreset": "vertical"}}},
    }
    assert selected_geometry(
        {"background": {
            "footage": ["Вертикаль", "Кино"],
            "photo": ["Портрет"],
            "sourceVideos": [{"id": "mine", "format": "16:9", "sourceIds": ["src-1"]}],
        }},
        catalogs,
    ) == {"9:16", "16:9", "4:3"}


def test_batch_rejects_allocation_for_removed_background() -> None:
    with pytest.raises(ValueError, match="удалённый"):
        selected_geometry(
            {
                "background": {"footage": ["Вертикаль"], "photo": [], "sourceVideos": []},
                "allocation": {"background": {"footage:Удалён": 1}},
            },
            {"footage": {"Вертикаль": {"selector": {"renderPreset": "vertical"}}}, "photo": {}},
        )


def test_batch_rejects_unknown_catalog_geometry() -> None:
    with pytest.raises(ValueError, match="Неизвестная геометрия"):
        selected_geometry(
            {"background": {"footage": ["broken"], "photo": []}},
            {"footage": {"broken": {"selector": {"renderPreset": "portrait"}}}, "photo": {}},
        )


def test_legacy_upload_shape_keeps_its_geometry() -> None:
    assert selected_geometry(
        {"background": {"uploads": ["src-1"], "sourceFormat": "16:9"}},
        {"footage": {}, "photo": {}},
    ) == {"16:9"}


def test_personal_video_is_a_separate_pool_variation_with_its_own_order() -> None:
    assets = [
        {"id": "src-a", "s3Key": "s3://assets/a.mp4", "width": 1920, "height": 1080, "duration": 4.0},
        {"id": "src-b", "s3Key": "s3://assets/b.mp4", "width": 1920, "height": 1080, "duration": 8.0},
    ]
    stage = {
        "background": {
            "mode": "footage",
            "footage": ["Вертикаль"],
            "photo": ["Фото"],
            "sourceVideos": [{"id": "mine", "format": "16:9", "sourceIds": ["src-b", "src-a"]}],
            "sourceAssets": assets,
        },
        "subtitles": {"pool": ["Impulse"]},
        "hooks": {"configs": {}},
        "allocation": {
            "total": 3,
            "background": {"upload:mine": 1, "footage:Вертикаль": 1, "photo:Фото": 1},
            "subtitles": {"Impulse": 3},
            "hooks": {},
        },
        "track": {"s3Key": "s3://audio/track.wav", "durationS": 20},
        "timing": {"from": "00:00", "to": "00:10"},
        "lyrics": "line",
        "final": {},
    }

    job = build_render_job("batch", "project", "user", stage, 3)
    own, footage, photo = job["variations"]
    assert own["background"]["mode"] == "upload"
    assert own["background"]["sourceFormat"] == "16:9"
    assert [item["id"] for item in own["background"]["sourceAssets"]] == ["src-b", "src-a"]
    assert own["background"]["sourceLabel"] == "Своё видео 1 · 16:9"
    assert footage["background"]["mode"] == "footage"
    assert footage["background"]["sourceAssets"] == []
    assert photo["background"]["mode"] == "photo"


def test_personal_video_does_not_silently_drop_missing_source() -> None:
    stage = {
        "background": {
            "sourceVideos": [{"id": "mine", "format": "16:9", "sourceIds": ["missing"]}],
            "sourceAssets": [],
        },
        "subtitles": {"pool": ["Impulse"]},
        "hooks": {"configs": {}},
        "allocation": {"total": 1, "background": {"upload:mine": 1}, "subtitles": {"Impulse": 1}, "hooks": {}},
        "track": {"s3Key": "s3://audio/track.wav", "durationS": 20},
        "timing": {"from": "00:00", "to": "00:10"},
        "lyrics": "line",
        "final": {},
    }
    with pytest.raises(ValueError, match="отсутствующие исходники"):
        build_render_job("batch", "project", "user", stage, 1)


def test_personal_video_unknown_allocation_unit_fails_explicitly() -> None:
    stage = {
        "background": {
            "sourceVideos": [{"id": "mine", "format": "16:9", "sourceIds": ["src-1"]}],
            "sourceAssets": [{"id": "src-1", "s3Key": "s3://assets/a.mp4"}],
        },
        "subtitles": {"pool": ["Impulse"]},
        "hooks": {"configs": {}},
        "allocation": {"total": 1, "background": {"upload:deleted": 1}, "subtitles": {"Impulse": 1}, "hooks": {}},
        "track": {"s3Key": "s3://audio/track.wav", "durationS": 20},
        "timing": {"from": "00:00", "to": "00:10"},
        "lyrics": "line",
        "final": {},
    }
    with pytest.raises(ValueError, match="Неизвестное личное видео"):
        build_render_job("batch", "project", "user", stage, 1)
