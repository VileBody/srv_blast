from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from mlcore.photo_quality import analyze_photo_quality, normalize_photo_quality


def _write(path: Path, image: np.ndarray) -> Path:
    ok, encoded = cv2.imencode(path.suffix, image)
    assert ok
    encoded.tofile(str(path))
    return path


def test_small_photo_is_rejected_for_resolution(tmp_path: Path) -> None:
    image = np.random.default_rng(1).integers(0, 256, (300, 300, 3), dtype=np.uint8)
    result = analyze_photo_quality(_write(tmp_path / "small.png", image))
    assert result["reject"] is True
    assert "low_resolution" in result["reasons"]


def test_large_sharp_photo_passes(tmp_path: Path) -> None:
    image = np.zeros((900, 1200, 3), dtype=np.uint8)
    image[:, ::8] = 255
    image[::8, :] = 255
    result = analyze_photo_quality(_write(tmp_path / "sharp.png", image))
    assert result["reject"] is False
    assert result["width"] == 1200
    assert result["height"] == 900


def test_large_featureless_photo_is_rejected_as_severe_blur(tmp_path: Path) -> None:
    image = np.full((900, 1200, 3), 100, dtype=np.uint8)
    result = analyze_photo_quality(_write(tmp_path / "blur.png", image))
    assert result["reject"] is True
    assert "severe_blur" in result["reasons"]


def test_normalizer_drops_unknown_fields_and_deduplicates_reasons() -> None:
    result = normalize_photo_quality(
        {
            "width": "1200",
            "height": 900,
            "reject": True,
            "reasons": ["severe_blur", "severe_blur"],
            "unknown": "x",
        }
    )
    assert result["reasons"] == ["severe_blur"]
    assert "unknown" not in result
