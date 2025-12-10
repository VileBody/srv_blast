from __future__ import annotations

import os
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from celery import Celery
from celery.result import AsyncResult
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from src.storage.s3 import upload_bytes_to_s3

app = FastAPI(title="blast-orchestrator", version="1.0.0")

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

celery_app = Celery(
    "orchestrator",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

S3_BUCKET_RAW_AUDIO = os.getenv("S3_BUCKET_RAW_AUDIO")


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class JobInternal(BaseModel):
    id: str
    name: str

    # S3-key аудио, а не локальный путь
    audio_key: str

    ml_task_id: Optional[str] = None
    render_task_id: Optional[str] = None

    ml_status: StepStatus = StepStatus.PENDING
    render_status: StepStatus = StepStatus.PENDING

    # список ссылок на сегменты
    segment_urls: List[str] = []

    error: Optional[str] = None


class JobPublic(BaseModel):
    id: str
    name: str
    status: str
    ml_status: StepStatus
    render_status: StepStatus

    # для совместимости — первая ссылка (если нужна)
    download_url: Optional[str] = None

    # полный список ссылок
    download_urls: List[str] = []

    # сырое сообщение об ошибке (если есть)
    error: Optional[str] = None


_jobs: Dict[str, JobInternal] = {}


def _update_from_async_result(job: JobInternal, result: AsyncResult, step: str) -> None:
    state = result.state

    if step == "ml":
        field = "ml_status"
    else:
        field = "render_status"

    if state == "PENDING":
        setattr(job, field, StepStatus.PENDING)
    elif state in {"RECEIVED", "STARTED", "RETRY"}:
        setattr(job, field, StepStatus.RUNNING)
    elif state == "SUCCESS":
        setattr(job, field, StepStatus.SUCCESS)
    elif state in {"FAILURE", "REVOKED"}:
        setattr(job, field, StepStatus.FAILED)
        try:
            job.error = str(result.result)
        except Exception:
            job.error = "Unknown error"


def _maybe_schedule_render(job: JobInternal, ml_result: dict) -> None:
    """
    Если ML-план готов и рендер ещё не поставлен — ставим ae.render_from_plan.

    Поддерживаем два формата ответа от ml-core:
      1) {"plan": {...}}        — старый вариант
      2) {..., "segments": ...} — новый вариант, когда сам результат уже план
    """
    if job.render_task_id is not None:
        return

    plan = ml_result.get("plan")

    # Backward/forward compatibility:
    # если поля "plan" нет, но есть "segments"/"composition"/"project_data" — считаем, что
    # ml_result и есть готовый план
    if plan is None:
        if any(key in ml_result for key in ("segments", "composition", "project_data")):
            plan = ml_result
        else:
            raise RuntimeError("ml_core result has no usable plan fields")

    async_result = celery_app.send_task(
        "ae.render_from_plan",
        args=[job.id, plan],
    )
    job.render_task_id = async_result.id
    job.render_status = StepStatus.PENDING



def _refresh_job_state(job: JobInternal) -> None:
    # 1) ML-таска
    if job.ml_task_id:
        ml_res = AsyncResult(job.ml_task_id, app=celery_app)
        prev_ml_status = job.ml_status
        _update_from_async_result(job, ml_res, "ml")

        if prev_ml_status != StepStatus.SUCCESS and job.ml_status == StepStatus.SUCCESS:
            ml_payload = ml_res.get()
            _maybe_schedule_render(job, ml_payload)

    # 2) рендер-таска
    if job.render_task_id:
        r_res = AsyncResult(job.render_task_id, app=celery_app)
        prev_render_status = job.render_status
        _update_from_async_result(job, r_res, "render")

        if prev_render_status != StepStatus.SUCCESS and job.render_status == StepStatus.SUCCESS:
            payload = r_res.get() or {}
            segments = payload.get("segments") or []
            urls: List[str] = []
            for seg in segments:
                url = seg.get("s3_url")
                if url:
                    urls.append(url)
            job.segment_urls = urls


def _derive_overall_status(job: JobInternal) -> str:
    if job.render_status == StepStatus.SUCCESS and job.segment_urls:
        return "DONE"
    if job.render_status in {StepStatus.RUNNING, StepStatus.PENDING} and job.ml_status == StepStatus.SUCCESS:
        return "RENDERING"
    if job.ml_status in {StepStatus.RUNNING, StepStatus.PENDING}:
        return "PROCESSING"
    if job.ml_status == StepStatus.FAILED or job.render_status == StepStatus.FAILED:
        return "FAILED"
    return "UNKNOWN"


def _to_public(job: JobInternal) -> JobPublic:
    urls = job.segment_urls or []
    first_url = urls[0] if urls else None
    return JobPublic(
        id=job.id,
        name=job.name,
        status=_derive_overall_status(job),
        ml_status=job.ml_status,
        render_status=job.render_status,
        download_url=first_url,
        download_urls=urls,
        error=job.error,
    )


def _upload_audio_to_s3(job_id: str, filename: str, data: bytes) -> str:
    """
    Заливаем аудио в S3_BUCKET_RAW_AUDIO.
    Ключ = <job_id><orig_ext>. Возвращаем key.
    """
    if not S3_BUCKET_RAW_AUDIO:
        raise RuntimeError("S3_BUCKET_RAW_AUDIO is not set; cannot upload audio")

    ext = Path(filename or "").suffix or ".m4a"
    key = f"{job_id}{ext}"

    content_type = "audio/m4a"
    if ext.lower() == ".mp3":
        content_type = "audio/mpeg"
    elif ext.lower() == ".wav":
        content_type = "audio/wav"

    upload_bytes_to_s3(S3_BUCKET_RAW_AUDIO, key, data, content_type=content_type)
    return key


@app.post("/api/v1/jobs", response_model=JobPublic)
async def create_job(
    file: UploadFile = File(...),
    name: str = Form("edit"),
) -> JobPublic:
    """
    Клиент присылает file -> кладём его в S3_BUCKET_RAW_AUDIO, key = <job_id><ext>.
    """
    if not S3_BUCKET_RAW_AUDIO:
        raise HTTPException(
            status_code=500,
            detail="S3_BUCKET_RAW_AUDIO is not configured on server",
        )

    job_id = uuid.uuid4().hex
    data = await file.read()
    audio_key = _upload_audio_to_s3(job_id, file.filename or "", data)

    job = JobInternal(
        id=job_id,
        name=name,
        audio_key=audio_key,
    )
    _jobs[job_id] = job

    async_result = celery_app.send_task(
        "ml_core.build_edit_plan",
        args=[job_id, audio_key, name],
    )
    job.ml_task_id = async_result.id
    job.ml_status = StepStatus.PENDING

    return _to_public(job)


@app.get("/api/v1/jobs/{job_id}", response_model=JobPublic)
def get_job(job_id: str) -> JobPublic:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    _refresh_job_state(job)
    return _to_public(job)
