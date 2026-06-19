from __future__ import annotations

import builtins
import shutil
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
wavfile = pytest.importorskip("scipy.io.wavfile")

from mlcore.audio_analysis import analyze_focus_clip


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg binary is required")
def test_analyze_focus_clip_does_not_import_librosa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sr = 22050
    duration_s = 8.0
    y = np.zeros(int(sr * duration_s), dtype=np.float32)
    click_len = int(0.025 * sr)
    click = np.hanning(click_len).astype(np.float32)
    for t in np.arange(0.0, duration_s, 0.5):
        idx = int(t * sr)
        end = min(y.size, idx + click_len)
        y[idx:end] += click[: end - idx]

    audio_path = tmp_path / "click.wav"
    wavfile.write(audio_path, sr, y)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "librosa" or name.startswith("librosa."):
            raise AssertionError("audio_analysis must not import librosa in prod path")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = analyze_focus_clip(
        audio_path=audio_path,
        clip_start_abs=0.0,
        clip_end_abs=duration_s,
    )

    assert 110.0 <= result.bpm <= 130.0
    assert result.onsets
    assert result.drop_candidates
