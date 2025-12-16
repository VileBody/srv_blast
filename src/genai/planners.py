from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from google.genai import types
from pydantic import BaseModel, ValidationError

from .ae_composition_schema import AeComposition
from .json_schema_utils import strip_additional_properties

from src.core.models import AudioSegmentPlan, VisualShotSpec, SegmentEditPlan
from .client_base import GenaiClientBase
from .prompts import (
    AE_PROJECT_SYSTEM,
    COMBINED_PLANNER_SYSTEM,
    PLAN_VISUALS_SYSTEM,
    SELECT_AUDIO_HIGHLIGHTS_SYSTEM,
)

log = logging.getLogger(__name__)


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


class AePlanner:
    """Доменные операции Gemini для планирования роликов."""

    def __init__(self, client: GenaiClientBase):
        self.client = client
        self.cfg = client.cfg

    def build_full_plan(
        self, audio_path: Path, library_payload: list[dict]
    ) -> List[SegmentEditPlan]:
        file_obj = self.client.client.files.upload(file=str(audio_path))
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

        resp = self.client.client.models.generate_content(
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

        raw = self.client.extract_text_or_raise(resp, "build_full_plan")

        try:
            parsed = CombinedPlanModel.model_validate_json(raw)
        except ValidationError as exc:
            log.error("[build_full_plan] Pydantic validation error: %s", exc)
            log.debug("[build_full_plan] Raw JSON that failed: %s", raw)
            raise

        segment_plans: List[SegmentEditPlan] = []
        for seg in parsed.segments:
            audio_seg = AudioSegmentPlan(
                index=seg.index,
                start=seg.start_sec,
                end=seg.end_sec,
                mood=seg.mood or "",
                description=seg.description or "",
            )
            shots = [
                VisualShotSpec(
                    asset_prefix=shot.asset_prefix,
                    target_duration=float(shot.target_duration_sec),
                )
                for shot in seg.shots
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

    def build_ae_edit_plan(
        self, audio_path: Path, library_payload: list[dict]
    ) -> dict:
        # Для совместимости оставляем метод, но он просто прокидывает в новый конвейер
        # build_ae_project, который возвращает готовый AE-проект.
        return self.build_ae_project(audio_path=audio_path, library_payload=library_payload)

    def build_ae_project(
        self, audio_path: Path, library_payload: list[dict]
    ) -> dict:
        file_obj = self.client.client.files.upload(file=str(audio_path))
        log.info(
            "[build_ae_project] Uploaded audio %s (id=%s) for model %s",
            audio_path,
            getattr(file_obj, "name", None),
            self.cfg.gemini_model_planning,
        )

        log.info(
            "[build_ae_project] Request config: model=%s, mime=application/json",
            self.cfg.gemini_model_planning,
        )

        # Structured Output (SO) обязателен: просим модель вернуть JSON строго по схеме AeComposition.
        base_kwargs = {
            "temperature": 0.6,
            "response_mime_type": "application/json",
        }

        # IMPORTANT (Gemini SO): google-genai rejects JSON Schema containing
        # `additionalProperties` anywhere. Pydantic emits it for models with
        # dict-like fields and/or `extra` config. So we:
        # 1) generate JSON Schema dict from the Pydantic model
        # 2) recursively strip `additionalProperties`
        # 3) pass the resulting dict into the SDK (field name differs by version)
        response_schema_dict = strip_additional_properties(AeComposition.model_json_schema())

        try:
            # Newer SDKs (some builds) expose `response_json_schema`
            gen_cfg = types.GenerateContentConfig(
                **base_kwargs, response_json_schema=response_schema_dict
            )
        except TypeError:
            # Older SDKs use `response_schema` (and accept dict schema)
            gen_cfg = types.GenerateContentConfig(
                **base_kwargs, response_schema=response_schema_dict
            )

        resp = self.client.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[
                AE_PROJECT_SYSTEM,
                json.dumps(library_payload, ensure_ascii=False),
                file_obj,
            ],
            config=gen_cfg,
        )

        raw = self.client.extract_text_or_raise(resp, "build_ae_project")

        try:
            parsed = AeComposition.model_validate_json(raw)
        except ValidationError as exc:
            log.error("[build_ae_project] Pydantic validation error: %s", exc)
            log.debug("[build_ae_project] Raw JSON that failed: %s", raw)
            raise

        return parsed.model_dump(by_alias=True, exclude_none=True)

    def select_audio_highlights(self, audio_path: Path) -> List[AudioSegmentPlan]:
        log.info(
            "[select_audio_highlights] Uploading audio %s for model %s",
            audio_path,
            self.cfg.gemini_model_planning,
        )
        file_obj = self.client.client.files.upload(file=str(audio_path))

        resp = self.client.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[SELECT_AUDIO_HIGHLIGHTS_SYSTEM, file_obj],
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        raw = self.client.extract_text_or_raise(resp, "select_audio_highlights")

        try:
            parsed = AudioHighlightsModel.model_validate_json(raw)
        except ValidationError as exc:
            log.error("[select_audio_highlights] Pydantic validation error: %s", exc)
            log.debug("[select_audio_highlights] Raw JSON that failed: %s", raw)
            raise

        segments: List[AudioSegmentPlan] = []
        for segment in parsed.segments:
            seg = AudioSegmentPlan(
                index=segment.index,
                start=segment.start_sec,
                end=segment.end_sec,
                mood=segment.mood or "",
                description=segment.description or "",
            )
            segments.append(seg)

        segments.sort(key=lambda item: item.index)
        log.info(
            "[select_audio_highlights] Parsed %d segments: %s",
            len(segments),
            [(s.index, s.start, s.end) for s in segments],
        )
        return segments

    def plan_visuals_for_segment(
        self, segment: AudioSegmentPlan, library_payload: list[dict]
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

        resp = self.client.client.models.generate_content(
            model=self.cfg.gemini_model_planning,
            contents=[PLAN_VISUALS_SYSTEM, json.dumps(payload, ensure_ascii=False)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        raw = self.client.extract_text_or_raise(resp, "plan_visuals_for_segment")

        try:
            parsed = VisualPlanModel.model_validate_json(raw)
        except ValidationError as exc:
            log.error("[plan_visuals_for_segment] Pydantic validation error: %s", exc)
            log.debug("[plan_visuals_for_segment] Raw JSON that failed: %s", raw)
            raise

        shots = [
            VisualShotSpec(
                asset_prefix=shot.asset_prefix,
                target_duration=float(shot.target_duration_sec),
            )
            for shot in parsed.shots
        ]
        log.info(
            "[plan_visuals_for_segment] Got %d shots, total duration ~%.2f sec",
            len(shots),
            sum(s.target_duration for s in shots),
        )
        return shots
