# services/orchestrator/app.py
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from core.llm_worker_types import LLM_WORKER_TYPE_SDK
from .job_store import JobStore
from .llm_workers import (
    ensure_config_initialized,
    get_runtime_status,
    release_worker_slot,
    reserve_worker_type,
    set_config,
)
from .payment_webhook import make_payment_router
from .schemas import (
    JobState,
    LLMWorkerRuntimeStatus,
    LLMWorkersConfigRequest,
    LLMWorkersStatusResponse,
    SendVideoRequest,
    SendVideoResponse,
)
from .tasks import build_job_hybrid, build_job_openrouter, build_job_sdk
from .config import SETTINGS
from .bundle_bootstrap import ensure_descriptions_bundle
from .asset_routes import create_asset_router
from services.tg_bot_botapi.user_store import UserStore


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

    # Payment webhook router — backed by PostgreSQL via shared UserStore.
    # Initialized once at startup, closed on shutdown.
    _user_store: UserStore | None = None

    @app.on_event("startup")
    async def _init_db() -> None:
        nonlocal _user_store
        if SETTINGS.credits_db_url:
            _user_store = UserStore(SETTINGS.credits_db_url)
            await _user_store.init()

    @app.on_event("shutdown")
    async def _close_db() -> None:
        if _user_store is not None:
            await _user_store.close()

    if SETTINGS.payment_webhook_secret or SETTINGS.payment_admin_token:
        # Router uses _user_store which is set by startup event before first request.
        # We pass a lambda so the router always gets the current value.
        class _LazyUserStore:
            """Thin proxy so payment router works even though pool isn't ready at import time."""
            async def ensure_profile(self, *a, **kw):  # type: ignore[override]
                assert _user_store, "CREDITS_DB_URL not configured"
                return await _user_store.ensure_profile(*a, **kw)

            async def confirm_payment(self, *a, **kw):  # type: ignore[override]
                assert _user_store, "CREDITS_DB_URL not configured"
                return await _user_store.confirm_payment(*a, **kw)

            async def manual_activate(self, *a, **kw):  # type: ignore[override]
                assert _user_store, "CREDITS_DB_URL not configured"
                return await _user_store.manual_activate(*a, **kw)

        payment_router = make_payment_router(
            _LazyUserStore(),  # type: ignore[arg-type]
            webhook_secret=SETTINGS.payment_webhook_secret,
            admin_token=SETTINGS.payment_admin_token,
        )
        app.include_router(payment_router)

    @app.on_event("startup")
    def _startup() -> None:
        nonlocal _bundle_ok
        ensure_config_initialized(store)
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

        ok = all(checks.values())
        return {"ok": ok, "checks": checks}

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
        if not created:
            return SendVideoResponse(job_id=st.job_id, status=st.status, created=False)

        worker_type: str | None = None
        queued = False
        try:
            selected = reserve_worker_type(store, requested=req.llm_worker_type)
            worker_type = selected.worker_type

            store.patch_request(st.job_id, {"llm_worker_type": worker_type})
            store.set_status(
                st.job_id,
                "QUEUED",
                stage="build",
                result={"llm_worker_type": worker_type},
            )
            queued = True

            if worker_type == "sdk":
                build_job_sdk.delay(st.job_id)
            elif worker_type == "openrouter":
                build_job_openrouter.delay(st.job_id)
            elif worker_type == "hybrid":
                build_job_hybrid.delay(st.job_id)
            else:
                raise RuntimeError(f"unsupported llm_worker_type: {worker_type}")
        except Exception as e:
            if worker_type and not queued:
                try:
                    release_worker_slot(store, worker_type)
                except Exception:
                    pass
            store.set_status(st.job_id, "FAILED", stage="build", error=f"queue_failed: {e!r}")
            msg = str(e)
            if "capacity_exhausted" in msg or "disabled" in msg or "no_enabled_types" in msg:
                raise HTTPException(status_code=503, detail=f"LLM workers capacity issue: {msg}")
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

    @app.get("/llm-workers", response_model=LLMWorkersStatusResponse)
    def get_llm_workers() -> LLMWorkersStatusResponse:
        ensure_config_initialized(store)
        status = get_runtime_status(store)
        workers = {
            worker_type: LLMWorkerRuntimeStatus.model_validate(row.model_dump(mode="json"))
            for worker_type, row in status.items()
        }
        return LLMWorkersStatusResponse(
            workers=workers,
            default_worker_type=LLM_WORKER_TYPE_SDK,
        )

    @app.put("/llm-workers", response_model=LLMWorkersStatusResponse)
    def put_llm_workers(payload: LLMWorkersConfigRequest) -> LLMWorkersStatusResponse:
        try:
            set_config(store, payload)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid workers config: {e}") from e

        status = get_runtime_status(store)
        workers = {
            worker_type: LLMWorkerRuntimeStatus.model_validate(row.model_dump(mode="json"))
            for worker_type, row in status.items()
        }
        return LLMWorkersStatusResponse(
            workers=workers,
            default_worker_type=LLM_WORKER_TYPE_SDK,
        )

    @app.get("/metrics")
    def metrics() -> dict:
        """Lightweight observability endpoint — queue lengths, job status counts."""
        from .celery_app import celery_app as _celery

        counts: dict = {"NEW": 0, "QUEUED": 0, "RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0}
        jobs_error: str | None = None
        try:
            for job in store.list_jobs():
                s = str(getattr(job, "status", "") or "")
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
