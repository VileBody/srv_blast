from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np


def _load_librosa():
    try:
        import librosa  # type: ignore

        return librosa
    except Exception as e:
        raise RuntimeError(
            "librosa is required for BPM detection. Install dependency and rebuild runtime image."
        ) from e


def detect_bpm_librosa_from_signal(*, y: "np.ndarray", sr: int) -> float:
    librosa = _load_librosa()
    try:
        import numpy as np  # type: ignore
    except Exception as e:
        raise RuntimeError("numpy is required for BPM detection input arrays.") from e
    if int(sr) <= 0:
        raise ValueError("sr must be > 0")
    if y.ndim != 1:
        raise ValueError("audio signal must be mono 1-D array")
    if y.size < 2:
        raise ValueError("audio signal is too short for BPM detection")

    tempo, _beats = librosa.beat.beat_track(y=y, sr=int(sr))
    if isinstance(tempo, (list, tuple)):
        if not tempo:
            raise RuntimeError("librosa returned empty tempo result")
        bpm = float(tempo[0])
    else:
        bpm = float(tempo)

    if bpm <= 0.0:
        raise RuntimeError(f"Invalid BPM detected by librosa: {bpm!r}")
    return bpm


def detect_bpm_librosa(
    *,
    audio_path: Path,
    clip_start_abs: float = 0.0,
    clip_end_abs: Optional[float] = None,
    target_sr: int = 22050,
) -> float:
    librosa = _load_librosa()
    p = Path(audio_path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"audio_path missing for BPM detection: {p}")

    if clip_start_abs < 0.0:
        raise ValueError("clip_start_abs must be >= 0")
    if clip_end_abs is not None and clip_end_abs <= clip_start_abs:
        raise ValueError("clip_end_abs must be > clip_start_abs")

    duration = None
    if clip_end_abs is not None:
        duration = float(clip_end_abs) - float(clip_start_abs)

    y, sr = librosa.load(
        str(p),
        sr=int(target_sr),
        mono=True,
        offset=float(clip_start_abs),
        duration=duration,
    )
    return detect_bpm_librosa_from_signal(y=y, sr=int(sr))
