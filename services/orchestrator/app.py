# services/orchestrator/app.py
from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from core.llm_worker_types import LLM_WORKER_TYPE_SDK
from .job_store import JobStore
from .llm_workers import choose_worker_type, get_runtime_status, set_config
from .schemas import (
    ActiveJobsResponse,
    ActiveJobSummary,
    JobState,
    KillJobRequest,
    KillJobResponse,
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


def _patch_request_compat(store: JobStore, job_id: str, patch: dict) -> None:
    """
    Keep enqueue path compatible with older JobStore revisions that do not
    expose patch_request(), while still persisting llm_worker_type in request.
    """
    patch_request_fn = getattr(store, "patch_request", None)
    if callable(patch_request_fn):
        patch_request_fn(job_id, patch)
        return

    st = store.get(job_id)
    if not st:
        return

    req = dict(st.request or {})
    req.update(patch or {})
    payload = st.model_dump(mode="json")
    payload["request"] = req
    payload["updated_at"] = time.time()
    st2 = JobState.model_validate(payload)

    put_fn = getattr(store, "_put", None)
    if not callable(put_fn):
        raise RuntimeError("job_store_missing_patch_request_and_put")
    put_fn(st2)


def _iter_celery_tasks(raw_tasks: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw_tasks, list):
        return out
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        req = item.get("request")
        if isinstance(req, dict):
            out.append(req)
            continue
        out.append(item)
    return out


def _get_celery_app():
    from .celery_app import celery_app  # local import: keeps tests importable without celery installed

    return celery_app


def _celery_task_matches_job_id(task: dict[str, Any], job_id: str) -> bool:
    target = str(job_id or "").strip()
    if not target:
        return False

    args = task.get("args")
    if isinstance(args, (list, tuple)) and args:
        if str(args[0]).strip() == target:
            return True

    kwargs = task.get("kwargs")
    if isinstance(kwargs, dict):
        if str(kwargs.get("job_id") or "").strip() == target:
            return True

    for key in ("args", "kwargs", "argsrepr", "kwargsrepr"):
        value = task.get(key)
        if isinstance(value, str) and target in value:
            return True
    return False


def _revoke_celery_tasks_for_job(job_id: str) -> list[str]:
    revoked: list[str] = []
    seen: set[str] = set()

    celery_app = _get_celery_app()
    inspector = celery_app.control.inspect(timeout=1.5)
    if inspector is None:
        return revoked

    snapshots: list[dict[str, Any]] = []
    for getter_name in ("active", "reserved", "scheduled"):
        getter = getattr(inspector, getter_name, None)
        if not callable(getter):
            continue
        snapshot = getter()
        if isinstance(snapshot, dict):
            snapshots.append(snapshot)

    for snapshot in snapshots:
        for worker_tasks in snapshot.values():
            for task in _iter_celery_tasks(worker_tasks):
                if not _celery_task_matches_job_id(task, job_id):
                    continue
                task_id = str(task.get("id") or "").strip()
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                revoked.append(task_id)
    return revoked


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
        if not created:
            return SendVideoResponse(job_id=st.job_id, status=st.status, created=False)

        try:
            selected = choose_worker_type(store, requested=req.llm_worker_type)
            worker_type = selected.worker_type
            _patch_request_compat(store, st.job_id, {"llm_worker_type": worker_type})
            store.set_status(
                st.job_id,
                "QUEUED",
                stage="build",
                result={"llm_worker_type": worker_type},
            )
            if worker_type == "sdk":
                build_job_sdk.delay(st.job_id)
            elif worker_type == "openrouter":
                build_job_openrouter.delay(st.job_id)
            elif worker_type == "hybrid":
                build_job_hybrid.delay(st.job_id)
            else:
                raise RuntimeError(f"unsupported llm_worker_type: {worker_type}")
        except Exception as e:
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

    @app.get("/jobs/active", response_model=ActiveJobsResponse)
    def list_active_jobs(min_age_seconds: int = 900, limit: int = 100) -> ActiveJobsResponse:
        min_age = max(0, min(int(min_age_seconds), 604800))
        out_limit = max(1, min(int(limit), 500))
        now = time.time()

        rows: list[ActiveJobSummary] = []
        for st in store.list_jobs():
            if st.status not in {"NEW", "QUEUED", "RUNNING"}:
                continue

            updated_at = float(st.updated_at or st.created_at or now)
            age_seconds = max(0, int(now - updated_at))
            if age_seconds < min_age:
                continue

            req = st.request or {}
            rows.append(
                ActiveJobSummary(
                    job_id=st.job_id,
                    status=st.status,
                    stage=st.stage,
                    project_id=str(req.get("project_id") or ""),
                    llm_worker_type=str(req.get("llm_worker_type") or ""),
                    idempotency_key=str(st.idempotency_key or req.get("idempotency_key") or ""),
                    created_at=float(st.created_at),
                    updated_at=updated_at,
                    age_seconds=age_seconds,
                )
            )

        rows.sort(key=lambda row: row.age_seconds, reverse=True)
        return ActiveJobsResponse(
            jobs=rows[:out_limit],
            total_active=len(rows),
            min_age_seconds=min_age,
            limit=out_limit,
        )

    @app.post("/jobs/{job_id}/kill", response_model=KillJobResponse)
    def kill_job(job_id: str, payload: KillJobRequest) -> KillJobResponse:
        jid = str(job_id or "").strip()
        if not jid:
            raise HTTPException(status_code=400, detail="job_id is empty")

        st = store.get(jid)
        if not st:
            raise HTTPException(status_code=404, detail="job not found")

        prev_status = st.status
        if prev_status in {"SUCCEEDED", "FAILED"}:
            raise HTTPException(status_code=409, detail=f"job already terminal: {prev_status}")

        reason = str(payload.reason or "").strip() or "admin_kill_stuck"
        req = st.request or {}
        project_id = str(req.get("project_id") or "")

        try:
            revoked_task_ids = _revoke_celery_tasks_for_job(jid)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"failed_to_revoke_celery_tasks: {e!r}") from e

        st2 = store.set_status(
            jid,
            "FAILED",
            stage="admin_kill_stuck",
            error=f"admin_kill_stuck: {reason}",
            result={
                "killed_by_admin": True,
                "kill_reason": reason,
                "revoked_task_ids": revoked_task_ids,
            },
        )
        if not st2:
            raise HTTPException(status_code=404, detail="job not found")

        return KillJobResponse(
            job_id=jid,
            previous_status=prev_status,
            new_status=st2.status,
            stage=str(st2.stage or "admin_kill_stuck"),
            reason=reason,
            revoked_task_ids=revoked_task_ids,
            project_id=project_id,
        )

    @app.get("/jobs/{job_id}", response_model=JobState)
    def get_job(job_id: str) -> JobState:
        st = store.get(job_id)
        if not st:
            raise HTTPException(status_code=404, detail="job not found")
        return st

    @app.get("/llm-workers", response_model=LLMWorkersStatusResponse)
    def get_llm_workers() -> LLMWorkersStatusResponse:
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

    # Serve built frontend (if exists)
    _ui_dist = Path(__file__).resolve().parents[2] / "asset_ui" / "dist"
    if _ui_dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/asset-ui", StaticFiles(directory=str(_ui_dist), html=True), name="asset-ui-static")

    return app


app = create_app()
