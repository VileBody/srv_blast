# mlcore/hooks/f5_cognition/smoke_tts.py
"""
Smoke-test: проверяем что gemini-3.1-flash-tts-preview доступна нашим
API-ключом через AI Studio и реально отдаёт аудио-байты.

Запуск:
    python -m mlcore.hooks.f5_cognition.smoke_tts
    python -m mlcore.hooks.f5_cognition.smoke_tts --text "Привет, мир" --voice Kore

Что делает:
  1. Грузит GEMINI_API_KEY из .env (или из окружения).
  2. Зовёт generate_content в AUDIO-модальности.
  3. Достаёт inline_data из ответа, оборачивает PCM в WAV, сохраняет.
  4. Печатает mime_type, размер, длительность.

Это диагностический скрипт, НЕ часть продакшн-pipeline.
"""
from __future__ import annotations

import argparse
import os
import sys
import wave
from pathlib import Path


def _load_env_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    # пробуем .env в корне репо
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("GEMINI_API_KEY not found in env or .env")


def _parse_audio_mime(mime: str) -> tuple[int, int]:
    """
    Из строки вида 'audio/L16;codec=pcm;rate=24000' достаём (rate, sample_width).
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mlcore.hooks.f5_cognition.smoke_tts")
    parser.add_argument("--text", default="А если он не вернётся?")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL_F5_TTS", "gemini-3.1-flash-tts-preview"))
    parser.add_argument("--voice", default="Kore", help="prebuilt voice name")
    parser.add_argument("--out", default="f5_smoke.wav")
    args = parser.parse_args(argv)

    from google import genai
    from google.genai import types

    api_key = _load_env_key()
    client = genai.Client(api_key=api_key)

    print(f"[smoke] model={args.model} voice={args.voice}")
    print(f"[smoke] text={args.text!r}")

    resp = client.models.generate_content(
        model=args.model,
        contents=args.text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=args.voice,
                    )
                )
            ),
        ),
    )

    # Достаём inline_data
    part = resp.candidates[0].content.parts[0]
    inline = getattr(part, "inline_data", None)
    if inline is None or not getattr(inline, "data", None):
        print("[smoke] FAIL: no inline audio data in response")
        print("[smoke] raw part:", part)
        return 2

    pcm: bytes = inline.data
    mime: str = getattr(inline, "mime_type", "") or ""
    rate, width = _parse_audio_mime(mime)

    out_path = Path(args.out).resolve()
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

    duration_s = len(pcm) / (rate * width)
    print(f"[smoke] OK mime={mime!r} bytes={len(pcm)} rate={rate} width={width}")
    print(f"[smoke] duration={duration_s:.2f}s saved={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
