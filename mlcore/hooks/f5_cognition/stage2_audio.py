# mlcore/hooks/f5_cognition/stage2_audio.py
"""
Stage 2 — синтез голоса через Gemini TTS.

Контракт:
  VoiceSpec → bytes (WAV/PCM, моно ≥24kHz)

Длина результата:
  < 1500 мс  → retry с пометкой «расширь» (макс N), потом F5TtsTooShort
  1500–4000  → ок
  > 4000 мс  → не ошибка, mixer обрежет с fade-out
"""
from __future__ import annotations

import io
import logging
import os

from pydub import AudioSegment

from mlcore.hooks.f5_cognition._gemini import (
    make_client,
    parse_audio_mime,
    pcm_to_wav_bytes,
)
from mlcore.hooks.f5_cognition.errors import F5GeminiTimeout, F5TtsTooShort
from mlcore.hooks.f5_cognition.models import VoiceSpec

logger = logging.getLogger(__name__)


TTS_MIN_ACCEPTABLE_MS = 1500
TTS_MAX_ACCEPTABLE_MS = 4000
MAX_TTS_RETRIES = 2

# Имя prebuilt-голоса Gemini TTS. Можно переопределить через env, когда
# свяжем voice_persona с конкретными голосами. Kore — нейтральный дефолт.
DEFAULT_TTS_VOICE = os.getenv("GEMINI_F5_TTS_VOICE", "Kore")


# ─────────────────────────────────────────────────────────────────────────────
# Промт для TTS-модели
# ─────────────────────────────────────────────────────────────────────────────

def build_voice_prompt(spec: VoiceSpec, *, retry_hint: str = "") -> str:
    hint = f"\nДополнительно: {retry_hint}" if retry_hint else ""
    return f"""\
Произнеси следующий текст голосом, описанным ниже.

Текст: "{spec.tts_text}"

Голос: {spec.voice_persona}
Эмоция: {spec.voice_emotion}
Темп: {spec.voice_pacing}

Инструкции:
- Многоточия (...) — паузы 200–400 мс.
- Восклицательные знаки — резкий акцент.
- Целевая длина: {spec.expected_duration_ms} мс.
- Не произноси кавычки, скобки, эмодзи.
- Голос должен звучать как живой человек (если только эмоция не "robotic").{hint}
"""


def _pacing_to_rate(pacing: str) -> float:
    return {
        "slow": 0.85,
        "normal": 1.0,
        "fast": 1.15,
        "staccato": 1.05,
        "rising": 1.0,
        "falling": 0.95,
    }.get(pacing, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini TTS-вызов
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini_tts(prompt: str, *, spec: VoiceSpec, model: str) -> bytes:
    """
    Реальный вызов Gemini TTS.

    Возвращает WAV-байты (PCM, обёрнутый в контейнер). Сырой ответ модели —
    inline PCM (mime вида 'audio/L16;codec=pcm;rate=24000'); оборачиваем в WAV,
    чтобы pydub/AE могли его читать.

    Модель по умолчанию (env GEMINI_MODEL_F5_TTS) — gemini-3.1-flash-tts-preview.
    Откат на gemini-2.5-flash-preview-tts = одна строка в .env, если 3.1 начнёт
    отдавать 500 INTERNAL / пустой контент.
    """
    from google.genai import types

    client = make_client()

    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=DEFAULT_TTS_VOICE,
                    )
                )
            ),
        ),
    )

    # Gemini may return HTTP 200 with NO audio: a candidate whose content is
    # None (blocked / non-STOP finish reason) or empty parts. Inspect why and
    # raise a retryable error with diagnostics instead of crashing on .parts.
    cands = getattr(resp, "candidates", None) or []
    if not cands:
        fb = getattr(resp, "prompt_feedback", None)
        raise F5GeminiTimeout(f"Gemini TTS returned no candidates (prompt_feedback={fb!r})")
    cand = cands[0]
    content = getattr(cand, "content", None)
    finish = getattr(cand, "finish_reason", None)
    if content is None or not getattr(content, "parts", None):
        fb = getattr(resp, "prompt_feedback", None)
        raise F5GeminiTimeout(
            f"Gemini TTS returned empty content (finish_reason={finish!r}, "
            f"prompt_feedback={fb!r})"
        )

    try:
        part = content.parts[0]
        inline = getattr(part, "inline_data", None)
        pcm = getattr(inline, "data", None) if inline is not None else None
    except (AttributeError, IndexError, TypeError) as e:
        raise F5GeminiTimeout(f"Gemini TTS returned malformed response: {e}") from e

    if not pcm:
        raise F5GeminiTimeout(
            f"Gemini TTS returned no inline audio data (finish_reason={finish!r})"
        )

    mime = getattr(inline, "mime_type", "") or ""
    rate, width = parse_audio_mime(mime)
    return pcm_to_wav_bytes(pcm, rate=rate, width=width, channels=1)


# ─────────────────────────────────────────────────────────────────────────────
# Главная точка входа
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_voice(spec: VoiceSpec) -> tuple[bytes, int]:
    """
    Возвращает (audio_bytes, actual_duration_ms).

    Делает до MAX_TTS_RETRIES попыток если TTS оказался короче 1.5с.
    Если все попытки короткие — поднимает F5TtsTooShort (вызывающий код
    может попробовать reverb extension в mixer).

    > 4с считается ОК — mixer обрежет с fade-out.
    """
    # Дефолт — 3.1 TTS (gemini-3.1-flash-tts-preview). Откат на 2.5
    # (gemini-2.5-flash-preview-tts) = смена env GEMINI_MODEL_F5_TTS, без правок кода.
    model = os.getenv("GEMINI_MODEL_F5_TTS", "gemini-3.1-flash-tts-preview")

    last_audio: bytes | None = None
    last_duration_ms: int = 0
    last_blocked_err: F5GeminiTimeout | None = None

    for attempt in range(MAX_TTS_RETRIES + 1):
        retry_hint = ""
        if attempt > 0:
            retry_hint = (
                "Предыдущая попытка вышла слишком короткой "
                f"({last_duration_ms} мс). Произнеси полнее, добавь выразительности, "
                "не ускоряй."
            )

        prompt = build_voice_prompt(spec, retry_hint=retry_hint)
        logger.info("f5.stage2 attempt=%d model=%s", attempt, model)

        # A blocked/empty TTS response (HTTP 200 but content=None) is retryable:
        # re-call rather than aborting F5 entirely. Keep the last error so we can
        # surface a clear reason if every attempt is blocked.
        try:
            audio_bytes = _call_gemini_tts(prompt, spec=spec, model=model)
        except F5GeminiTimeout as e:
            last_blocked_err = e
            logger.warning("f5.stage2 attempt=%d blocked/empty: %s", attempt, e)
            continue

        duration_ms = _measure_duration_ms(audio_bytes)

        logger.info("f5.stage2 attempt=%d duration_ms=%d", attempt, duration_ms)

        last_audio, last_duration_ms = audio_bytes, duration_ms

        if duration_ms >= TTS_MIN_ACCEPTABLE_MS:
            # Длиннее 4с не считаем ошибкой — mixer cut+fade.
            return audio_bytes, duration_ms

    # No usable audio after all attempts. If we never got ANY audio (every call
    # was blocked/empty), surface the block reason; otherwise it was too short.
    if last_audio is None:
        raise last_blocked_err or F5GeminiTimeout(
            f"Gemini TTS returned no audio after {MAX_TTS_RETRIES + 1} attempts"
        )
    raise F5TtsTooShort(
        f"TTS too short after {MAX_TTS_RETRIES + 1} attempts: "
        f"{last_duration_ms} ms < {TTS_MIN_ACCEPTABLE_MS} ms"
    )


def _measure_duration_ms(audio_bytes: bytes) -> int:
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
    return len(seg)
