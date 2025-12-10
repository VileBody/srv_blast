from __future__ import annotations

import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from config import Config
from .models import AudioSegmentPlan, VisualShotSpec, SegmentEditPlan
from .prompts import (
    DESCRIBE_VIDEO_SYSTEM,
    SELECT_AUDIO_HIGHLIGHTS_SYSTEM,
    PLAN_VISUALS_SYSTEM,
    SUBTITLES_SYSTEM,
    COMBINED_PLANNER_SYSTEM,
    AE_EDIT_PLAN_SYSTEM,
)

log = logging.getLogger(__name__)

# Корректный MIME для .m4a
mimetypes.add_type("audio/mp4", ".m4a")


# --------------------------------------------------------------------------- #
# Pydantic-модели под JSON-ответы (кроме AeEditPlan — его валидируем в planner)
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


class CombinedSegmentModel(BaseModel):
    index: int
    start_sec: float
    end_sec: float
    mood: Optional[str] = ""
    description: Optional[str] = ""
    shots: List[VisualShotModel]


class CombinedPlanModel(BaseModel):
    segments: List[CombinedSegmentModel]


class VideoOptionModel(BaseModel):
    file: str
    width: int
    height: int


class VideoResponseModel(BaseModel):
    response: dict
    options: List[VideoOptionModel]


# --------------------------------------------------------------------------- #
# Клиент Gemini
# --------------------------------------------------------------------------- #

class GeminiClient:
    def __init__(self, cfg: Config):
        """
        cfg.outbound_proxy:
          - None  -> ходим напрямую
          - строка (socks5h://host:port или http://...) -> используем как HTTP(S)-прокси
        """
        import os as _os
        if cfg.outbound_proxy:
            _os.environ["HTTPS_PROXY"] = cfg.outbound_proxy
            _os.environ["HTTP_PROXY"] = cfg.outbound_proxy
            log.info("GeminiClient: using outbound proxy %s", cfg.outbound_proxy)
        else:
            _os.environ.pop("HTTPS_PROXY", None)
            _os.environ.pop("HTTP_PROXY", None)
            log.info("GeminiClient: no outbound proxy")

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
        Аккуратно достаём текст/JSON из ответа SDK.
        """
        raw = getattr(resp, "output_text", None) or getattr(resp, "text", None)
        if not raw:
            try:
                for cand in getattr(resp, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in getattr(content, "parts", []) or []:
                        if getattr(part, "text", None):
                            raw = part.text
                            break
                    if raw:
                        break
            except Exception as e:
                log.warning("[%s] Failed to inspect response parts: %s", context, e)

        if not raw:
            log.error("[%s] Empty model output: %r", context, resp)
            raise RuntimeError(f"Empty model output in {context}")

        log.debug("[%s] Raw model output (truncated): %s", context, raw[:500])
        return raw

    # ---------------------------------------------------------------------- #
    # A) Старый комбинированный планировщик (3 сегмента + шоты)
    # ---------------------------------------------------------------------- #

    def build_full_plan(
        self,
        audio_path: Path,
        library_payload: list[dict],
    ) -> List[SegmentEditPlan]:
        file_obj = self.client.files.upload(file=str(audio_path))
        log.info(
            "[build_full_plan] Uploaded audio %s (id=%s) for model %s",
            audio_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        log.info(
            "[build_full_plan] Request config: model=%s, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[
                COMBINED_PLANNER_SYSTEM,
                json.dumps(library_payload, ensure_ascii=False),
                file_obj,
            ],
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        raw = self._extract_text_or_raise(resp, "build_full_plan")

        try:
            parsed = CombinedPlanModel.model_validate_json(raw)
        except ValidationError as e:
            log.error("[build_full_plan] Pydantic validation error: %s", e)
            log.debug("[build_full_plan] Raw JSON that failed: %s", raw)
            raise

        segment_plans: List[SegmentEditPlan] = []
        for s in parsed.segments:
            audio_seg = AudioSegmentPlan(
                index=s.index,
                start=s.start_sec,
                end=s.end_sec,
                mood=s.mood or "",
                description=s.description or "",
            )
            shots = [
                VisualShotSpec(
                    asset_prefix=shot.asset_prefix,
                    target_duration=float(shot.target_duration_sec),
                )
                for shot in s.shots
            ]
            segment_plans.append(SegmentEditPlan(audio_segment=audio_seg, shots=shots))

        segment_plans.sort(key=lambda sp: sp.audio_segment.index)
        log.info(
            "[build_full_plan] Built %d segments from combined planner: %s",
            len(segment_plans),
            [
                (sp.audio_segment.index, sp.audio_segment.start, sp.audio_segment.end)
                for sp in segment_plans
            ],
        )
        return segment_plans

    # ---------------------------------------------------------------------- #
    # B) План AeEditPlan (1 ролик + субтитры)
    # ---------------------------------------------------------------------- #

    def build_ae_edit_plan(
        self,
        audio_path: Path,
        library_payload: list[dict],
    ) -> dict:
        """
        Один запрос к модели по промпту AE_EDIT_PLAN_SYSTEM.

        Ожидаем JSON со структурой AeEditPlan:
          {
            "total_duration_sec": ...,
            "segments": [...],
            "subtitles": [...]
          }

        Здесь мы НЕ навешиваем свою дополнительную Pydantic-схему,
        а просто возвращаем json.loads(raw). Строгая валидация идёт
        позже в planner через ae_plan_models.AeEditPlan.
        """
        file_obj = self.client.files.upload(file=str(audio_path))
        log.info(
            "[build_ae_edit_plan] Uploaded audio %s (id=%s) for model %s",
            audio_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        log.info(
            "[build_ae_edit_plan] Request config: model=%s, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[
                AE_EDIT_PLAN_SYSTEM,
                json.dumps(library_payload, ensure_ascii=False),
                file_obj,
            ],
            config=types.GenerateContentConfig(
                temperature=0.6,
                response_mime_type="application/json",
            ),
        )

        raw = self._extract_text_or_raise(resp, "build_ae_edit_plan")

        try:
            data = json.loads(raw)
        except Exception as e:
            log.error("[build_ae_edit_plan] Failed to parse JSON: %s", e)
            log.debug("[build_ae_edit_plan] Raw JSON that failed: %s", raw)
            raise

        return data

    # ---------------------------------------------------------------------- #
    # C) Описание видео
    # ---------------------------------------------------------------------- #

    def describe_video(
        self,
        video_path: Path,
        variants_payload: list[dict],
    ) -> dict:
        mime_type, _ = mimetypes.guess_type(str(video_path))
        log.info(
            "[describe_video] Local guess mime_type for %s -> %r",
            video_path,
            mime_type,
        )

        file_obj = self.client.files.upload(file=str(video_path))
        log.info(
            "[describe_video] Uploaded video %s (id=%s) for model %s",
            video_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        file_obj = self._wait_file_active(file_obj, "describe_video")

        payload = json.dumps(variants_payload, ensure_ascii=False)

        log.info(
            "[describe_video] Request config: model=%s, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[
                DESCRIBE_VIDEO_SYSTEM,
                "Список доступных вариантов файла (верни их в поле 'options'): " + payload,
                file_obj,
            ],
            config=types.GenerateContentConfig(
                temperature=0.5,
                response_mime_type="application/json",
            ),
        )

        raw = self._extract_text_or_raise(resp, "describe_video")

        try:
            parsed = VideoResponseModel.model_validate_json(raw)
        except ValidationError as e:
            log.error("[describe_video] Pydantic validation error: %s", e)
            log.debug("[describe_video] Raw JSON that failed: %s", raw)
            raise

        return json.loads(parsed.model_dump_json(by_alias=True, exclude_none=True))

    # ---------------------------------------------------------------------- #
    # D) Старые функции (highlights + visuals)
    # ---------------------------------------------------------------------- #

    def select_audio_highlights(self, audio_path: Path) -> List[AudioSegmentPlan]:
        mime_type, _ = mimetypes.guess_type(str(audio_path))
        log.info(
            "[select_audio_highlights] Local guess mime_type for %s -> %r",
            audio_path,
            mime_type,
        )

        file_obj = self.client.files.upload(file=str(audio_path))
        log.info(
            "[select_audio_highlights] Uploaded audio %s (id=%s) for model %s",
            audio_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        log.info(
            "[select_audio_highlights] Request config: model=%s, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[SELECT_AUDIO_HIGHLIGHTS_SYSTEM, file_obj],
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
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

    def plan_visuals_for_segment(
        self,
        segment: AudioSegmentPlan,
        library_payload: list[dict],
    ) -> List[VisualShotSpec]:
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
            ),
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
    # E) Сабтайтлы как отдельный сервис
    # ---------------------------------------------------------------------- #

    def generate_srt_for_video(self, video_path: Path) -> str:
        file_obj = self.client.files.upload(file=str(video_path))
        log.info(
            "[generate_srt_for_video] Uploaded video %s (id=%s) for model %s",
            video_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_subtitles,
        )

        file_obj = self._wait_file_active(file_obj, "generate_srt_for_video")

        resp = self.client.models.generate_content(
            model=self.cfg.gemini_model_subtitles,
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
