from __future__ import annotations

from typing import Any, Dict

from .celery_app import celery_app
from .planner import build_edit_plan


@celery_app.task(name="ml_core.build_edit_plan")
def build_edit_plan_task(job_id: str, audio_src: str, name: str = "edit") -> Dict[str, Any]:
    """
    Celery-задача, которую будет ставить оркестратор.
    Возвращает { job_id, plan }.
    """
    plan = build_edit_plan(job_id=job_id, audio_src=audio_src, name=name)
    return {
        "job_id": job_id,
        "plan": plan,
    }


@celery_app.task(name="ae.render_from_plan")
def ae_render_stub(job_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Временная заглушка вместо реального рендера в After Effects.
    Просто возвращает фейковый URL, чтобы оркестратор увидел DONE.
    Позже это место заменит реальный Windows+AE воркер.
    """
    fake_url = f"https://example.com/fake-render/{job_id}.mp4"
    return {
        "job_id": job_id,
        "s3_url": fake_url,
    }
