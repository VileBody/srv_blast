"""Нормализация исходников с сайта: крупное видео ужимается в рамку рендера.

Раньше 4K перекодировалось в 4K и загрузка с ПК упиралась в таймаут ffmpeg / nginx.
"""
from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture(autouse=True)
def _web_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # runtime.SETTINGS читается при импорте source_uploads — нужен минимальный мок-конфиг
    for key, value in {"MODE": "dev", "BLAST_BACKEND_MODE": "mock", "APP_URL": "http://localhost:5173",
                       "BLAST_SESSION_SECRET": "test-session-secret", "BLAST_CORS_ORIGINS": "http://localhost:5173",
                       "BLAST_PERSIST": "0", "BLAST_REQUIRE_AUTH": "0", "BLAST_CSRF": "0", "BLAST_RATE_LIMIT": "0"}.items():
        monkeypatch.setenv(key, value)


def _modules():
    from web_app.backend.app import media_uploads, source_uploads
    return media_uploads, source_uploads


def _make_video(path: Path, size: str, seconds: float = 1.2) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=10:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=d={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", "-y", str(path)], check=True)
    return path


@needs_ffmpeg
@pytest.mark.parametrize(
    ("size", "fmt", "expected"),
    [
        ("2160x3840", "9:16", (1080, 1920)),  # вертикальный 4K
        ("3840x2160", "16:9", (1920, 1080)),  # горизонтальный 4K
        ("720x1280", "9:16", (720, 1280)),    # меньше рамки — не апскейлим
    ],
)
def test_video_is_fit_into_render_box(tmp_path: Path, size: str, fmt: str, expected: tuple[int, int]) -> None:
    media_uploads, source_uploads = _modules()
    src = _make_video(tmp_path / "in.mp4", size)
    out, meta = media_uploads.normalize(src, tmp_path, video=True, expected_format=fmt)
    assert (meta["width"], meta["height"]) == expected
    assert meta["bytes"] == out.stat().st_size
    assert meta["format"] == fmt


@needs_ffmpeg
def test_phone_rotation_is_applied_before_fit(tmp_path: Path) -> None:
    media_uploads, source_uploads = _modules()
    # Телефон: кадр 3840x2160 + поворот 90° → зритель видит вертикаль.
    src = tmp_path / "in.mp4"
    _make_video(tmp_path / "raw.mp4", "3840x2160")
    subprocess.run(["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(tmp_path / "raw.mp4"),
                    "-c", "copy", "-y", str(src)], check=True)
    _, meta = media_uploads.normalize(src, tmp_path, video=True, expected_format="9:16")
    assert (meta["width"], meta["height"]) == (1080, 1920)


@needs_ffmpeg
def test_transcode_timeout_is_a_clear_user_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media_uploads, source_uploads = _modules()
    src = _make_video(tmp_path / "in.mp4", "720x1280")

    real_run = subprocess.run

    def slow_run(args, capture_output, timeout, check):  # noqa: ANN001
        if args[0] == "ffmpeg":
            raise subprocess.TimeoutExpired(args, timeout)
        return real_run(args, capture_output=capture_output, timeout=timeout, check=check)

    monkeypatch.setattr(media_uploads.subprocess, "run", slow_run)
    with pytest.raises(ValueError, match="1080p"):
        media_uploads.normalize(src, tmp_path, video=True, expected_format="9:16")


def test_oversized_body_is_rejected_while_spooling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media_uploads, source_uploads = _modules()
    monkeypatch.setattr(source_uploads, "MAX_SOURCE_BYTES", 10)
    upload = UploadFile(io.BytesIO(b"x" * 11), filename="big.mp4")
    with pytest.raises(ValueError, match="200 МБ"):
        source_uploads._spool_to_disk(upload, tmp_path / "input")


@needs_ffmpeg
def test_upload_streams_normalized_file_to_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media_uploads, source_uploads = _modules()
    src = _make_video(tmp_path / "in.mp4", "2160x3840")
    saved: dict = {}
    monkeypatch.setattr(source_uploads.upload_store, "reserve", lambda user_id: None)
    monkeypatch.setattr(source_uploads.upload_store, "release", lambda user_id: saved.setdefault("released", True))
    monkeypatch.setattr(source_uploads.upload_store, "save",
                        lambda user_id, project_id, kind, meta: saved.setdefault("asset", {**meta, "id": "src_1"}))
    monkeypatch.setattr(source_uploads, "SETTINGS", SimpleNamespace(backend="mock"))

    upload = UploadFile(io.BytesIO(src.read_bytes()), filename="clip.MOV")
    asset = asyncio.run(source_uploads.upload(upload, user_id="u1", project_id="p1", kind="source",
                                              format="9:16", local_dir=tmp_path / "out"))
    assert (asset["width"], asset["height"]) == (1080, 1920)
    assert asset["name"] == "clip.mp4"
    stored = list((tmp_path / "out").iterdir())
    assert len(stored) == 1 and stored[0].stat().st_size == asset["bytes"]
    assert "released" not in saved
