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
from .asset_routes import create_asset_router
from .windows_node_pool import WindowsNodePool


def create_app() -> FastAPI:
    app = FastAPI(title="Blast Orchestrator", version="0.4")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Asset browsing UI
    app.include_router(create_asset_router())

    store = JobStore.from_env()
    _bundle_ok = False

    @app.on_event("startup")
    def _startup() -> None:
        nonlocal _bundle_ok
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
            _bundle_ok = True
        else:
            print(f"[bundle][ERR] {res.reason}")
            _bundle_ok = False

    @app.get("/health")
    def health() -> dict:
        checks: dict = {}
        try:
            store.r.ping()
            checks["redis"] = True
        except Exception:
            checks["redis"] = False

        checks["bundle"] = _bundle_ok
        pool = WindowsNodePool(
            redis_client=store.r,
            key_prefix=store.key_prefix,
            lease_ttl_s=SETTINGS.windows_node_lease_ttl_s,
        )
        active_urls = pool.get_active_urls(default_urls=SETTINGS.windows_render_urls)
        checks["windows_render_nodes"] = bool(active_urls)

        ok = all(checks.values())
        return {"ok": ok, "checks": checks, "windows_render_nodes": active_urls}

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

    @app.get("/metrics")
    def metrics() -> dict:
        """Lightweight observability endpoint — queue lengths, job status counts."""
        from .celery_app import celery_app as _celery

        counts: dict = {"NEW": 0, "QUEUED": 0, "RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0}
        jobs_error: str | None = None
        try:
            for job in store.list_jobs():
                s = job.get("status", "")
                if s in counts:
                    counts[s] += 1
        except Exception as exc:
            jobs_error = repr(exc)

        queues: dict = {}
        try:
            inspect = _celery.control.inspect(timeout=1.0)
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            for worker, tasks in active.items():
                queues[worker] = {"active": len(tasks), "reserved": len(reserved.get(worker, []))}
        except Exception:
            queues["error"] = "inspect_failed"

        return {
            "job_status_counts": counts,
            "job_status_error": jobs_error,
            "workers": queues,
            "bundle_ok": _bundle_ok,
        }

    # Serve built frontend (if exists)
    _ui_dist = Path(__file__).resolve().parents[2] / "asset_ui" / "dist"
    if _ui_dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/asset-ui", StaticFiles(directory=str(_ui_dist), html=True), name="asset-ui-static")

    return app


app = create_app()
