from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.tg_bot_public import audio_prepare as ap


def test_run_ffmpeg_decodes_non_utf8_stderr_without_crashing(tmp_path: Path, monkeypatch) -> None:
    """ffmpeg may emit a Cyrillic filename (e.g. Неоновый_дождь.mp3) in stderr
    using a non-UTF-8 system locale. Decoding it must not raise UnicodeDecodeError;
    instead we surface a RuntimeError with the (replacement-decoded) stderr tail."""

    # 0xd1 alone is an invalid UTF-8 continuation byte -> would crash text=True.
    bad_stderr = b"Output file: \xd1\xe5\xee\xed\xee\xe2\xfb\xe9.mp3 error"

    def _fake_run(cmd, capture_output=False, **kwargs):  # noqa: ANN001
        assert "text" not in kwargs or kwargs["text"] is not True
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=bad_stderr)

    monkeypatch.setattr(ap.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError) as exc:
        ap._run_ffmpeg(
            ffmpeg_bin="ffmpeg",
            src=tmp_path / "Неоновый_дождь.mp3",
            dst=tmp_path / "out.mp3",
            bitrate="128k",
        )

    msg = str(exc.value)
    assert "ffmpeg failed" in msg
    assert "rc=1" in msg
    # Replacement char proves we decoded the invalid bytes instead of crashing.
    assert "�" in msg


def test_run_ffmpeg_success_does_not_touch_stderr(tmp_path: Path, monkeypatch) -> None:
    def _fake_run(cmd, capture_output=False, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"\xd1bad")

    monkeypatch.setattr(ap.subprocess, "run", _fake_run)

    # Returncode 0 -> no decode, no raise.
    ap._run_ffmpeg(
        ffmpeg_bin="ffmpeg",
        src=tmp_path / "a.mp3",
        dst=tmp_path / "b.mp3",
        bitrate="128k",
    )
