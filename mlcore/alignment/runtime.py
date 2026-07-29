from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import (
    AlignmentFailure,
    AlignmentResult,
    ERROR_INTERNAL,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_TIMEOUT,
    ERROR_WINDOW_MISMATCH,
    align_target_fragment,
)


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _float_env(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


def _int_env(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class AlignmentSettings:
    model_path: Path
    model_revision: str
    allowed_audio_root: Path
    ffmpeg_bin: str
    timeout_s: float
    padding_left_sec: float
    padding_right_sec: float
    min_word_confidence: float
    pause_min_gap_sec: float
    max_window_sec: float
    max_reference_words: int
    torch_threads: int

    @classmethod
    def from_env(cls) -> "AlignmentSettings":
        return cls(
            model_path=Path(_env("ALIGNMENT_MODEL_PATH", "/opt/models/ru")).resolve(),
            model_revision=_env("ALIGNMENT_MODEL_REVISION"),
            allowed_audio_root=Path(
                _env("ALIGNMENT_ALLOWED_AUDIO_ROOT", "/app/work/jobs")
            ).resolve(),
            ffmpeg_bin=_env("ALIGNMENT_FFMPEG_BIN", "ffmpeg"),
            timeout_s=_float_env("ALIGNMENT_TIMEOUT_S", 600.0),
            padding_left_sec=_float_env("ALIGNMENT_PADDING_LEFT_SEC", 0.5),
            padding_right_sec=_float_env("ALIGNMENT_PADDING_RIGHT_SEC", 0.5),
            min_word_confidence=_float_env("ALIGNMENT_MIN_WORD_CONFIDENCE", 0.05),
            pause_min_gap_sec=_float_env("ALIGNMENT_PAUSE_MIN_GAP_SEC", 0.35),
            max_window_sec=_float_env("ALIGNMENT_MAX_WINDOW_SEC", 120.0),
            max_reference_words=_int_env("ALIGNMENT_MAX_REFERENCE_WORDS", 400),
            torch_threads=max(1, _int_env("ALIGNMENT_TORCH_THREADS", 4)),
        )


class AlignmentRuntime:
    def __init__(self, settings: AlignmentSettings):
        self.settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alignment")
        self._load_task: asyncio.Task[None] | None = None
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._soundfile: Any = None
        self._load_error = ""
        self._ready = False

    @property
    def ready(self) -> bool:
        return bool(self._ready)

    @property
    def load_error(self) -> str:
        return str(self._load_error)

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "model_revision": self.settings.model_revision,
            "load_error": self.load_error,
        }

    async def start(self) -> None:
        if self._load_task is None:
            loop = asyncio.get_running_loop()

            async def _load() -> None:
                await loop.run_in_executor(self._executor, self._load_model)

            self._load_task = asyncio.create_task(_load(), name="alignment-model-load")

    async def close(self) -> None:
        if self._load_task is not None and not self._load_task.done():
            self._load_task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _load_model(self) -> None:
        try:
            if not self.settings.model_revision:
                raise RuntimeError("ALIGNMENT_MODEL_REVISION is empty")
            if not self.settings.model_path.is_dir():
                raise RuntimeError(f"model directory is missing: {self.settings.model_path}")
            import soundfile
            import torch
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

            torch.set_num_threads(self.settings.torch_threads)
            processor = Wav2Vec2Processor.from_pretrained(
                str(self.settings.model_path),
                local_files_only=True,
            )
            model = Wav2Vec2ForCTC.from_pretrained(
                str(self.settings.model_path),
                local_files_only=True,
            )
            model.to("cpu")
            model.eval()
            self._processor = processor
            self._model = model
            self._torch = torch
            self._soundfile = soundfile
            self._ready = True
            self._load_error = ""
        except Exception as exc:
            self._ready = False
            self._load_error = f"{type(exc).__name__}: {exc}"

    def resolve_audio_path(self, raw_path: str) -> Path:
        candidate = Path(str(raw_path or "")).expanduser().resolve()
        root = self.settings.allowed_audio_root
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "audio_path is outside the allowed job root",
            ) from exc
        if not candidate.is_file():
            raise AlignmentFailure(ERROR_INTERNAL, "audio_path does not exist")
        return candidate

    def _align_sync(
        self,
        *,
        audio_path: str,
        target_fragment: str,
        clip_start_abs: float,
        clip_end_abs: float,
    ) -> AlignmentResult:
        if not self.ready:
            raise AlignmentFailure(
                ERROR_MODEL_UNAVAILABLE,
                self.load_error or "alignment model is not ready",
            )
        duration = float(clip_end_abs) - float(clip_start_abs)
        if duration <= 0.0 or duration > float(self.settings.max_window_sec):
            raise AlignmentFailure(
                ERROR_WINDOW_MISMATCH,
                f"window duration must be in (0, {self.settings.max_window_sec}]",
            )
        if len(str(target_fragment or "").split()) > int(self.settings.max_reference_words):
            raise AlignmentFailure(
                ERROR_WINDOW_MISMATCH,
                f"reference exceeds {self.settings.max_reference_words} words",
            )
        return align_target_fragment(
            audio_path=self.resolve_audio_path(audio_path),
            target_fragment=target_fragment,
            clip_start_abs=float(clip_start_abs),
            clip_end_abs=float(clip_end_abs),
            processor=self._processor,
            model=self._model,
            torch_module=self._torch,
            soundfile_module=self._soundfile,
            model_revision=self.settings.model_revision,
            ffmpeg_bin=self.settings.ffmpeg_bin,
            padding_left_sec=self.settings.padding_left_sec,
            padding_right_sec=self.settings.padding_right_sec,
            min_word_confidence=self.settings.min_word_confidence,
            pause_min_gap_sec=self.settings.pause_min_gap_sec,
        )

    async def align(
        self,
        *,
        audio_path: str,
        target_fragment: str,
        clip_start_abs: float,
        clip_end_abs: float,
    ) -> AlignmentResult:
        if not self.ready:
            raise AlignmentFailure(
                ERROR_MODEL_UNAVAILABLE,
                self.load_error or "alignment model is not ready",
            )
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor,
            lambda: self._align_sync(
                audio_path=audio_path,
                target_fragment=target_fragment,
                clip_start_abs=clip_start_abs,
                clip_end_abs=clip_end_abs,
            ),
        )
        try:
            return await asyncio.wait_for(future, timeout=float(self.settings.timeout_s))
        except asyncio.TimeoutError as exc:
            raise AlignmentFailure(
                ERROR_TIMEOUT,
                f"inference exceeded {self.settings.timeout_s:.1f}s",
            ) from exc
