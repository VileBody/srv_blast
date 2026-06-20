# mlcore/hooks/f5_cognition/stage1_text.py
"""
Stage 1 — генерация текста вставки и voice spec через Gemini text mode.

Возвращает VoiceSpec (см. models.py). Stage 1 НЕ выбирает устройство —
оно приходит из бота во F5Request.device.

Скелет: фактический gemini-вызов помечен TODO — подключаем когда определимся
с используемым клиентом (либо google.genai напрямую, либо через mlcore.gemini_client).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from mlcore.hooks.f5_cognition._gemini import make_client
from mlcore.hooks.f5_cognition.errors import F5GeminiTimeout, F5Stage1ParseError
from mlcore.hooks.f5_cognition.models import F5Request, VoiceSpec
from mlcore.hooks.f5_cognition.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

# Окно валидации expected_duration_ms (Pydantic его уже валидирует жёстко 1500–4000,
# но Stage 1 хочет таргет именно в этом подокне).
TARGET_DURATION_MIN_MS = 2500
TARGET_DURATION_MAX_MS = 3500


def _strip_code_fence(text: str) -> str:
    """Снимает ```json ... ``` обёртку если LLM её добавила."""
    t = text.strip()
    if t.startswith("```"):
        # ```json\n{...}\n```
        m = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", t, re.DOTALL)
        if m:
            return m.group(1).strip()
    return t


def _parse_voice_spec(raw: str) -> VoiceSpec:
    try:
        data: dict[str, Any] = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as e:
        raise F5Stage1ParseError(f"Stage 1 returned non-JSON: {e}; raw={raw[:300]!r}") from e

    # Pre-clamp the model's expected_duration_ms estimate into the VoiceSpec field
    # bounds [1500, 4000] BEFORE validation. It is only an estimate (clamped to the
    # 2500-3500 target just below), so an out-of-range guess must NOT crash all of
    # F5 — which it did: a sub-1500 estimate tripped VoiceSpec's ge=1500 → the
    # render had no voice (job 08a98492…, missing_word). The soft clamp below was
    # dead for that case because Pydantic rejected the value first.
    if isinstance(data, dict):
        try:
            _edm = int(data["expected_duration_ms"])
            data["expected_duration_ms"] = max(1500, min(4000, _edm))
        except (KeyError, TypeError, ValueError):
            pass  # missing/non-numeric → let VoiceSpec raise a clear error

    try:
        spec = VoiceSpec(**data)
    except Exception as e:
        raise F5Stage1ParseError(f"Stage 1 JSON missing fields: {e}; data={data!r}") from e

    # Мягкая правка: если expected_duration_ms ушёл за таргет — подравниваем.
    if spec.expected_duration_ms < TARGET_DURATION_MIN_MS:
        logger.warning(
            "Stage 1 returned expected_duration_ms=%d, clamping to %d",
            spec.expected_duration_ms, TARGET_DURATION_MIN_MS,
        )
        spec = spec.model_copy(update={"expected_duration_ms": TARGET_DURATION_MIN_MS})
    elif spec.expected_duration_ms > TARGET_DURATION_MAX_MS:
        logger.warning(
            "Stage 1 returned expected_duration_ms=%d, clamping to %d",
            spec.expected_duration_ms, TARGET_DURATION_MAX_MS,
        )
        spec = spec.model_copy(update={"expected_duration_ms": TARGET_DURATION_MAX_MS})

    return spec


def _call_gemini_text(system_prompt: str, user_prompt: str, *, model: str, seed: int | None) -> str:
    """
    Реальный text-вызов Gemini для Stage 1.

    Лёгкий text-only запрос с JSON-ответом (google.genai напрямую, не через
    mlcore.gemini_client — тот заточен под основной ASR-пайплайн).

    system_prompt уходит в system_instruction, user_prompt — в contents.
    Просим JSON через response_mime_type. seed прокидываем для воспроизводимости
    в тестах (в проде кэш отключён → seed=None → каждый раз новая персона).
    """
    from google.genai import types

    client = make_client()

    cfg_kwargs: dict[str, Any] = {
        "system_instruction": system_prompt,
        "response_mime_type": "application/json",
        "temperature": 1.0,
    }
    if seed is not None:
        cfg_kwargs["seed"] = seed

    resp = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(**cfg_kwargs),
    )

    text = getattr(resp, "text", None)
    if not text:
        raise F5GeminiTimeout("Gemini Stage 1 returned empty text response")
    return text


def run_stage1(req: F5Request) -> VoiceSpec:
    """
    Главная точка входа Stage 1.

    Логика:
      1. Сборка system+user промтов под выбранное устройство.
      2. Вызов Gemini text.
      3. Парсинг JSON → VoiceSpec.
    """
    system_prompt = build_system_prompt(req.device)
    user_prompt = build_user_prompt(req)

    model = os.getenv("GEMINI_MODEL_F5_TEXT", "gemini-2.5-flash")

    logger.info(
        "f5.stage1 start device=%s model=%s focal_ms=%d",
        req.device.value, model, req.focal_start_ms,
    )

    raw = _call_gemini_text(system_prompt, user_prompt, model=model, seed=req.seed)
    spec = _parse_voice_spec(raw)

    logger.info(
        "f5.stage1 done text=%r emotion=%s pacing=%s expected_ms=%d",
        spec.tts_text, spec.voice_emotion, spec.voice_pacing, spec.expected_duration_ms,
    )
    return spec
