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
    DynamicWindowConfig,
    ERROR_INTERNAL,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_TIMEOUT,
    ERROR_WINDOW_MISMATCH,
    align_target_fragment,
)
from .contracts import (
    ERROR_PRONUNCIATION_UNAVAILABLE,
    ERROR_SEPARATOR_UNAVAILABLE,
)
from .pronunciation import (
    PRONUNCIATION_MODE,
    EspeakEnglishToRussianNormalizer,
    load_pronunciation_overrides,
)
from .separation import DemucsVocalSeparator


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
    ffmpeg_timeout_s: float
    timeout_s: float
    padding_left_sec: float
    padding_right_sec: float
    min_word_confidence: float
    pause_min_gap_sec: float
    dynamic_window_max_adjust_sec: float
    dynamic_window_step_sec: float
    dynamic_window_min_edge_clearance_sec: float
    dynamic_window_stability_tolerance_sec: float
    dynamic_window_min_consensus_candidates: int
    dynamic_window_score_tolerance: float
    dynamic_window_min_boundary_duration_ratio: float
    max_window_sec: float
    max_reference_words: int
    torch_threads: int
    audio_preprocessor: str
    demucs_model_repo: Path
    demucs_model_name: str
    demucs_model_revision: str
    demucs_package_version: str
    demucs_segment_sec: float
    demucs_overlap: float
    pronunciation_mode: str
    espeak_bin: str
    espeak_voice: str
    espeak_expected_version: str
    espeak_timeout_s: float
    pronunciation_overrides_path: Path

    @classmethod
    def from_env(cls) -> "AlignmentSettings":
        return cls(
            model_path=Path(_env("ALIGNMENT_MODEL_PATH", "/opt/models/ru")).resolve(),
            model_revision=_env("ALIGNMENT_MODEL_REVISION"),
            allowed_audio_root=Path(
                _env("ALIGNMENT_ALLOWED_AUDIO_ROOT", "/app/work/jobs")
            ).resolve(),
            ffmpeg_bin=_env("ALIGNMENT_FFMPEG_BIN", "ffmpeg"),
            ffmpeg_timeout_s=_float_env("ALIGNMENT_FFMPEG_TIMEOUT_S", 120.0),
            timeout_s=_float_env("ALIGNMENT_TIMEOUT_S", 600.0),
            padding_left_sec=_float_env("ALIGNMENT_PADDING_LEFT_SEC", 0.5),
            padding_right_sec=_float_env("ALIGNMENT_PADDING_RIGHT_SEC", 0.5),
            min_word_confidence=_float_env("ALIGNMENT_MIN_WORD_CONFIDENCE", 0.05),
            pause_min_gap_sec=_float_env("ALIGNMENT_PAUSE_MIN_GAP_SEC", 0.35),
            dynamic_window_max_adjust_sec=_float_env(
                "ALIGNMENT_DYNAMIC_WINDOW_MAX_ADJUST_SEC",
                1.0,
            ),
            dynamic_window_step_sec=_float_env(
                "ALIGNMENT_DYNAMIC_WINDOW_STEP_SEC",
                0.25,
            ),
            dynamic_window_min_edge_clearance_sec=_float_env(
                "ALIGNMENT_DYNAMIC_WINDOW_MIN_EDGE_CLEARANCE_SEC",
                0.12,
            ),
            dynamic_window_stability_tolerance_sec=_float_env(
                "ALIGNMENT_DYNAMIC_WINDOW_STABILITY_TOLERANCE_SEC",
                0.12,
            ),
            dynamic_window_min_consensus_candidates=_int_env(
                "ALIGNMENT_DYNAMIC_WINDOW_MIN_CONSENSUS_CANDIDATES",
                3,
            ),
            dynamic_window_score_tolerance=_float_env(
                "ALIGNMENT_DYNAMIC_WINDOW_SCORE_TOLERANCE",
                0.12,
            ),
            dynamic_window_min_boundary_duration_ratio=_float_env(
                "ALIGNMENT_DYNAMIC_WINDOW_MIN_BOUNDARY_DURATION_RATIO",
                0.15,
            ),
            max_window_sec=_float_env("ALIGNMENT_MAX_WINDOW_SEC", 120.0),
            max_reference_words=_int_env("ALIGNMENT_MAX_REFERENCE_WORDS", 400),
            torch_threads=max(1, _int_env("ALIGNMENT_TORCH_THREADS", 4)),
            audio_preprocessor=_env("ALIGNMENT_AUDIO_PREPROCESSOR", "demucs"),
            demucs_model_repo=Path(
                _env("ALIGNMENT_DEMUCS_MODEL_REPO", "/opt/models/demucs")
            ).resolve(),
            demucs_model_name=_env("ALIGNMENT_DEMUCS_MODEL_NAME", "htdemucs"),
            demucs_model_revision=_env("ALIGNMENT_DEMUCS_MODEL_REVISION"),
            demucs_package_version=_env(
                "ALIGNMENT_DEMUCS_PACKAGE_VERSION",
                "4.1.0",
            ),
            demucs_segment_sec=_float_env("ALIGNMENT_DEMUCS_SEGMENT_SEC", 7.0),
            demucs_overlap=_float_env("ALIGNMENT_DEMUCS_OVERLAP", 0.25),
            pronunciation_mode=_env("ALIGNMENT_PRONUNCIATION_MODE"),
            espeak_bin=_env("ALIGNMENT_ESPEAK_BIN", "espeak-ng"),
            espeak_voice=_env("ALIGNMENT_ESPEAK_VOICE", "en-us"),
            espeak_expected_version=_env("ALIGNMENT_ESPEAK_EXPECTED_VERSION"),
            espeak_timeout_s=_float_env("ALIGNMENT_ESPEAK_TIMEOUT_S", 2.0),
            pronunciation_overrides_path=Path(
                _env(
                    "ALIGNMENT_PRONUNCIATION_OVERRIDES_PATH",
                    "/app/config/alignment_pronunciations.json",
                )
            ).resolve(),
        )


class AlignmentRuntime:
    def __init__(self, settings: AlignmentSettings):
        self.settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alignment")
        self._load_task: asyncio.Task[None] | None = None
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._vocal_separator: DemucsVocalSeparator | None = None
        self._pronunciation_normalizer: EspeakEnglishToRussianNormalizer | None = None
        self._load_error = ""
        self._load_error_code = ERROR_MODEL_UNAVAILABLE
        self._ready = False
        self._timed_out_jobs = 0

    @property
    def ready(self) -> bool:
        return bool(self._ready and self._timed_out_jobs == 0)

    @property
    def load_error(self) -> str:
        return str(self._load_error)

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "model_revision": self.settings.model_revision,
            "audio_preprocessor": self.settings.audio_preprocessor,
            "separator_model": self.settings.demucs_model_name,
            "separator_revision": self.settings.demucs_model_revision,
            "separator_package_version": self.settings.demucs_package_version,
            "pronunciation_mode": self.settings.pronunciation_mode,
            "pronunciation_engine_version": (
                self._pronunciation_normalizer.engine_version
                if self._pronunciation_normalizer is not None
                else ""
            ),
            "dynamic_window": {
                "max_adjust_sec": self.settings.dynamic_window_max_adjust_sec,
                "step_sec": self.settings.dynamic_window_step_sec,
                "min_edge_clearance_sec": (
                    self.settings.dynamic_window_min_edge_clearance_sec
                ),
                "stability_tolerance_sec": (
                    self.settings.dynamic_window_stability_tolerance_sec
                ),
                "min_consensus_candidates": (
                    self.settings.dynamic_window_min_consensus_candidates
                ),
                "score_tolerance": self.settings.dynamic_window_score_tolerance,
                "min_boundary_duration_ratio": (
                    self.settings.dynamic_window_min_boundary_duration_ratio
                ),
            },
            "load_error_code": self._load_error_code if self.load_error else "",
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
            DynamicWindowConfig(
                max_adjust_sec=self.settings.dynamic_window_max_adjust_sec,
                step_sec=self.settings.dynamic_window_step_sec,
                min_edge_clearance_sec=(
                    self.settings.dynamic_window_min_edge_clearance_sec
                ),
                stability_tolerance_sec=(
                    self.settings.dynamic_window_stability_tolerance_sec
                ),
                min_consensus_candidates=(
                    self.settings.dynamic_window_min_consensus_candidates
                ),
                score_tolerance=self.settings.dynamic_window_score_tolerance,
                min_boundary_duration_ratio=(
                    self.settings.dynamic_window_min_boundary_duration_ratio
                ),
            ).validate()
            if not self.settings.model_revision:
                raise RuntimeError("ALIGNMENT_MODEL_REVISION is empty")
            if not self.settings.model_path.is_dir():
                raise RuntimeError(f"model directory is missing: {self.settings.model_path}")
            if self.settings.audio_preprocessor != "demucs":
                raise AlignmentFailure(
                    ERROR_SEPARATOR_UNAVAILABLE,
                    "ALIGNMENT_AUDIO_PREPROCESSOR must be explicitly set to 'demucs'",
                )
            if self.settings.pronunciation_mode != PRONUNCIATION_MODE:
                raise AlignmentFailure(
                    ERROR_PRONUNCIATION_UNAVAILABLE,
                    "ALIGNMENT_PRONUNCIATION_MODE must be explicitly set to "
                    f"{PRONUNCIATION_MODE!r}",
                )
            pronunciation_normalizer = EspeakEnglishToRussianNormalizer(
                espeak_bin=self.settings.espeak_bin,
                voice=self.settings.espeak_voice,
                expected_version=self.settings.espeak_expected_version,
                timeout_s=self.settings.espeak_timeout_s,
                overrides=load_pronunciation_overrides(
                    self.settings.pronunciation_overrides_path
                ),
            )
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
            vocal_separator = DemucsVocalSeparator(
                model_repo=self.settings.demucs_model_repo,
                model_name=self.settings.demucs_model_name,
                model_revision=self.settings.demucs_model_revision,
                package_version=self.settings.demucs_package_version,
                segment_sec=self.settings.demucs_segment_sec,
                overlap=self.settings.demucs_overlap,
            )
            self._processor = processor
            self._model = model
            self._torch = torch
            self._vocal_separator = vocal_separator
            self._pronunciation_normalizer = pronunciation_normalizer
            self._ready = True
            self._load_error_code = ""
            self._load_error = ""
        except AlignmentFailure as exc:
            self._ready = False
            self._load_error_code = exc.code
            self._load_error = exc.message
        except Exception as exc:
            self._ready = False
            self._load_error_code = ERROR_MODEL_UNAVAILABLE
            self._load_error = f"{type(exc).__name__}: {exc}"

    def _not_ready_failure(self) -> AlignmentFailure:
        return AlignmentFailure(
            self._load_error_code or ERROR_MODEL_UNAVAILABLE,
            self.load_error or "alignment model is not ready",
        )

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
            raise self._not_ready_failure()
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
        if self._vocal_separator is None:
            raise AlignmentFailure(
                ERROR_SEPARATOR_UNAVAILABLE,
                "Demucs separator is not loaded",
            )
        if self._pronunciation_normalizer is None:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "pronunciation normalizer is not loaded",
            )
        return align_target_fragment(
            audio_path=self.resolve_audio_path(audio_path),
            target_fragment=target_fragment,
            clip_start_abs=float(clip_start_abs),
            clip_end_abs=float(clip_end_abs),
            processor=self._processor,
            model=self._model,
            torch_module=self._torch,
            vocal_separator=self._vocal_separator,
            pronunciation_normalizer=self._pronunciation_normalizer,
            model_revision=self.settings.model_revision,
            ffmpeg_bin=self.settings.ffmpeg_bin,
            ffmpeg_timeout_s=self.settings.ffmpeg_timeout_s,
            padding_left_sec=self.settings.padding_left_sec,
            padding_right_sec=self.settings.padding_right_sec,
            min_word_confidence=self.settings.min_word_confidence,
            pause_min_gap_sec=self.settings.pause_min_gap_sec,
            dynamic_window_max_adjust_sec=(
                self.settings.dynamic_window_max_adjust_sec
            ),
            dynamic_window_step_sec=self.settings.dynamic_window_step_sec,
            dynamic_window_min_edge_clearance_sec=(
                self.settings.dynamic_window_min_edge_clearance_sec
            ),
            dynamic_window_stability_tolerance_sec=(
                self.settings.dynamic_window_stability_tolerance_sec
            ),
            dynamic_window_min_consensus_candidates=(
                self.settings.dynamic_window_min_consensus_candidates
            ),
            dynamic_window_score_tolerance=(
                self.settings.dynamic_window_score_tolerance
            ),
            dynamic_window_min_boundary_duration_ratio=(
                self.settings.dynamic_window_min_boundary_duration_ratio
            ),
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
            raise self._not_ready_failure()
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
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=float(self.settings.timeout_s),
            )
        except asyncio.TimeoutError as exc:
            self._timed_out_jobs += 1
            self._load_error_code = ERROR_TIMEOUT
            self._load_error = (
                f"inference exceeded {self.settings.timeout_s:.1f}s; "
                "runtime is unavailable until the worker finishes"
            )

            def _recover_after_timeout(_future: Any) -> None:
                self._timed_out_jobs = max(0, self._timed_out_jobs - 1)
                if self._ready and self._timed_out_jobs == 0:
                    self._load_error_code = ""
                    self._load_error = ""

            future.add_done_callback(_recover_after_timeout)
            raise AlignmentFailure(ERROR_TIMEOUT, self._load_error) from exc
