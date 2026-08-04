"""Fast, deterministic quality gate for PHOTO assets.

The gate uses only OpenCV metrics computed while a photo is already downloaded
for tagging/framing. Thresholds are fixed and versioned; they were calibrated
on the current pool to reject roughly 10-15% without treating night, fog, or
soft light as defects by themselves.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Mapping


QUALITY_VERSION = "photo-quality-v1"
ANALYSIS_MAX_SIDE = 1024

MIN_SIDE_PX = 480
MIN_MEGAPIXELS = 0.30
MAX_BLUR_LAPLACIAN = 16.0
MAX_BLUR_EDGE_DENSITY = 0.008
MAX_CRUSHED_BLACK_FRACTION = 0.78
MAX_CRUSHED_BLACK_MEAN_LUMA = 16.0
MAX_BLOCKINESS = 2.7
MAX_BLOCKY_MEGAPIXELS = 0.70


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return result if math.isfinite(result) else default


def normalize_photo_quality(value: Any) -> Dict[str, Any]:
    """Validate and compact quality metadata read from a snapshot or DB."""
    if not isinstance(value, Mapping):
        return {}
    reasons = value.get("reasons")
    if not isinstance(reasons, (list, tuple)):
        reasons = []
    return {
        "version": str(value.get("version") or QUALITY_VERSION),
        "width": max(0, int(_finite_float(value.get("width")))),
        "height": max(0, int(_finite_float(value.get("height")))),
        "megapixels": round(max(0.0, _finite_float(value.get("megapixels"))), 4),
        "laplacian_var": round(max(0.0, _finite_float(value.get("laplacian_var"))), 3),
        "edge_density": round(max(0.0, min(1.0, _finite_float(value.get("edge_density")))), 6),
        "mean_luma": round(max(0.0, min(255.0, _finite_float(value.get("mean_luma")))), 3),
        "black_clip": round(max(0.0, min(1.0, _finite_float(value.get("black_clip")))), 6),
        "white_clip": round(max(0.0, min(1.0, _finite_float(value.get("white_clip")))), 6),
        "blockiness": round(max(0.0, _finite_float(value.get("blockiness"))), 3),
        "reject": bool(value.get("reject")),
        "reasons": list(dict.fromkeys(str(x) for x in reasons if str(x))),
    }


def _blockiness(gray: Any, np: Any) -> float:
    """Estimate JPEG 8x8 boundary discontinuities relative to inner pixels."""
    if min(gray.shape[:2]) <= 16:
        return 0.0
    values = gray.astype(np.float32)
    vertical = np.abs(np.diff(values, axis=1))
    horizontal = np.abs(np.diff(values, axis=0))
    v_boundaries = np.arange(7, vertical.shape[1], 8)
    h_boundaries = np.arange(7, horizontal.shape[0], 8)
    if not len(v_boundaries) or not len(h_boundaries):
        return 0.0
    v_boundary = float(vertical[:, v_boundaries].mean())
    h_boundary = float(horizontal[h_boundaries, :].mean())
    v_inside = float(np.delete(vertical, v_boundaries, axis=1).mean())
    h_inside = float(np.delete(horizontal, h_boundaries, axis=0).mean())
    return max(0.0, ((v_boundary - v_inside) + (h_boundary - h_inside)) / 2.0)


def analyze_photo_quality(path: str | Path) -> Dict[str, Any]:
    """Decode one image and return versioned quality metrics plus gate result."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "photo quality analysis requires opencv-python-headless and numpy"
        ) from exc

    # cv2.imread on Windows cannot reliably open non-ASCII paths.
    encoded = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"OpenCV could not read photo: {path}")
    height, width = image.shape[:2]
    scale = min(1.0, float(ANALYSIS_MAX_SIDE) / float(max(width, height)))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    megapixels = float(width * height) / 1_000_000.0
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    edge_density = float((cv2.Canny(gray, 60, 180) > 0).mean())
    mean_luma = float(gray.mean())
    black_clip = float((gray <= 8).mean())
    white_clip = float((gray >= 247).mean())
    blockiness = _blockiness(gray, np)

    reasons = []
    if min(width, height) < MIN_SIDE_PX or megapixels < MIN_MEGAPIXELS:
        reasons.append("low_resolution")
    if laplacian_var < MAX_BLUR_LAPLACIAN and edge_density < MAX_BLUR_EDGE_DENSITY:
        reasons.append("severe_blur")
    if (
        black_clip > MAX_CRUSHED_BLACK_FRACTION
        and mean_luma < MAX_CRUSHED_BLACK_MEAN_LUMA
    ):
        reasons.append("crushed_blacks")
    if blockiness > MAX_BLOCKINESS and megapixels < MAX_BLOCKY_MEGAPIXELS:
        reasons.append("compression_artifacts")

    return normalize_photo_quality(
        {
            "version": QUALITY_VERSION,
            "width": width,
            "height": height,
            "megapixels": megapixels,
            "laplacian_var": laplacian_var,
            "edge_density": edge_density,
            "mean_luma": mean_luma,
            "black_clip": black_clip,
            "white_clip": white_clip,
            "blockiness": blockiness,
            "reject": bool(reasons),
            "reasons": reasons,
        }
    )


def attach_photo_quality(
    framing: Mapping[str, Any] | None, path: str | Path
) -> Dict[str, Any]:
    """Attach quality metadata to the existing framing JSONB contract."""
    result = dict(framing or {})
    result["quality"] = analyze_photo_quality(path)
    return result
