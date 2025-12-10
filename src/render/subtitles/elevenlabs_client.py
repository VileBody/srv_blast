from __future__ import annotations

import contextlib
import json
import logging
import wave
from pathlib import Path
from typing import List

import requests
from pydantic import BaseModel, ValidationError

from config import Config

log = logging.getLogger(__name__)


class STTWord(BaseModel):
    text: str
    start: float
    end: float


class STTResult(BaseModel):
    words: List[STTWord]


def _get_wav_duration_seconds(path: Path) -> float:
    """
    Узнаём длительность WAV-файла в секундах через стандартную либу wave.
    Нужен только для редкого фолбэка, если нет нормальных таймкодов.
    """
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return frames / float(rate)
    except Exception as e:
        log.warning("[elevenlabs] Failed to read WAV duration for %s: %s", path, e)
        return 0.0


class ElevenLabsClient:
    """
    Обёртка над ElevenLabs Speech-to-Text (Scribe v1).

    REST endpoint:
        POST https://api.elevenlabs.io/v1/speech-to-text

    По доке: https://elevenlabs.io/docs/capabilities/speech-to-text :contentReference[oaicite:1]{index=1}

    Пример ответа:

    {
      "language_code": "en",
      "language_probability": 1,
      "text": "...",
      "words": [
        { "text": "With", "start": 0.119, "end": 0.259, "type": "word", "speaker_id": "speaker_0" },
        { "text": " ",    "start": 0.239, "end": 0.299, "type": "spacing", "speaker_id": "speaker_0" },
        ...
      ],
      "transcription_id": "..."
    }

    Нас интересуют только элементы words с type == "word".
    """

    BASE_URL = "https://api.elevenlabs.io/v1/speech-to-text"

    def __init__(self, cfg: Config):
        self.api_key = cfg.eleven_api_key
        self.model_id = cfg.eleven_stt_model

    def transcribe_with_word_timestamps(self, audio_path: Path) -> List[STTWord]:
        """
        Отправляет аудио в ElevenLabs и возвращает список слов с таймкодами.

        Основной путь:
        - берем payload["words"] как список,
        - фильтруем только type == "word",
        - тащим text/start/end.

        Фолбэк:
        - если words нет или пустой, используем payload["text"], длительность WAV
          и размазываем слова по таймлайну равномерно.
        """
        log.info("[elevenlabs] Transcribing audio via STT: %s", audio_path)

        headers = {
            "xi-api-key": self.api_key,
        }

        files = {
            "file": (
                audio_path.name,
                audio_path.open("rb"),
                "audio/wav",
            )
        }

        data = {
            "model_id": self.model_id,
            "timestamps_granularity": "word",
        }

        resp = requests.post(
            self.BASE_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=600,
        )

        if resp.status_code != 200:
            log.error(
                "[elevenlabs] STT request failed: %s %s\nBody: %s",
                resp.status_code,
                resp.reason,
                resp.text[:1000],
            )
            resp.raise_for_status()

        try:
            payload = resp.json()
        except json.JSONDecodeError:
            log.error("[elevenlabs] STT response is not JSON: %s", resp.text[:1000])
            raise

        log.debug("[elevenlabs] Raw STT JSON (truncated): %s", json.dumps(payload)[:1000])

        # --- Основная ветка: words[] из доки ---
        words_raw = payload.get("words")
        normalized: list[dict] = []

        if isinstance(words_raw, list) and words_raw:
            log.info(
                "[elevenlabs] Found 'words' list in STT response (%d items)",
                len(words_raw),
            )
            for w in words_raw:
                if not isinstance(w, dict):
                    continue
                # по доке: "type": "word" | "spacing" | ...
                if w.get("type") != "word":
                    continue
                text = w.get("text")
                start = w.get("start")
                end = w.get("end")
                if text is None or start is None or end is None:
                    continue
                try:
                    normalized.append(
                        {
                            "text": str(text),
                            "start": float(start),
                            "end": float(end),
                        }
                    )
                except (TypeError, ValueError):
                    continue

        # --- Фолбэк: синтетические timestamps из plain text ---
        if not normalized:
            text_field = payload.get("text") or ""
            if not text_field.strip():
                log.error(
                    "[elevenlabs] No usable 'words' and no non-empty 'text' in STT response. Keys: %s",
                    list(payload.keys()),
                )
                raise RuntimeError("Unexpected ElevenLabs STT response format")

            log.warning(
                "[elevenlabs] No usable word timestamps found, "
                "falling back to synthetic timing from plain text"
            )

            duration = _get_wav_duration_seconds(audio_path)
            tokens = text_field.split()

            if duration <= 0:
                duration = max(0.5 * len(tokens), 1.0)

            if not tokens:
                raise RuntimeError("ElevenLabs STT text is empty after split")

            step = duration / max(len(tokens), 1)
            for i, tok in enumerate(tokens):
                start = i * step
                end = (i + 1) * step
                normalized.append(
                    {
                        "text": tok,
                        "start": start,
                        "end": end,
                    }
                )

            log.info(
                "[elevenlabs] Synthetic word timestamps generated: %d words, duration=%.2f sec, step=%.3f",
                len(tokens),
                duration,
                step,
            )

        if not normalized:
            log.error("[elevenlabs] No valid word entries after normalization")
            raise RuntimeError("No valid words in ElevenLabs STT response")

        try:
            parsed = STTResult(words=normalized)
        except ValidationError as e:
            log.error("[elevenlabs] Pydantic validation error: %s", e)
            log.debug(
                "[elevenlabs] Normalized words that failed: %s",
                json.dumps(normalized)[:1000],
            )
            raise

        log.info(
            "[elevenlabs] Parsed %d words (first: %r)",
            len(parsed.words),
            parsed.words[0] if parsed.words else None,
        )
        return parsed.words
