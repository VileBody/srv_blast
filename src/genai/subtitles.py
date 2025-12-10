from __future__ import annotations

import logging
from pathlib import Path

from google.genai import types

from .client_base import GenaiClientBase
from .prompts import SUBTITLES_SYSTEM

log = logging.getLogger(__name__)


class GeminiSubtitlesClient:
    """Обёртка Gemini только для сабов."""

    def __init__(self, client: GenaiClientBase):
        self.client = client

    def generate_srt_for_video(self, video_path: Path) -> str:
        file_obj = self.client.client.files.upload(file=str(video_path))
        log.info(
            "[generate_srt_for_video] Uploaded video %s (id=%s) for model %s",
            video_path,
            getattr(file_obj, "name", None),
            self.client.cfg.gemini_model_subtitles,
        )

        file_obj = self.client.wait_file_active(file_obj, "generate_srt_for_video")

        resp = self.client.client.models.generate_content(
            model=self.client.cfg.gemini_model_subtitles,
            contents=[SUBTITLES_SYSTEM, file_obj],
            config=types.GenerateContentConfig(
                temperature=0.3,
            ),
        )

        srt_text = getattr(resp, "output_text", None) or getattr(resp, "text", "")
        if not srt_text:
            try:
                for cand in getattr(resp, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in getattr(content, "parts", []) or []:
                        if getattr(part, "text", None):
                            srt_text = part.text
                            break
                    if srt_text:
                        break
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[generate_srt_for_video] Failed to inspect response parts: %s", exc
                )

        if not srt_text:
            log.error("[generate_srt_for_video] Empty SRT response: %r", resp)
            raise RuntimeError("Gemini вернул пустой ответ в generate_srt_for_video")

        log.info(
            "[generate_srt_for_video] Generated SRT, %d chars, %d lines",
            len(srt_text),
            srt_text.count("\n"),
        )
        return srt_text
