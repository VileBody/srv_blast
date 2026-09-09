from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
RENDER_MAX_WORKERS = max(1, int(os.getenv("AE_NODE_RENDER_MAX_WORKERS", "2") or "2"))
RENDER_MAX_PENDING = max(
    RENDER_MAX_WORKERS,
    int(os.getenv("AE_NODE_RENDER_MAX_PENDING", "8") or "8"),
)

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


class RenderQueueFullError(RuntimeError):
    pass


class RenderTaskManager:
    def __init__(self, *, max_workers: int, max_pending: int) -> None:
        self._lock = threading.Lock()
        self._states: Dict[str, _RenderState] = {}
        self._render_id_by_job_id: Dict[str, str] = {}
        self._max_workers = max(1, int(max_workers))
        self._max_pending = max(self._max_workers, int(max_pending))
        self._unfinished = 0
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="render",
        )
        self._state_file = Path(
            os.getenv("RENDER_STATE_FILE") or (Path(__file__).resolve().parent / "render_state.json")
        )
        self._restore()

    # ---- durable registry -------------------------------------------------
    # The registry used to live only in memory, so restarting the agent lost
    # every finished render the orchestrator had not collected yet. It then
    # polled /render/<id>, got a 404 and refunded the user for a video that had
    # actually rendered (job 6cc3ef6a, 15:23). Finished results now survive a
    # restart, and renders that were still in flight come back as a failure
    # with a reason instead of vanishing.

    @staticmethod
    def _state_to_dict(st: "_RenderState") -> Dict[str, Any]:
        r = st.result
        return {
            "render_id": st.render_id,
            "job_id": st.job_id,
            "payload_hash": st.payload_hash,
            "status": st.status,
            "created_at": st.created_at,
            "updated_at": st.updated_at,
            "finished_at": st.finished_at,
            "error": st.error,
            "result": None if r is None else {
                "job_id": r.job_id,
                "success": bool(r.success),
                "message": r.message,
                "app_dir": str(r.app_dir),
                "output_path": (str(r.output_path) if r.output_path else None),
                "output_s3_url": r.output_s3_url,
                "artifacts_s3_uri": r.artifacts_s3_uri,
            },
        }

    @staticmethod
    def _state_from_dict(raw: Dict[str, Any]) -> "_RenderState":
        rr = raw.get("result")
        result = None
        if isinstance(rr, dict):
            result = AeJobResult(
                job_id=str(rr.get("job_id") or ""),
                success=bool(rr.get("success")),
                message=str(rr.get("message") or ""),
                app_dir=Path(str(rr.get("app_dir") or ".")),
                output_path=(Path(rr["output_path"]) if rr.get("output_path") else None),
                output_s3_url=rr.get("output_s3_url"),
                artifacts_s3_uri=rr.get("artifacts_s3_uri"),
            )
        return _RenderState(
            render_id=str(raw.get("render_id") or ""),
            job_id=str(raw.get("job_id") or ""),
            payload_hash=str(raw.get("payload_hash") or ""),
            status=str(raw.get("status") or "failed"),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            finished_at=raw.get("finished_at"),
            result=result,
            error=raw.get("error"),
        )

    def _persist_locked(self) -> None:
        """Caller holds self._lock."""
        try:
            cutoff = time.time() - float(os.getenv("RENDER_STATE_TTL_S") or 24 * 3600)
            payload = [
                self._state_to_dict(st)
                for st in self._states.values()
                if (st.finished_at or st.updated_at or 0) >= cutoff
            ]
            tmp = self._state_file.with_name(self._state_file.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._state_file)
        except Exception as e:
            log.warning("render state persist failed: %r", e)

    def _restore(self) -> None:
        if not self._state_file.exists():
            return
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("render state restore failed: %r", e)
            return

        restored = 0
        orphaned = 0
        for item in raw if isinstance(raw, list) else []:
            try:
                st = self._state_from_dict(item)
            except Exception:
                continue
            if not st.render_id:
                continue
            if st.status in ("accepted", "running"):
                # Its worker thread died with the previous process. Report it
                # honestly rather than leaving the orchestrator polling forever.
                st.status = "failed"
                st.finished_at = st.finished_at or time.time()
                st.error = "render agent restarted while this job was in flight"
                st.result = None
                orphaned += 1
            self._states[st.render_id] = st
            if st.job_id:
                self._render_id_by_job_id[st.job_id] = st.render_id
            restored += 1

        log.info(
            "render state restored entries=%s orphaned_in_flight=%s file=%s",
            restored, orphaned, self._state_file,
        )

    def _drop_failed_locked(self, job_id: str, render_id: str) -> bool:
        """Caller holds self._lock. Forget a failed render so the job can retry.

        A failed record used to be returned as-is on re-dispatch, so the job was
        never handed to AE again -- it just got the old failure back until the
        record aged out (RENDER_STATE_TTL_S, a day). accepted/running/succeeded
        keep their idempotency: those protect against duplicate renders.
        """
        st = self._states.get(render_id)
        if st is None or st.status != "failed":
            return False
        self._states.pop(render_id, None)
        if self._render_id_by_job_id.get(job_id) == render_id:
            self._render_id_by_job_id.pop(job_id, None)
        self._persist_locked()
        log.info("dropped failed render so job can retry job_id=%s render_id=%s", job_id, render_id)
        return True

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
                if existing_render_id and self._drop_failed_locked(payload_job_id, existing_render_id):
                    existing_render_id = None
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
            if existing_render_id and self._drop_failed_locked(job_id, existing_render_id):
                existing_render_id = None
            if existing_render_id:
                st = self._states[existing_render_id]
                if st.payload_hash != payload_hash:
                    raise JobConflictError(
                        f"job_id={job_id!r} already exists with different payload; "
                        "reuse same payload or use a new job_id"
                    )
                return st

            if self._unfinished >= self._max_pending:
                raise RenderQueueFullError(
                    "render_queue_full: "
                    f"unfinished={self._unfinished} max_pending={self._max_pending}"
                )

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
            self._unfinished += 1
            self._persist_locked()

        try:
            self._executor.submit(self._run, render_id, spec)
        except Exception:
            with self._lock:
                self._unfinished = max(0, self._unfinished - 1)
                self._states.pop(render_id, None)
                self._render_id_by_job_id.pop(job_id, None)
                self._persist_locked()
            raise
        return st

    def _run(self, render_id: str, spec) -> None:
        with self._lock:
            st = self._states[render_id]
            st.status = "running"
            st.updated_at = time.time()
            self._persist_locked()

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
                self._persist_locked()
        except Exception as e:
            now = time.time()
            log.exception("async render worker crashed render_id=%s job_id=%s", render_id, spec.job_id)
            with self._lock:
                st = self._states[render_id]
                st.updated_at = now
                st.finished_at = now
                st.status = "failed"
                st.error = f"unexpected render worker error: {e}"
                self._persist_locked()
        finally:
            with self._lock:
                self._unfinished = max(0, self._unfinished - 1)

    def forget(self, render_id: str) -> str:
        """Drop a render record. Returns "removed", "not_found" or "running"."""
        with self._lock:
            st = self._states.get(render_id)
            if st is None:
                return "not_found"
            if st.status == "running":
                return "running"
            self._states.pop(render_id, None)
            if self._render_id_by_job_id.get(st.job_id) == render_id:
                self._render_id_by_job_id.pop(st.job_id, None)
            if st.status == "accepted":
                self._unfinished = max(0, self._unfinished - 1)
            self._persist_locked()
            log.info("render record forgotten render_id=%s job_id=%s", render_id, st.job_id)
            return "removed"

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

    def stats(self) -> Dict[str, int | bool]:
        with self._lock:
            running = sum(1 for st in self._states.values() if st.status == "running")
            accepted = sum(1 for st in self._states.values() if st.status == "accepted")
            unfinished = self._unfinished
            return {
                "running": running,
                "queued": accepted,
                "unfinished": unfinished,
                "max_workers": self._max_workers,
                "max_pending": self._max_pending,
                "available_slots": max(0, self._max_pending - unfinished),
                "ready": unfinished < self._max_pending,
            }

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)


manager = RenderTaskManager(
    max_workers=RENDER_MAX_WORKERS,
    max_pending=RENDER_MAX_PENDING,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "render": manager.stats()}


@app.get("/ready")
def ready() -> dict:
    stats = manager.stats()
    if not bool(stats["ready"]):
        raise HTTPException(
            status_code=503,
            detail={"code": "render_queue_full", "render": stats},
            headers={"Retry-After": "15"},
        )
    return {"status": "ready", "render": stats}


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
    except RenderQueueFullError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "render_queue_full", "message": str(e)},
            headers={"Retry-After": "15"},
        ) from e
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


@app.delete("/render/{render_id}")
def delete_render(render_id: str) -> Dict[str, str]:
    rid = str(render_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="render_id is empty")

    outcome = manager.forget(rid)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="render_id not found")
    if outcome == "running":
        raise HTTPException(status_code=409, detail="render is running; refusing to forget it")
    return {"status": "removed", "render_id": rid}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
