from __future__ import annotations

import json
from pathlib import Path

from app.photo_comp import (
    build_photo_payload,
    extract_photos_and_segments_from_footage_cfg,
)
from mlcore.footage_bucket_previews import build_photo_montage_spec
from mlcore.footage_picker import (
    load_footage_style_metadata_rows,
    map_inventory_assets_with_style_metadata,
    merge_footage_style_metadata_rows,
)
from mlcore.models.footage_plan import FootageClipPick
from mlcore.photo_bucket_catalog import PHOTO_BUCKETS


FRAMING = {
    "version": "photo-framing-v1",
    "strategy": "yolox",
    "subject_class": "car",
    "subject_bbox": [0.1, 0.6, 0.9, 0.9],
    "focus_x": 0.5,
    "focus_y": 0.72,
    "confidence": 0.9,
}


def test_snapshot_inventory_and_clip_model_preserve_framing(tmp_path: Path) -> None:
    snapshot = tmp_path / "photo_tags.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "video_key": "12345678.jpg",
                    "video_path": "12345678.jpg",
                    "mood": "minor",
                    "color_tone": "dark",
                    "people_type": "none",
                    "theme_tags": ["car", "night drive"],
                    "framing": FRAMING,
                }
            ]
        ),
        encoding="utf-8",
    )
    rows = load_footage_style_metadata_rows(db_paths=[snapshot])
    merged = merge_footage_style_metadata_rows(rows)
    mapped, unmapped = map_inventory_assets_with_style_metadata(
        assets=[
            {
                "file_name": "12345678.jpg",
                "file_path": "s3://bucket/photo/12345678.jpg",
                "src_w": 1080,
                "src_h": 1920,
                "duration_sec": 999999,
            }
        ],
        metadata_index=merged,
    )
    assert unmapped == []
    assert mapped[0]["meta_framing"] == FRAMING

    clip = FootageClipPick.model_validate(
        {
            "file_name": "12345678.jpg",
            "framing": mapped[0]["meta_framing"],
            "in_point": 1.0,
            "out_point": 2.0,
            "start_time": 1.0,
        }
    )
    assert clip.framing == FRAMING


def test_footage_config_to_photo_job_preserves_framing() -> None:
    photos, segments = extract_photos_and_segments_from_footage_cfg(
        {
            "layers": [
                {
                    "type": "footage",
                    "file_name": "12345678.jpg",
                    "file_path": "s3://bucket/photo/12345678.jpg",
                    "in_point": 0.0,
                    "out_point": 1.5,
                    "framing": FRAMING,
                }
            ]
        }
    )
    payload = build_photo_payload(photos, segments=segments)
    assert payload["photo_job"]["segments"][0]["framing"] == FRAMING


def test_preview_spec_preserves_same_framing() -> None:
    bucket = PHOTO_BUCKETS[0]
    spec = build_photo_montage_spec(
        bucket,
        [{"file_name": "12345678.jpg", "meta_framing": FRAMING}],
    )
    assert spec["clips"][0]["framing"] == FRAMING
