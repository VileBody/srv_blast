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

def test_cache_revision_keys_on_both_registry_and_tags() -> None:
    """A node reuses its hydrated picker cache while the revision marker matches.

    The marker used to key on footage_assets alone, but the picker also reads
    footage_tags (theme tags, and for photos the framing/quality backfill). A
    re-tag or a framing backfill therefore left every already-hydrated node
    serving a stale snapshot — the exact "node-local cache drifted from Postgres"
    failure this hydration was written to prevent.
    """
    import inspect

    from services.orchestrator.tasks import (
        _ensure_photo_picker_artifacts_from_registry,
        _ensure_video_picker_artifacts_from_registry,
    )

    for fn, source in (
        (_ensure_photo_picker_artifacts_from_registry, "photo"),
        (_ensure_video_picker_artifacts_from_registry, "video"),
    ):
        src = inspect.getsource(fn)
        assert "FROM footage_assets" in src, source
        assert "FROM footage_tags" in src, source
