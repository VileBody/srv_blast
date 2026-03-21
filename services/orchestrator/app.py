# services/orchestrator/app.py
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from .job_store import JobStore
from .schemas import SendVideoRequest, SendVideoResponse, JobState
from .tasks import build_job
from .config import SETTINGS
from .bundle_bootstrap import ensure_descriptions_bundle


def create_app() -> FastAPI:
    app = FastAPI(title="Blast Orchestrator", version="0.4")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    store = JobStore.from_env()

    @app.on_event("startup")
    def _startup() -> None:
        # Global bundle bootstrap (one for all jobs)
        inv = Path(SETTINGS.footage_inventory_json)
        bun = Path(SETTINGS.descriptions_bundle_path)

        max_assets = None
        if SETTINGS.descriptions_bundle_max_assets.strip():
            try:
                max_assets = int(SETTINGS.descriptions_bundle_max_assets.strip())
            except Exception:
                max_assets = None

        res = ensure_descriptions_bundle(
            inventory_json=inv,
            bundle_path=bun,
            max_assets=max_assets,
            force_rebuild=False,
        )
        if res.ok:
            print(f"[bundle] {res.action}: {res.bundle_path}")
        else:
            # Не валим сервис насмерть — но будет ошибка позже при LLM-вызове.
            print(f"[bundle][ERR] {res.reason}")

    @app.get("/health")
    def health() -> dict:
        try:
            store.r.ping()
            ok = True
        except Exception:
            ok = False
        return {"ok": ok}

    # ==========================================================
    # NEW: correct naming (audio URL -> enqueue pipeline)
    # ==========================================================
    # Мы используем те же модели SendVideoRequest/SendVideoResponse,
    # чтобы не править сразу весь проект.
    # Позже можешь переименовать модели в schemas.py, но эндпоинт уже будет правильный.
    @app.post("/send_audio_s3", response_model=SendVideoResponse)
    def send_audio_s3(req: SendVideoRequest) -> SendVideoResponse:
        st, created = store.new_job(
            request=req.model_dump(mode="json"),
            idempotency_key=req.idempotency_key,
        )

        try:
            store.set_status(st.job_id, "QUEUED", stage="build")
            build_job.delay(st.job_id)
        except Exception as e:
            store.set_status(st.job_id, "FAILED", stage="build", error=f"queue_failed: {e!r}")
            raise HTTPException(status_code=500, detail="Failed to enqueue job")

        st2 = store.get(st.job_id) or st
        return SendVideoResponse(job_id=st2.job_id, status=st2.status, created=created)

    # ==========================================================
    # Backward-compat alias (can be removed later)
    # ==========================================================
    @app.post("/send_video", response_model=SendVideoResponse)
    def send_video(req: SendVideoRequest) -> SendVideoResponse:
        # Aliases to the new endpoint implementation
        return send_audio_s3(req)

    @app.get("/jobs/{job_id}", response_model=JobState)
    def get_job(job_id: str) -> JobState:
        st = store.get(job_id)
        if not st:
            raise HTTPException(status_code=404, detail="job not found")
        return st

    return app


app = create_app()
