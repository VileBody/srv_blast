from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import (
    ERROR_SEPARATOR_UNAVAILABLE,
    ERROR_SOURCE_SEPARATION_FAILED,
)
from .core import AlignmentFailure, SAMPLE_RATE


@dataclass(frozen=True)
class SeparationResult:
    waveform: np.ndarray
    sample_rate: int
    diagnostics: dict[str, Any]


class DemucsVocalSeparator:
    """One process-local, deterministic CPU Demucs instance."""

    def __init__(
        self,
        *,
        model_repo: Path,
        model_name: str,
        model_revision: str,
        package_version: str,
        segment_sec: float,
        overlap: float,
    ):
        if not model_name:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                "ALIGNMENT_DEMUCS_MODEL_NAME is empty",
            )
        if not model_revision:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                "ALIGNMENT_DEMUCS_MODEL_REVISION is empty",
            )
        if not model_repo.is_dir():
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                f"Demucs model repository is missing: {model_repo}",
            )
        model_files = sorted(model_repo.glob("*.th"))
        if not model_files:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                f"Demucs model repository has no weights: {model_repo}",
            )
        verified_revision = ""
        for model_file in model_files:
            digest = sha256()
            with model_file.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() == model_revision:
                verified_revision = digest.hexdigest()
                break
        if not verified_revision:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                "Demucs model checksum does not match "
                f"ALIGNMENT_DEMUCS_MODEL_REVISION={model_revision}",
            )
        try:
            from demucs.api import Separator
            from demucs.audio import convert_audio
        except Exception as exc:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                f"Demucs import failed: {type(exc).__name__}: {exc}",
            ) from exc

        installed_version = version("demucs")
        if installed_version != package_version:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                "Demucs package version mismatch: "
                f"expected={package_version} actual={installed_version}",
            )
        try:
            separator = Separator(
                model=model_name,
                repo=model_repo,
                device="cpu",
                shifts=0,
                overlap=float(overlap),
                split=True,
                segment=float(segment_sec),
                jobs=0,
                progress=False,
            )
        except Exception as exc:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                f"Demucs model load failed: {type(exc).__name__}: {exc}",
            ) from exc

        if "vocals" not in list(getattr(separator.model, "sources", [])):
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                f"Demucs model {model_name!r} has no vocals stem",
            )
        self._separator = separator
        self._convert_audio = convert_audio
        self.model_name = model_name
        self.model_revision = model_revision
        self.package_version = package_version
        self.segment_sec = float(segment_sec)
        self.overlap = float(overlap)

    @property
    def input_sample_rate(self) -> int:
        return int(self._separator.samplerate)

    @property
    def input_channels(self) -> int:
        return int(self._separator.audio_channels)

    def separate_vocals(self, audio_path: Path) -> SeparationResult:
        started = time.monotonic()
        try:
            _mix, stems = self._separator.separate_audio_file(Path(audio_path))
            vocals = stems.get("vocals")
            if vocals is None:
                raise RuntimeError("Demucs response has no vocals stem")
            mono = self._convert_audio(
                vocals,
                self.input_sample_rate,
                SAMPLE_RATE,
                1,
            )
            waveform = mono.squeeze(0).detach().cpu().float().numpy()
        except Exception as exc:
            raise AlignmentFailure(
                ERROR_SOURCE_SEPARATION_FAILED,
                f"Demucs inference failed: {type(exc).__name__}: {exc}",
            ) from exc

        waveform = np.asarray(waveform, dtype=np.float32)
        if waveform.ndim != 1 or waveform.size < SAMPLE_RATE // 10:
            raise AlignmentFailure(
                ERROR_SOURCE_SEPARATION_FAILED,
                "Demucs produced an invalid or empty vocals stem",
            )
        if not np.isfinite(waveform).all():
            raise AlignmentFailure(
                ERROR_SOURCE_SEPARATION_FAILED,
                "Demucs vocals stem contains non-finite samples",
            )
        rms = float(np.sqrt(np.mean(np.square(waveform, dtype=np.float64))))
        return SeparationResult(
            waveform=waveform,
            sample_rate=SAMPLE_RATE,
            diagnostics={
                "separator": "demucs",
                "separator_model": self.model_name,
                "separator_revision": self.model_revision,
                "separator_package_version": self.package_version,
                "separator_elapsed_sec": round(time.monotonic() - started, 6),
                "vocals_rms": rms,
            },
        )
