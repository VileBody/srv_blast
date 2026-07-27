from __future__ import annotations

from pathlib import Path

import pytest

from mlcore.photo_tagger import run_photo_framing_batch


def test_framing_backfill_updates_without_retagging(tmp_path: Path) -> None:
    updated = []
    progress = []

    def download(_bucket: str, _key: str, dest: Path) -> None:
        dest.write_bytes(b"photo")

    def analyze(_path: Path, **_kwargs):
        return {
            "version": "photo-framing-v1",
            "strategy": "fake",
            "focus_x": 0.4,
            "focus_y": 0.7,
            "confidence": 0.9,
        }

    result = run_photo_framing_batch(
        bucket="bucket",
        db_url="unused",
        fetch_fn=lambda: [
            {
                "clip_id": "photo:1",
                "file_name": "1.jpg",
                "s3_key": "photo_collection/a/1.jpg",
                "theme_tags": ["car"],
                "people_type": "none",
            }
        ],
        update_fn=lambda rows: updated.extend(rows) or len(rows),
        analyze_fn=analyze,
        download_fn=download,
        progress_cb=lambda *args: progress.append(args),
    )
    assert result == {
        "framing_pending": 1,
        "framing_written": 1,
        "framing_failed": 0,
        "framing_failure_reasons": {},
    }
    assert updated[0]["clip_id"] == "photo:1"
    assert updated[0]["framing"]["focus_y"] == 0.7
    assert progress == [(1, 1, 1)]


def test_framing_backfill_fails_if_every_asset_fails() -> None:
    with pytest.raises(RuntimeError, match="OSError: missing"):
        run_photo_framing_batch(
            bucket="bucket",
            db_url="unused",
            fetch_fn=lambda: [
                {
                    "clip_id": "photo:1",
                    "file_name": "1.jpg",
                    "s3_key": "missing.jpg",
                }
            ],
            update_fn=lambda _rows: 0,
            analyze_fn=lambda *_args, **_kwargs: {},
            download_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
        )


def test_existing_framing_gets_quality_without_running_detector(tmp_path: Path) -> None:
    updated = []

    def download(_bucket: str, _key: str, dest: Path) -> None:
        dest.write_bytes(b"photo")

    result = run_photo_framing_batch(
        bucket="bucket",
        db_url="unused",
        fetch_fn=lambda: [
            {
                "clip_id": "photo:1",
                "file_name": "1.jpg",
                "s3_key": "photo_collection/a/1.jpg",
                "framing": {"strategy": "yolox", "focus_x": 0.4, "focus_y": 0.7},
            }
        ],
        update_fn=lambda rows: updated.extend(rows) or len(rows),
        analyze_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detector must not run")
        ),
        quality_fn=lambda framing, _path: {
            **framing,
            "quality": {"version": "photo-quality-v1", "reject": False},
        },
        download_fn=download,
    )

    assert result["framing_written"] == 1
    assert updated[0]["framing"]["strategy"] == "yolox"
    assert updated[0]["framing"]["quality"]["reject"] is False
