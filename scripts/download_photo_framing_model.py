#!/usr/bin/env python3
"""Download and verify the OpenCV Zoo YOLOX-S model used by photo framing."""
from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path


MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/47534e27c9851bb1128ccc0102f1145e27f23f98/models/"
    "object_detection_yolox/object_detection_yolox_2022nov.onnx"
)
MODEL_SHA256 = "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"
DEFAULT_PATH = Path("data/models/object_detection_yolox_2022nov.onnx")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(path: Path) -> Path:
    path = path.resolve()
    if path.exists() and _sha256(path) == MODEL_SHA256:
        print(f"photo framing model ready: {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="photo_framing_", suffix=".onnx", dir=path.parent, delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        urllib.request.urlretrieve(MODEL_URL, tmp_path)
        actual = _sha256(tmp_path)
        if actual != MODEL_SHA256:
            raise RuntimeError(
                f"photo framing model checksum mismatch: expected={MODEL_SHA256} actual={actual}"
            )
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    print(f"photo framing model downloaded: {path}")
    return path


if __name__ == "__main__":
    configured = os.environ.get("PHOTO_FRAMING_MODEL_PATH", "").strip()
    download_model(Path(configured) if configured else DEFAULT_PATH)
