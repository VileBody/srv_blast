from __future__ import annotations

import sys
from pathlib import Path

import pytest

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "windows" / "render-node-runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from ae_sdk import (  # noqa: E402
    AeRenderer,
    MediaFileSpec,
    _strip_non_rgb_icc_profile_from_jpeg,
)


def _segment(marker: int, payload: bytes) -> bytes:
    size = len(payload) + 2
    return b"\xff" + bytes([marker]) + size.to_bytes(2, "big") + payload


def _icc_profile(color_space: bytes) -> bytes:
    assert len(color_space) == 4
    profile = bytearray(128)
    profile[12:16] = b"prtr"
    profile[16:20] = color_space
    profile[20:24] = b"XYZ "
    profile[36:40] = b"acsp"
    return bytes(profile)


def _jpeg_with_icc(color_space: bytes) -> bytes:
    app0 = _segment(0xE0, b"JFIF\x00" + b"\x00" * 9)
    icc = _segment(0xE2, b"ICC_PROFILE\x00" + b"\x01\x01" + _icc_profile(color_space))
    scan_and_eoi = b"\xff\xda\x00\x08" + b"\x01\x01\x00\x00\x3f\x00" + b"pixels\xff\xd9"
    return b"\xff\xd8" + app0 + icc + scan_and_eoi


def test_strip_non_rgb_icc_preserves_jpeg_pixel_stream(tmp_path: Path) -> None:
    path = tmp_path / "bad.jpg"
    original = _jpeg_with_icc(b"GRAY")
    scan = original[original.index(b"\xff\xda") :]
    path.write_bytes(original)

    assert _strip_non_rgb_icc_profile_from_jpeg(path) == "GRAY"

    sanitized = path.read_bytes()
    assert b"ICC_PROFILE\x00" not in sanitized
    assert sanitized[sanitized.index(b"\xff\xda") :] == scan
    assert sanitized.startswith(b"\xff\xd8")


def test_rgb_icc_is_left_byte_for_byte_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "rgb.jpeg"
    original = _jpeg_with_icc(b"RGB ")
    path.write_bytes(original)

    assert _strip_non_rgb_icc_profile_from_jpeg(path) == ""
    assert path.read_bytes() == original


def test_prepare_files_sanitizes_downloaded_media_jpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = AeRenderer(base_dir=tmp_path)
    original = _jpeg_with_icc(b"GRAY")

    def _download(_url: str, dest: Path) -> None:
        dest.write_bytes(original)

    monkeypatch.setattr(renderer, "_download_any", _download)
    monkeypatch.setattr(renderer, "_patch_project_paths", lambda *_: None)

    job_dir = tmp_path / "job"
    renderer._prepare_files(
        job_dir,
        job_dir / "render.jsx",
        "",
        [MediaFileSpec(url="s3://bucket/bad.jpg", relpath="media/video/bad.jpg")],
    )

    assert b"ICC_PROFILE\x00" not in (job_dir / "media" / "video" / "bad.jpg").read_bytes()
