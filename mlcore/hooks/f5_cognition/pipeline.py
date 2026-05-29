# mlcore/hooks/f5_cognition/pipeline.py
"""
F5 Cognition pipeline orchestrator.

Точка входа:
    generate(req: F5Request, *, output_path: str | None = None) -> F5Response
        → Возвращает ЧИСТЫЙ TTS .wav (после reverb-extend/cut+fade).
          В AE этот файл уйдёт как отдельный audio-слой (см. inject.py),
          трек микшируется AE-стороной.

    generate_preview(req, ...) -> F5Response
        → Старое поведение: TTS наложен поверх focal трека через mixer.py.
          Использовать только для smoke-test и превью в Telegram-боте
          (если когда-то понадобится показать «как звучит до рендера»).

Кэширование выключено (см. v1.3): каждый раз новая voice_persona.
"""
from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from pydub import AudioSegment

from mlcore.hooks.f5_cognition.errors import (
    F5FocalOutOfBounds,
    F5LyricsEmpty,
    F5TtsRetryExhausted,
    F5TtsTooShort,
)
from mlcore.hooks.f5_cognition.mixer import (
    MIN_OUTPUT_MS,
    TTS_MAX_OUTPUT_MS,
    cut_with_fade,
    extend_with_reverb,
    mix_overlay,
)
from mlcore.hooks.f5_cognition.models import F5Request, F5Response
from mlcore.hooks.f5_cognition.stage1_text import run_stage1
from mlcore.hooks.f5_cognition.stage2_audio import (
    TTS_MIN_ACCEPTABLE_MS,
    synthesize_voice,
)

logger = logging.getLogger(__name__)


MIN_LYRICS_WORDS = 5


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_request(req: F5Request) -> None:
    if not req.lyrics or len(req.lyrics.split()) < MIN_LYRICS_WORDS:
        raise F5LyricsEmpty(
            f"lyrics must contain at least {MIN_LYRICS_WORDS} words"
        )

    track_ms = len(AudioSegment.from_file(req.track_path))
    if req.focal_start_ms + MIN_OUTPUT_MS > track_ms:
        raise F5FocalOutOfBounds(
            f"focal_start_ms={req.focal_start_ms} + {MIN_OUTPUT_MS} > "
            f"track length {track_ms}"
        )


def _default_output_path(req: F5Request, *, suffix: str = "") -> str:
    tmp = Path(tempfile.gettempdir()) / "blast_f5"
    tmp.mkdir(parents=True, exist_ok=True)
    stem = Path(req.track_path).stem
    tag = f"_{suffix}" if suffix else ""
    return str(tmp / f"{stem}_f5_{req.device.value}_{req.focal_start_ms}{tag}.wav")


# ─────────────────────────────────────────────────────────────────────────────
# Чистый TTS .wav — основной кейс (AE-сторона микширует)
# ─────────────────────────────────────────────────────────────────────────────

def _post_process_tts(
    tts_bytes: bytes, *, tts_duration_ms: int,
) -> tuple[AudioSegment, bool]:
    """
    Применяет к сырому TTS:
      - reverb tail extension, если длина < MIN_OUTPUT_MS (~3с) — закрывает focal-окно
      - cut + fade-out, если длина > TTS_MAX_OUTPUT_MS (4с)
    Возвращает (segment, extended_via_reverb).
    """
    seg = AudioSegment.from_file(io.BytesIO(tts_bytes))
    extended = False

    if len(seg) < MIN_OUTPUT_MS:
        seg = extend_with_reverb(seg, MIN_OUTPUT_MS)
        extended = True

    if len(seg) > TTS_MAX_OUTPUT_MS:
        seg = cut_with_fade(seg, TTS_MAX_OUTPUT_MS)

    return seg, extended


def generate(req: F5Request, *, output_path: str | None = None) -> F5Response:
    """
    Главная точка входа. Возвращает чистый TTS-файл, готовый к загрузке
    в S3 и подключению в AE как отдельный audio-слой.
    """
    _validate_request(req)
    output_path = output_path or _default_output_path(req)

    # ── Stage 1: текст + voice spec
    spec = run_stage1(req)

    # ── Stage 2: TTS synth
    try:
        tts_bytes, tts_duration_ms = synthesize_voice(spec)
    except F5TtsTooShort as e:
        # Stage 2 исчерпал retry. Сейчас Stage 2 при short бросает исключение
        # без возврата bytes. TODO: вернуть последние bytes наружу чтобы здесь
        # можно было применить reverb-extend как последний шанс.
        logger.warning("f5.pipeline TTS retry exhausted: %s", e)
        raise F5TtsRetryExhausted(str(e)) from e

    # ── Post-process: reverb-extend / cut+fade
    seg, extended = _post_process_tts(tts_bytes, tts_duration_ms=tts_duration_ms)
    final_duration_ms = len(seg)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    seg.export(output_path, format="wav")

    logger.info(
        "f5.pipeline done device=%s out=%s tts_ms=%d final_ms=%d extended=%s",
        req.device.value, output_path, tts_duration_ms, final_duration_ms, extended,
    )

    return F5Response(
        audio_path=output_path,
        audio_duration_ms=final_duration_ms,
        tts_text=spec.tts_text,
        voice_persona=spec.voice_persona,
        voice_emotion=spec.voice_emotion,
        voice_pacing=spec.voice_pacing,
        tts_duration_ms=tts_duration_ms,
        chosen_device=req.device,
        rationale=spec.rationale,
        extended_via_reverb=extended,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Preview-режим: overlay поверх focal трека (smoke-test / Telegram-превью)
# ─────────────────────────────────────────────────────────────────────────────

def generate_preview(req: F5Request, *, output_path: str | None = None) -> F5Response:
    """
    Альтернативный режим: TTS уже смикширован поверх focal трека.
    Использовать ТОЛЬКО для превью/smoke-test, не для финального видео.

    Финальное видео должно использовать generate() + inject.py + AE-mixing.
    """
    _validate_request(req)
    output_path = output_path or _default_output_path(req, suffix="preview")

    spec = run_stage1(req)

    try:
        tts_bytes, tts_duration_ms = synthesize_voice(spec)
    except F5TtsTooShort as e:
        raise F5TtsRetryExhausted(str(e)) from e

    audio_path, audio_duration_ms, extended = mix_overlay(
        track_path=req.track_path,
        focal_start_ms=req.focal_start_ms,
        tts_audio=tts_bytes,
        output_path=output_path,
    )

    return F5Response(
        audio_path=audio_path,
        audio_duration_ms=audio_duration_ms,
        tts_text=spec.tts_text,
        voice_persona=spec.voice_persona,
        voice_emotion=spec.voice_emotion,
        voice_pacing=spec.voice_pacing,
        tts_duration_ms=tts_duration_ms,
        chosen_device=req.device,
        rationale=spec.rationale,
        extended_via_reverb=extended,
    )
