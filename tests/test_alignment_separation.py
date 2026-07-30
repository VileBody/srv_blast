from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from mlcore.alignment.contracts import (
    ERROR_SEPARATOR_UNAVAILABLE,
    ERROR_SOURCE_SEPARATION_FAILED,
)
from mlcore.alignment.core import AlignmentFailure, extract_analysis_crop
from mlcore.alignment.separation import DemucsVocalSeparator


class _FakeTensor:
    def __init__(self, values: np.ndarray):
        self._values = values

    def squeeze(self, _axis: int) -> "_FakeTensor":
        return _FakeTensor(np.squeeze(self._values, axis=0))

    def detach(self) -> "_FakeTensor":
        return self

    def cpu(self) -> "_FakeTensor":
        return self

    def float(self) -> "_FakeTensor":
        return self

    def numpy(self) -> np.ndarray:
        return self._values


class _FakeDemucs:
    samplerate = 44_100
    audio_channels = 2

    def separate_audio_file(self, _audio_path: Path):
        return object(), {"vocals": object()}


def _separator() -> DemucsVocalSeparator:
    separator = object.__new__(DemucsVocalSeparator)
    separator._separator = _FakeDemucs()
    separator._convert_audio = lambda *_args: _FakeTensor(
        np.ones((1, 16_000), dtype=np.float32)
    )
    separator.model_name = "htdemucs"
    separator.model_revision = "separator-revision"
    separator.package_version = "4.1.0"
    separator.segment_sec = 7.0
    separator.overlap = 0.25
    return separator


def test_demucs_separator_returns_mono_16khz_with_identity(tmp_path: Path) -> None:
    result = _separator().separate_vocals(tmp_path / "crop.wav")

    assert result.sample_rate == 16_000
    assert result.waveform.shape == (16_000,)
    assert result.diagnostics["separator_model"] == "htdemucs"
    assert result.diagnostics["separator_revision"] == "separator-revision"


def test_demucs_separator_has_no_mix_fallback(tmp_path: Path) -> None:
    separator = _separator()

    def fail(_audio_path: Path):
        raise RuntimeError("separation failed")

    separator._separator.separate_audio_file = fail
    with pytest.raises(AlignmentFailure) as exc:
        separator.separate_vocals(tmp_path / "crop.wav")

    assert exc.value.code == ERROR_SOURCE_SEPARATION_FAILED


def test_demucs_separator_requires_local_model_repo(tmp_path: Path) -> None:
    with pytest.raises(AlignmentFailure) as exc:
        DemucsVocalSeparator(
            model_repo=tmp_path / "missing",
            model_name="htdemucs",
            model_revision="separator-revision",
            package_version="4.1.0",
            segment_sec=7.0,
            overlap=0.25,
        )

    assert exc.value.code == ERROR_SEPARATOR_UNAVAILABLE


def test_analysis_crop_times_out_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "input.mp3"
    audio_path.write_bytes(b"audio")

    def time_out(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, timeout=0.01)

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(AlignmentFailure) as exc:
        extract_analysis_crop(
            ffmpeg_bin="ffmpeg",
            audio_path=audio_path,
            output_path=tmp_path / "crop.wav",
            clip_start_abs=10.0,
            clip_end_abs=20.0,
            padding_left_sec=0.5,
            padding_right_sec=0.5,
            timeout_s=0.01,
        )

    assert exc.value.code == "ALIGNMENT_TIMEOUT"


def test_analysis_crop_uses_demucs_audio_format(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "input.mp3"
    audio_path.write_bytes(b"audio")
    output_path = tmp_path / "crop.wav"
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    extract_analysis_crop(
        ffmpeg_bin="ffmpeg",
        audio_path=audio_path,
        output_path=output_path,
        clip_start_abs=10.0,
        clip_end_abs=20.0,
        padding_left_sec=0.5,
        padding_right_sec=0.5,
        sample_rate=44_100,
        channels=2,
    )

    assert commands
    command = commands[0]
    assert command[command.index("-ac") + 1] == "2"
    assert command[command.index("-ar") + 1] == "44100"
