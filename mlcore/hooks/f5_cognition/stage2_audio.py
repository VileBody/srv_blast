# mlcore/hooks/f5_cognition/stage2_audio.py
"""
Stage 2 — синтез голоса через выбранный TTS provider.

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
from mlcore.hooks.f5_cognition._openrouter import synthesize_pcm16
from mlcore.hooks.f5_cognition.errors import (
    F5GeminiTimeout,
    F5ProviderError,
    F5TtsTooShort,
)
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


def _provider() -> str:
    provider = (os.environ.get("F5_TTS_PROVIDER") or "gemini").strip().lower()
    if provider not in {"gemini", "openrouter"}:
        raise RuntimeError(
            f"unsupported F5_TTS_PROVIDER={provider!r}; expected gemini|openrouter"
        )
    return provider


def _model(provider: str) -> str:
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL_F5_TTS", "gemini-3.1-flash-tts-preview").strip()
    model = (os.environ.get("OPENROUTER_MODEL_F5_TTS") or "").strip()
    if not model:
        raise RuntimeError(
            "OPENROUTER_MODEL_F5_TTS is required when F5_TTS_PROVIDER=openrouter"
        )
    return model


def _call_tts(
    prompt: str,
    *,
    spec: VoiceSpec,
    provider: str,
    model: str,
) -> bytes:
    if provider == "gemini":
        return _call_gemini_tts(prompt, spec=spec, model=model)
    voice = (os.environ.get("OPENROUTER_F5_TTS_VOICE") or "alloy").strip()
    try:
        sample_rate = int(os.environ.get("OPENROUTER_F5_TTS_SAMPLE_RATE", "24000"))
    except ValueError as exc:
        raise RuntimeError("OPENROUTER_F5_TTS_SAMPLE_RATE must be an integer") from exc
    try:
        max_tokens = int(os.environ.get("OPENROUTER_F5_TTS_MAX_TOKENS", "96"))
        max_stream_seconds = float(
            os.environ.get("OPENROUTER_F5_TTS_MAX_STREAM_SECONDS", "6")
        )
    except ValueError as exc:
        raise RuntimeError(
            "OPENROUTER_F5_TTS_MAX_TOKENS and MAX_STREAM_SECONDS must be numeric"
        ) from exc
    return synthesize_pcm16(
        prompt,
        model=model,
        voice=voice,
        sample_rate=sample_rate,
        max_tokens=max_tokens,
        max_stream_seconds=max_stream_seconds,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Главная точка входа
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_voice(spec: VoiceSpec) -> tuple[bytes, int]:
    """
    Возвращает (audio_bytes, actual_duration_ms).

    Делает до MAX_TTS_RETRIES попыток если TTS вне окна [1.5с, 4с]:
      - короче 1.5с → retry «произнеси полнее»; все короткие → F5TtsTooShort
        (вызывающий код может попробовать reverb extension в mixer).
      - длиннее 4с → retry «произнеси быстрее/короче» (иначе mixer режет фразу
        посреди предложения, а субтитр показывает невысказанный хвост). Если
        модель так и не уложилась — берём САМУЮ короткую из длинных попыток
        (минимум на отрез) и отдаём в mixer cut+fade.
    """
    provider = _provider()
    model = _model(provider)

    last_audio: bytes | None = None
    last_duration_ms: int = 0
    last_blocked_err: F5ProviderError | None = None
    best_long_audio: bytes | None = None   # shortest over-4s take (least to cut)
    best_long_ms: int = 0

    for attempt in range(MAX_TTS_RETRIES + 1):
        retry_hint = ""
        if attempt > 0 and 0 < last_duration_ms < TTS_MIN_ACCEPTABLE_MS:
            retry_hint = (
                "Предыдущая попытка вышла слишком короткой "
                f"({last_duration_ms} мс). Произнеси полнее, добавь выразительности, "
                "не ускоряй."
            )
        elif attempt > 0 and last_duration_ms > TTS_MAX_ACCEPTABLE_MS:
            retry_hint = (
                "Предыдущая попытка вышла слишком длинной "
                f"({last_duration_ms} мс). Произнеси заметно быстрее и компактнее, "
                f"уложись в {TTS_MAX_ACCEPTABLE_MS} мс, без длинных пауз между словами."
            )

        prompt = build_voice_prompt(spec, retry_hint=retry_hint)
        logger.info(
            "f5.stage2 attempt=%d provider=%s model=%s",
            attempt,
            provider,
            model,
        )

        # A blocked/empty TTS response (HTTP 200 but content=None) is retryable:
        # re-call rather than aborting F5 entirely. Keep the last error so we can
        # surface a clear reason if every attempt is blocked.
        try:
            audio_bytes = _call_tts(
                prompt,
                spec=spec,
                provider=provider,
                model=model,
            )
        except F5ProviderError as e:
            last_blocked_err = e
            logger.warning("f5.stage2 attempt=%d blocked/empty: %s", attempt, e)
            continue

        duration_ms = _measure_duration_ms(audio_bytes)

        logger.info("f5.stage2 attempt=%d duration_ms=%d", attempt, duration_ms)

        last_audio, last_duration_ms = audio_bytes, duration_ms

        if TTS_MIN_ACCEPTABLE_MS <= duration_ms <= TTS_MAX_ACCEPTABLE_MS:
            return audio_bytes, duration_ms
        if duration_ms > TTS_MAX_ACCEPTABLE_MS:
            # Over-long: keep the SHORTEST such take as a fallback, then retry
            # asking the voice to speak faster.
            if best_long_audio is None or duration_ms < best_long_ms:
                best_long_audio, best_long_ms = audio_bytes, duration_ms

    # Prefer the shortest over-long take (mixer cut+fade) over failing — the voice
    # never fit the window but at least we cut the least.
    if best_long_audio is not None:
        logger.warning(
            "f5.stage2 all attempts over %d ms; using shortest=%d ms (mixer cut+fade)",
            TTS_MAX_ACCEPTABLE_MS, best_long_ms,
        )
        return best_long_audio, best_long_ms

    # No usable audio after all attempts. If we never got ANY audio (every call
    # was blocked/empty), surface the block reason; otherwise it was too short.
    if last_audio is None:
        raise last_blocked_err or F5ProviderError(
            f"F5 TTS returned no audio after {MAX_TTS_RETRIES + 1} attempts"
        )
    raise F5TtsTooShort(
        f"TTS too short after {MAX_TTS_RETRIES + 1} attempts: "
        f"{last_duration_ms} ms < {TTS_MIN_ACCEPTABLE_MS} ms"
    )


def _measure_duration_ms(audio_bytes: bytes) -> int:
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
    return len(seg)
