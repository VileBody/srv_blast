# mlcore/hooks/f5_cognition/_gemini.py
"""
Тонкая обёртка над google.genai для F5.

Намеренно НЕ используем mlcore/gemini_client.py — он заточен под основной
ASR/сценарий-пайплайн (один настроенный клиент под одну модель). F5 зовёт
text- и TTS-модели напрямую, с разными модальностями.

Содержит:
  - load_api_key()        — GEMINI_API_KEY из env или .env в корне репо
  - make_client()         — google.genai.Client (AI Studio key, опц. proxy)
  - parse_audio_mime()    — (rate, sample_width) из 'audio/L16;rate=24000'
  - pcm_to_wav_bytes()    — оборачивает сырой PCM в контейнер WAV
"""
from __future__ import annotations

import io
import os
import wave
from pathlib import Path


def load_api_key() -> str:
    """GEMINI_API_KEY из окружения, иначе из .env в корне репо."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    # mlcore/hooks/f5_cognition/_gemini.py -> parents[3] == repo root
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("GEMINI_API_KEY not found in env or .env")


def make_client():
    """
    Создаёт google.genai.Client под AI Studio key.

    Импорт genai ленивый — чтобы модуль F5 не падал на импорте, когда SDK
    отсутствует (например в окружении без ML-зависимостей).
    """
    from google import genai

    api_key = load_api_key()

    http_options = None
    proxy = (os.environ.get("OUTBOUND_PROXY") or "").strip()
    if proxy:
        # genai прокидывает proxy через http_options.client_args (httpx)
        from google.genai import types as _types

        http_options = _types.HttpOptions(client_args={"proxy": proxy})

    if http_options is not None:
        return genai.Client(api_key=api_key, http_options=http_options)
    return genai.Client(api_key=api_key)


def parse_audio_mime(mime: str) -> tuple[int, int]:
    """
    Из 'audio/L16;codec=pcm;rate=24000' достаём (rate, sample_width_bytes).
    L16 → 16 бит = 2 байта. Дефолт 24000/2.
    """
    rate = 24000
    width = 2
    for part in (mime or "").split(";"):
        part = part.strip().lower()
        if part.startswith("rate="):
            try:
                rate = int(part.split("=", 1)[1])
            except ValueError:
                pass
        if part.startswith("audio/l"):
            try:
                bits = int(part.split("audio/l", 1)[1])
                width = max(1, bits // 8)
            except ValueError:
                pass
    return rate, width


def pcm_to_wav_bytes(pcm: bytes, *, rate: int, width: int, channels: int = 1) -> bytes:
    """Оборачивает сырой PCM в WAV-контейнер (in-memory)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()
