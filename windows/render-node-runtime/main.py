from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ae_sdk import AeJobResult, AeRenderer, make_job_spec_from_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AE_JOBS_BASE_DIR = os.getenv("AE_JOBS_BASE_DIR", r"C:\ae_jobs")
AFTERFX_BIN = os.getenv("AFTERFX_BIN")

HOST = os.getenv("AE_NODE_HOST", "0.0.0.0")
PORT = int(os.getenv("AE_NODE_PORT", "8000"))

renderer = AeRenderer(
    base_dir=AE_JOBS_BASE_DIR,
    afterfx_bin=AFTERFX_BIN,
)

app = FastAPI(title="AE Render Node (Async Render API)", version="0.5.0")


class MediaFilePayload(BaseModel):
    url: str = Field(
        ...,
        description="HTTP/HTTPS URL ИЛИ s3://bucket/key",
    )
    relpath: str = Field(
        ...,
        description="Относительный путь внутри app/, напр. 'media/video/clip1.mp4'",
    )


class CreateJobRequest(BaseModel):
    job_id: Optional[str] = Field(None, description="Опциональный внешний ID джобы")

    # Old inline mode
    render_jsx: Optional[str] = Field(
        None,
        description="Полный текст render.jsx (legacy inline mode)",
    )
    media: List[MediaFilePayload] = Field(
        default_factory=list,
        description="Legacy inline media массив: url + relpath",
    )

    # New S3-ref mode
    render_jsx_s3_uri: Optional[str] = Field(
        None,
        description="s3://... (или http[s]) ссылка на render_full.jsx",
    )
    render_payload_s3_uri: Optional[str] = Field(
        None,
        description="s3://... (или http[s]) ссылка на final_render_instructions_full.json",
    )
    audio_url: Optional[str] = Field(
        None,
        description="Remote URL аудио (http[s]/s3)",
    )

    entry_comp: str = Field(
        "Main Render",
        description="Имя композиции (default, если JSX не вернул другое compName)",
    )
    output_relpath: str = Field(
        "work/output.mp4",
        description="Относительный путь итогового файла внутри app/",
    )

    output_s3_bucket: Optional[str] = Field(None, description="S3 bucket для итогового файла")
    output_s3_key: Optional[str] = Field(None, description="S3 key для итогового файла")


class JobResponse(BaseModel):
    job_id: str
    success: bool
    message: str
    app_dir: str
    output_path: Optional[str]
    output_url: Optional[str]


class RenderAcceptedResponse(BaseModel):
    status: str
    render_id: str
    job_id: str


class RenderStatusResponse(BaseModel):
    status: str
    render_id: str
    job_id: str
    success: Optional[bool] = None
    message: Optional[str] = None
    output_path: Optional[str] = None
    output_url: Optional[str] = None
    app_dir: Optional[str] = None


@dataclass
class _RenderState:
    render_id: str
    job_id: str
    payload_hash: str
    status: str
    created_at: float
    updated_at: float
    finished_at: Optional[float]
    result: Optional[AeJobResult]
    error: Optional[str]


class JobConflictError(RuntimeError):
    pass


class RenderTaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: Dict[str, _RenderState] = {}
        self._render_id_by_job_id: Dict[str, str] = {}

    @staticmethod
    def _payload_hash(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def submit(self, payload: Dict[str, Any]) -> _RenderState:
        payload_hash = self._payload_hash(payload)
        payload_job_id = str(payload.get("job_id") or "").strip()

        with self._lock:
            if payload_job_id:
                existing_render_id = self._render_id_by_job_id.get(payload_job_id)
                if existing_render_id:
                    st = self._states[existing_render_id]
                    if st.payload_hash != payload_hash:
                        raise JobConflictError(
                            f"job_id={payload_job_id!r} already exists with different payload; "
                            "reuse same payload or use a new job_id"
                        )
                    return st

        spec = make_job_spec_from_payload(payload)
        job_id = str(spec.job_id).strip()
        if not job_id:
            raise RuntimeError("job_id is empty after payload parsing")

        with self._lock:
            existing_render_id = self._render_id_by_job_id.get(job_id)
            if existing_render_id:
                st = self._states[existing_render_id]
                if st.payload_hash != payload_hash:
                    raise JobConflictError(
                        f"job_id={job_id!r} already exists with different payload; "
                        "reuse same payload or use a new job_id"
                    )
                return st

            render_id = uuid.uuid4().hex
            now = time.time()
            st = _RenderState(
                render_id=render_id,
                job_id=job_id,
                payload_hash=payload_hash,
                status="accepted",
                created_at=now,
                updated_at=now,
                finished_at=None,
                result=None,
                error=None,
            )
            self._states[render_id] = st
            self._render_id_by_job_id[job_id] = render_id

        t = threading.Thread(
            target=self._run,
            args=(render_id, spec),
            name=f"render-{render_id[:8]}",
            daemon=True,
        )
        t.start()
        return st

    def _run(self, render_id: str, spec) -> None:
        with self._lock:
            st = self._states[render_id]
            st.status = "running"
            st.updated_at = time.time()

        try:
            result = renderer.run_job(spec)
            now = time.time()
            with self._lock:
                st = self._states[render_id]
                st.result = result
                st.updated_at = now
                st.finished_at = now
                st.status = "succeeded" if result.success else "failed"
                st.error = None if result.success else result.message
        except Exception as e:
            now = time.time()
            log.exception("async render worker crashed render_id=%s job_id=%s", render_id, spec.job_id)
            with self._lock:
                st = self._states[render_id]
                st.updated_at = now
                st.finished_at = now
                st.status = "failed"
                st.error = f"unexpected render worker error: {e}"

    def get(self, render_id: str) -> Optional[_RenderState]:
        with self._lock:
            st = self._states.get(render_id)
            if st is None:
                return None
            return _RenderState(
                render_id=st.render_id,
                job_id=st.job_id,
                payload_hash=st.payload_hash,
                status=st.status,
                created_at=st.created_at,
                updated_at=st.updated_at,
                finished_at=st.finished_at,
                result=st.result,
                error=st.error,
            )


manager = RenderTaskManager()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/jobs", response_model=JobResponse)
def create_job_sync(req: CreateJobRequest) -> JobResponse:
    payload = req.model_dump()
    job_spec = make_job_spec_from_payload(payload)

    try:
        result: AeJobResult = renderer.run_job(job_spec)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"render error: {e}") from e

    return JobResponse(
        job_id=result.job_id,
        success=result.success,
        message=result.message,
        app_dir=str(result.app_dir),
        output_path=str(result.output_path) if result.output_path else None,
        output_url=result.output_s3_url,
    )


@app.post("/render", response_model=RenderAcceptedResponse)
def create_render(req: CreateJobRequest) -> RenderAcceptedResponse:
    payload = req.model_dump()
    try:
        st = manager.submit(payload)
    except JobConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid render payload: {e}") from e

    # Always return accepted/running contract with stable render_id for idempotent retries.
    status = str(st.status or "running")
    return RenderAcceptedResponse(status=status, render_id=st.render_id, job_id=st.job_id)


@app.get("/render/{render_id}", response_model=RenderStatusResponse)
def get_render(render_id: str) -> RenderStatusResponse:
    rid = str(render_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="render_id is empty")

    st = manager.get(rid)
    if st is None:
        raise HTTPException(status_code=404, detail="render_id not found")

    result = st.result
    return RenderStatusResponse(
        status=st.status,
        render_id=st.render_id,
        job_id=st.job_id,
        success=(bool(result.success) if result is not None else None),
        message=(result.message if result is not None else st.error),
        output_path=(str(result.output_path) if (result is not None and result.output_path) else None),
        output_url=(result.output_s3_url if result is not None else None),
        app_dir=(str(result.app_dir) if result is not None else None),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
