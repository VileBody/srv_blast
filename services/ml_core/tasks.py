from __future__ import annotations

import logging
from typing import Any, Dict

from google.genai import errors as genai_errors

from .celery_app import celery_app
from .planner import build_edit_plan
from .render_ffmpeg import render_from_plan

log = logging.getLogger(__name__)


@celery_app.task(
    name="ml_core.build_edit_plan",
    bind=True,
    max_retries=3,               # максимум 3 попытки Celery поверх внутренних ретраев SDK
    default_retry_delay=10,      # базовая задержка, но мы ещё сделаем экспоненциальный backoff вручную
)
def build_edit_plan_task(
    self,
    job_id: str,
    audio_src: str,
    name: str = "edit",
) -> Dict[str, Any]:
    """
    Celery-задача, которую ставит оркестратор.

    Поведение:
      - вызывает build_edit_plan (Gemini + библиотека клипов);
      - если всё ок — возвращает { job_id, plan };
      - если от Gemini прилетает 5xx (ServerError), особенно 503 UNAVAILABLE,
        делаем несколько повторных попыток с паузами;
      - если после max_retries всё ещё ошибка — логируем и даём задаче упасть.
    """
    attempt = self.request.retries + 1  # начиная с 1
    max_attempts = self.max_retries + 1

    try:
        plan = build_edit_plan(job_id=job_id, audio_src=audio_src, name=name)
        log.info(
            "[build_edit_plan_task] job_id=%s finished on attempt %d/%d",
            job_id,
            attempt,
            max_attempts,
        )
        return {
            "job_id": job_id,
            "plan": plan,
        }

    except genai_errors.ServerError as e:
        # Это 5xx от Gemini — модель перегружена/недоступна.
        # Пример: 503 UNAVAILABLE "The model is overloaded. Please try again later."
        msg = str(e)
        log.warning(
            "[build_edit_plan_task] ServerError for job_id=%s on attempt %d/%d: %s",
            job_id,
            attempt,
            max_attempts,
            msg,
        )

        # Простейшая проверка на 503 — но можно расширить на другие 5xx
        is_503 = "503" in msg or "UNAVAILABLE" in msg.upper()

        if is_503 and self.request.retries < self.max_retries:
            # экспоненциальная задержка: 10, 20, 40 секунд ...
            countdown = self.default_retry_delay * (2 ** (self.request.retries))
            log.info(
                "[build_edit_plan_task] Retrying job_id=%s in %d seconds (attempt %d/%d)",
                job_id,
                countdown,
                attempt + 1,
                max_attempts,
            )
            raise self.retry(exc=e, countdown=countdown)

        # либо не 503, либо мы уже исчерпали попытки — фейлим окончательно
        log.error(
            "[build_edit_plan_task] Giving up on job_id=%s after attempt %d/%d. Error: %s",
            job_id,
            attempt,
            max_attempts,
            msg,
        )
        raise

    except Exception as e:
        # Любая другая ошибка — сразу логируем как неожиданную.
        log.exception(
            "[build_edit_plan_task] Unexpected error for job_id=%s on attempt %d/%d",
            job_id,
            attempt,
            max_attempts,
        )
        # Можно здесь тоже вызвать self.retry для сетевых/транзиентных ошибок,
        # но пока оставим как есть: упасть сразу, чтобы явно видеть баги.
        raise


@celery_app.task(name="ae.render_from_plan")
def ae_render_from_plan(job_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Реальный рендер через FFmpeg (не AE пока что, но интерфейс тот же):
    собирает клипы, монтирует сегменты, склеивает в один ролик, кладёт в S3_OUTPUT_VIDEO.
    """
    log.info("[ae.render_from_plan] Starting render for job_id=%s", job_id)
    result = render_from_plan(job_id, plan)
    log.info(
        "[ae.render_from_plan] Finished render for job_id=%s, s3_key=%s",
        job_id,
        result.get("s3_key"),
    )
    return result
