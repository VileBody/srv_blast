from __future__ import annotations

from services.orchestrator.tasks import _photo_registry_index_obj, _video_registry_index_obj


def test_photo_registry_rows_restore_technical_index() -> None:
    obj = _photo_registry_index_obj(
        [
            {
                "clip_id": "12345678",
                "file_name": "12345678.jpg",
                "genre": "visual",
                "tag": "nature_sunset_light_warm",
                "src_w": 1600,
                "src_h": 1200,
                "duration_sec": 1.5,
                "source": "photo",
            }
        ]
    )

    assert obj["media_type"] == "photo"
    assert obj["assets_count"] == 1
    assert obj["assets"][0] == {
        "file_name": "12345678.jpg",
        "genre": "visual",
        "tag": "nature_sunset_light_warm",
        "src_w": 1600,
        "src_h": 1200,
        "duration_sec": 1.5,
        "dominant_color": None,
        "s3_key": "",
    }


def test_video_registry_rows_restore_technical_index() -> None:
    obj = _video_registry_index_obj(
        [
            {
                "clip_id": "87654321",
                "s3_key": "videos/Rock/dark/87654321.mp4",
                "file_name": "87654321.mp4",
                "genre": "Rock",
                "tag": "dark",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 8.5,
                "source": "video",
            }
        ]
    )

    assert obj["media_type"] == "video"
    assert obj["assets_count"] == 1
    assert obj["assets"][0]["duration_sec"] == 8.5
    assert obj["assets"][0]["s3_key"] == "videos/Rock/dark/87654321.mp4"