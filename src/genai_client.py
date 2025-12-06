from __future__ import annotations

import json
import logging
import time
import mimetypes
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from config import Config
from .models import AudioSegmentPlan, VisualShotSpec
from .prompts import (
    DESCRIBE_VIDEO_SYSTEM,
    SELECT_AUDIO_HIGHLIGHTS_SYSTEM,
    PLAN_VISUALS_SYSTEM,
    SUBTITLES_SYSTEM,
)

log = logging.getLogger(__name__)

# Регистрация корректного MIME для .m4a,
# чтобы внутренняя логика google-genai смогла его нормально определить
mimetypes.add_type("audio/mp4", ".m4a")


# --------------------------------------------------------------------------- #
# Pydantic-модели под JSON-ответы
# --------------------------------------------------------------------------- #

class SegmentModel(BaseModel):
    index: int
    start_sec: float
    end_sec: float
    mood: Optional[str] = ""
    description: Optional[str] = ""


class AudioHighlightsModel(BaseModel):
    segments: List[SegmentModel]


class VisualShotModel(BaseModel):
    asset_prefix: str
    target_duration_sec: float


class VisualPlanModel(BaseModel):
    shots: List[VisualShotModel]


class VideoOptionModel(BaseModel):
    file: str
    width: int
    height: int


class VideoResponseModel(BaseModel):
    # формат:
    # {
    #   "response": {...},
    #   "options": [...]
    # }
    response: dict
    options: List[VideoOptionModel]


# --------------------------------------------------------------------------- #
# Клиент Gemini
# --------------------------------------------------------------------------- #

class GeminiClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = genai.Client(api_key=cfg.gemini_api_key)

    # ------------------------- утилиты ------------------------------------- #

    def _wait_file_active(
        self,
        file_obj,
        context: str,
        poll_interval: float = 2.0,
        max_wait: float = 600.0,
    ):
        """
        Ждём, пока file_obj.state.name станет 'ACTIVE'.
        Иначе Gemini для видео/аудио даёт FAILED_PRECONDITION.
        """
        start = time.time()
        name = getattr(file_obj, "name", None)

        while True:
            state = getattr(file_obj, "state", None)
            state_name = getattr(state, "name", None) if state else None

            if state_name == "ACTIVE":
                log.info("[%s] File %s is ACTIVE", context, name)
                return file_obj

            if time.time() - start > max_wait:
                log.error(
                    "[%s] File %s did not become ACTIVE within %.1f seconds (last state=%r)",
                    context,
                    name,
                    max_wait,
                    state_name,
                )
                raise RuntimeError(
                    f"File {name} did not become ACTIVE in {context}"
                )

            log.info(
                "[%s] Waiting for file %s to become ACTIVE (current state=%r)...",
                context,
                name,
                state_name,
            )
            time.sleep(poll_interval)
            file_obj = self.client.files.get(name=name)

    @staticmethod
    def _extract_text_or_raise(resp: types.GenerateContentResponse, context: str) -> str:
        """
        Аккуратно достаём текст из ответа SDK.

        - сначала output_text
        - потом text
        - потом candidates[*].content.parts[*].text
        Если ничего нет — логируем весь ответ и швыряем RuntimeError.
        """
        raw = getattr(resp, "output_text", None) or getattr(resp, "text", None)

        if not raw:
            try:
                as_dict = resp.to_dict() if hasattr(resp, "to_dict") else resp
            except Exception:
                as_dict = resp
            log.error("[%s] Empty response from Gemini. Full object: %r", context, as_dict)
            raise RuntimeError(f"Gemini вернул пустой ответ в {context}")

        log.debug("[%s] Raw model output (truncated): %s", context, raw[:500])
        return raw

    # ---------------------------------------------------------------------- #
    # 1) Описание одного видео (для автогенерации descriptions/*.json)
    # ---------------------------------------------------------------------- #

    def describe_video(self, video: Path, variants_payload: list[dict]) -> dict:
        """
        Проанализировать видео и вернуть JSON формата:
        {
          "response": {...},
          "options": [...]
        }

        Pydantic здесь только для валидации; наружу возвращаем dict.
        """
        file_obj = self.client.files.upload(file=str(video))
        log.info(
            "[describe_video] Uploaded video %s (id=%s) for model %s",
            video,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        # Видео может долго переходить в ACTIVE — подождём
        file_obj = self._wait_file_active(file_obj, "describe_video")

        payload = json.dumps(variants_payload, ensure_ascii=False)

        log.info(
            "[describe_video] Request config: model=%s, thinking_level=low, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[
                types.Part.from_text(DESCRIBE_VIDEO_SYSTEM),
                types.Part.from_text(
                    "Список доступных вариантов файла (верни их в поле 'options'): "
                    + payload
                ),
                file_obj,
            ],
            config=types.GenerateContentConfig(
                temperature=0.5,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
            ),
        )

        log.info("[describe_video] Got response from model %s", self.cfg.gemini_model_planning)

        raw = self._extract_text_or_raise(resp, "describe_video")

        try:
            parsed = VideoResponseModel.model_validate_json(raw)
        except ValidationError as e:
            log.error("[describe_video] Pydantic validation error: %s", e)
            log.debug("[describe_video] Raw JSON that failed: %s", raw)
            raise

        return json.loads(parsed.model_dump_json(by_alias=True, exclude_none=True))

    # ---------------------------------------------------------------------- #
    # 2) Выбор 3 хайлайтов аудио (10–20 секунд)
    # ---------------------------------------------------------------------- #

    def select_audio_highlights(self, audio_path: Path) -> List[AudioSegmentPlan]:
        """
        Просим модель выбрать 3 самых 'вайральных' фрагмента аудио
        длительностью 10–20 секунд.

        Ожидаемый JSON:
        {
          "segments": [
            {
              "index": 0,
              "start_sec": 12.3,
              "end_sec": 25.0,
              "mood": "...",
              "description": "..."
            },
            ...
          ]
        }
        """
        # Для дебага — посмотрим, что говорит mimetypes
        mime_type, _ = mimetypes.guess_type(str(audio_path))
        log.info(
            "[select_audio_highlights] Local guess mime_type for %s -> %r",
            audio_path,
            mime_type,
        )

        # Не передаём mime_type в SDK (у Python-метода его нет),
        # но благодаря add_type("audio/mp4", ".m4a") SDK теперь сможет сам корректно определить тип.
        file_obj = self.client.files.upload(file=str(audio_path))
        log.info(
            "[select_audio_highlights] Uploaded audio %s (id=%s) for model %s",
            audio_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        log.info(
            "[select_audio_highlights] Request config: model=%s, thinking_level=low, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[SELECT_AUDIO_HIGHLIGHTS_SYSTEM, file_obj],
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
            ),
        )

        usage = getattr(resp, "usage_metadata", None)
        log.info(
            "[select_audio_highlights] Got response from model %s, finish_reason=%s, total_tokens=%s, thoughts_tokens=%s",
            self.cfg.gemini_model_planning,
            getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else None,
            getattr(usage, "total_token_count", None),
            getattr(usage, "thoughts_token_count", None),
        )

        raw = self._extract_text_or_raise(resp, "select_audio_highlights")

        try:
            parsed = AudioHighlightsModel.model_validate_json(raw)
        except ValidationError as e:
            log.error("[select_audio_highlights] Pydantic validation error: %s", e)
            log.debug("[select_audio_highlights] Raw JSON that failed: %s", raw)
            raise

        segments: List[AudioSegmentPlan] = []
        for s in parsed.segments:
            seg = AudioSegmentPlan(
                index=s.index,
                start=s.start_sec,
                end=s.end_sec,
                mood=s.mood or "",
                description=s.description or "",
            )
            segments.append(seg)

        segments.sort(key=lambda x: x.index)
        log.info(
            "[select_audio_highlights] Parsed %d segments: %s",
            len(segments),
            [(s.index, s.start, s.end) for s in segments],
        )
        return segments

    # ---------------------------------------------------------------------- #
    # 3) Подбор визуалов под один аудиосегмент
    # ---------------------------------------------------------------------- #

    def plan_visuals_for_segment(
        self,
        segment: AudioSegmentPlan,
        library_payload: list[dict],
    ) -> List[VisualShotSpec]:
        """
        Разбиваем аудиосегмент на шоты 1.5–3 секунды и выбираем под каждый
        префикс клипа из библиотеки.

        Ожидаемый JSON:
        {
          "shots": [
            { "asset_prefix": "4503...", "target_duration_sec": 2.1 },
            ...
          ]
        }
        """
        payload = {
            "segment": {
                "start_sec": segment.start,
                "end_sec": segment.end,
                "duration_sec": segment.duration,
                "mood": segment.mood,
                "description": segment.description,
            },
            "library": library_payload,
        }

        log.info(
            "[plan_visuals_for_segment] Request for segment (%.2f–%.2f), duration %.2f, model=%s",
            segment.start,
            segment.end,
            segment.duration,
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[PLAN_VISUALS_SYSTEM, json.dumps(payload, ensure_ascii=False)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
            ),
        )

        log.info(
            "[plan_visuals_for_segment] Got response from model %s",
            self.cfg.gemini_model_planning,
        )

        raw = self._extract_text_or_raise(resp, "plan_visuals_for_segment")

        try:
            parsed = VisualPlanModel.model_validate_json(raw)
        except ValidationError as e:
            log.error("[plan_visuals_for_segment] Pydantic validation error: %s", e)
            log.debug("[plan_visuals_for_segment] Raw JSON that failed: %s", raw)
            raise

        shots = [
            VisualShotSpec(
                asset_prefix=s.asset_prefix,
                target_duration=float(s.target_duration_sec),
            )
            for s in parsed.shots
        ]
        log.info(
            "[plan_visuals_for_segment] Got %d shots, total duration ~%.2f sec",
            len(shots),
            sum(s.target_duration for s in shots),
        )
        return shots

    # ---------------------------------------------------------------------- #
    # 4) Генерация SRT для итогового видео (когда провайдер сабов = Gemini)
    # ---------------------------------------------------------------------- #

    def generate_srt_for_video(self, video_path: Path) -> str:
        """
        Просим модель сделать сабы в формате SRT (hh:mm:ss,ms).

        Здесь JSON не нужен — берём чистый текст.
        """
        file_obj = self.client.files.upload(file=str(video_path))
        log.info(
            "[generate_srt_for_video] Uploaded video %s (id=%s) for model %s",
            video_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_subtitles,
        )

        # ВАЖНО: ждём ACTIVE, иначе 400 FAILED_PRECONDITION
        file_obj = self._wait_file_active(file_obj, "generate_srt_for_video")

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_subtitles,
            contents=[SUBTITLES_SYSTEM, file_obj],
            config=types.GenerateContentConfig(
                temperature=0.3,
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
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
            except Exception as e:
                log.warning("[generate_srt_for_video] Failed to inspect response parts: %s", e)

        if not srt_text:
            log.error("[generate_srt_for_video] Empty SRT response: %r", resp)
            raise RuntimeError("Gemini вернул пустой ответ в generate_srt_for_video")

        log.info(
            "[generate_srt_for_video] Generated SRT, %d chars, %d lines",
            len(srt_text),
            srt_text.count("\n"),
        )
        return srt_text
