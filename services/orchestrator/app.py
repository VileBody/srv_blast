from __future__ import annotations

import os
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from celery import Celery
from celery.result import AsyncResult
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

app = FastAPI(title="blast-orchestrator", version="1.0.0")

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

celery_app = Celery(
    "orchestrator",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "/data/audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class JobInternal(BaseModel):
    id: str
    name: str
    audio_path: str

    ml_task_id: Optional[str] = None
    render_task_id: Optional[str] = None

    ml_status: StepStatus = StepStatus.PENDING
    render_status: StepStatus = StepStatus.PENDING

    download_url: Optional[str] = None
    error: Optional[str] = None


class JobPublic(BaseModel):
    id: str
    name: str
    status: str
    ml_status: StepStatus
    render_status: StepStatus
    download_url: Optional[str] = None
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
        job.error = str(result.result)


def _maybe_schedule_render(job: JobInternal, ml_result: dict) -> None:
    if job.render_task_id is not None:
        return

    plan = ml_result.get("plan")
    if not plan:
        raise RuntimeError("ml_core result has no 'plan' field")

    async_result = celery_app.send_task(
        "ae.render_from_plan",
        args=[job.id, plan],
    )
    job.render_task_id = async_result.id
    job.render_status = StepStatus.PENDING


def _refresh_job_state(job: JobInternal) -> None:
    if job.ml_task_id:
        ml_res = AsyncResult(job.ml_task_id, app=celery_app)
        prev_ml_status = job.ml_status
        _update_from_async_result(job, ml_res, "ml")

        if prev_ml_status != StepStatus.SUCCESS and job.ml_status == StepStatus.SUCCESS:
            ml_payload = ml_res.get()
            _maybe_schedule_render(job, ml_payload)

    if job.render_task_id:
        r_res = AsyncResult(job.render_task_id, app=celery_app)
        prev_render_status = job.render_status
        _update_from_async_result(job, r_res, "render")

        if prev_render_status != StepStatus.SUCCESS and job.render_status == StepStatus.SUCCESS:
            payload = r_res.get() or {}
            job.download_url = payload.get("s3_url")


def _derive_overall_status(job: JobInternal) -> str:
    if job.render_status == StepStatus.SUCCESS and job.download_url:
        return "DONE"
    if job.render_status in {StepStatus.RUNNING, StepStatus.PENDING} and job.ml_status == StepStatus.SUCCESS:
        return "RENDERING"
    if job.ml_status in {StepStatus.RUNNING, StepStatus.PENDING}:
        return "PROCESSING"
    if job.ml_status == StepStatus.FAILED or job.render_status == StepStatus.FAILED:
        return "FAILED"
    return "UNKNOWN"


def _to_public(job: JobInternal) -> JobPublic:
    return JobPublic(
        id=job.id,
        name=job.name,
        status=_derive_overall_status(job),
        ml_status=job.ml_status,
        render_status=job.render_status,
        download_url=job.download_url,
        error=job.error,
    )


@app.post("/api/v1/jobs", response_model=JobPublic)
async def create_job(
    file: UploadFile = File(...),
    name: str = Form("edit"),
) -> JobPublic:
    job_id = uuid.uuid4().hex

    ext = Path(file.filename or "").suffix or ".m4a"
    audio_path = AUDIO_DIR / f"{job_id}{ext}"

    data = await file.read()
    audio_path.write_bytes(data)

    job = JobInternal(
        id=job_id,
        name=name,
        audio_path=str(audio_path),
    )
    _jobs[job_id] = job

    async_result = celery_app.send_task(
        "ml_core.build_edit_plan",
        args=[job_id, str(audio_path), name],
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
