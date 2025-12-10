from __future__ import annotations

import logging
from typing import Any, Dict

from celery.utils.log import get_task_logger

from .celery_app import celery_app
from .planner import build_edit_plan
from .render_ae import render_from_plan as render_ae_from_plan

log = get_task_logger(__name__)


@celery_app.task(
    name="ml_core.build_edit_plan",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def build_edit_plan_task_wrapped(self, job_id: str, audio_key: str, name: str) -> Dict[str, Any]:
    """
    Обёртка над build_edit_plan с ретраями.

    Важно:
    - build_edit_plan сам по себе обычная функция (без декоратора celery_app.task),
      чтобы её было удобно тестировать отдельно.
    - Эта обёртка отвечает за повторные попытки в случае "503 / UNAVAILABLE" от модели.
    """
    max_attempts = self.max_retries + 1

    try:
        log.info(
            "[build_edit_plan_task] Starting job_id=%s, attempt=%d/%d",
            job_id,
            self.request.retries + 1,
            max_attempts,
        )

        result = build_edit_plan(job_id, audio_key, name)
        log.info(
            "[build_edit_plan_task] Finished job_id=%s successfully on attempt %d/%d",
            job_id,
            self.request.retries + 1,
            max_attempts,
        )
        return result

    except Exception as e:
        # Пробуем аккуратно отловить 503 от внешнего API (Gemini / ElevenLabs / S3 и т.п.)
        msg = str(e)
        log.warning(
            "[build_edit_plan_task] Error for job_id=%s on attempt %d/%d: %s",
            job_id,
            self.request.retries + 1,
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
                self.request.retries + 2,
                max_attempts,
            )
            raise self.retry(exc=e, countdown=countdown)

        # либо не 503, либо мы уже исчерпали попытки — фейлим окончательно
        log.error(
            "[build_edit_plan_task] Giving up on job_id=%s after attempt %d/%d. Error: %s",
            job_id,
            self.request.retries + 1,
            max_attempts,
            msg,
        )
        raise


@celery_app.task(name="ae.render_from_plan")
def ae_render_from_plan(job_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    AE-рендер ноды: собираем JSX + список медиа и отправляем на Windows-сервер с After Effects.
    Контракт по возврату совпадает с FFmpeg-версией (segments со ссылками в S3).
    """
    log.info("[ae.render_from_plan] Starting AE render for job_id=%s", job_id)
    result = render_ae_from_plan(job_id, plan)
    log.info(
        "[ae.render_from_plan] Finished AE render for job_id=%s, segments=%s",
        job_id,
        [s.get("s3_key") for s in result.get("segments", [])],
    )
    return result
