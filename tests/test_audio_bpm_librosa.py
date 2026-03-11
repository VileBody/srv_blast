from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("librosa")

from mlcore.audio_bpm import detect_bpm_librosa_from_signal


def test_detect_bpm_librosa_from_synthetic_click_track() -> None:
    sr = 22050
    bpm_target = 120.0
    duration_s = 20.0
    total = int(duration_s * sr)
    y = np.zeros(total, dtype=np.float32)

    beat_period = 60.0 / bpm_target
    click_len = int(0.03 * sr)
    click = np.hanning(click_len).astype(np.float32)
    t = 0.0
    while t < duration_s:
        idx = int(t * sr)
        end = min(total, idx + click_len)
        y[idx:end] += click[: end - idx]
        t += beat_period

    bpm = detect_bpm_librosa_from_signal(y=y, sr=sr)
    assert 116.0 <= bpm <= 124.0


def test_detect_bpm_accepts_numpy_tempo_array(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeBeat:
        @staticmethod
        def beat_track(*, y, sr):
            return np.array([128.5], dtype=np.float32), np.array([1, 2, 3], dtype=np.int64)

    class _FakeLibrosa:
        beat = _FakeBeat()

    monkeypatch.setattr("mlcore.audio_bpm._load_librosa", lambda: _FakeLibrosa())
    y = np.zeros(1024, dtype=np.float32)
    bpm = detect_bpm_librosa_from_signal(y=y, sr=22050)
    assert bpm == pytest.approx(128.5)
