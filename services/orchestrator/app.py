# services/orchestrator/app.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from core.llm_worker_types import (
    LLM_WORKER_TYPE_VERTEX_SDK_MIX,
    normalize_llm_worker_type,
)
from .job_store import JobStore
from .llm_workers import (
    ensure_config_initialized,
    get_inflight_counts,
    get_runtime_status,
    release_worker_slot,
    reserve_worker_type,
    set_config,
)
from .observability_metrics import get_counter_map
from .prometheus_metrics import build_prometheus_metrics_payload
from .payment_webhook import make_payment_router
from .schemas import (
    ActiveJobsResponse,
    ActiveJobSummary,
    JobState,
    KillJobRequest,
    KillJobResponse,
    LLMWorkerRuntimeStatus,
    LLMWorkersConfigRequest,
    LLMWorkersStatusResponse,
    RequeueJobRequest,
    RequeueJobResponse,
    SendVideoRequest,
    SendVideoResponse,
    WindowsNodesStatusResponse,
    WindowsNodesUpdateRequest,
)
from .tasks import (
    build_job_hybrid,
    build_job_openrouter,
    build_job_sdk,
    build_job_vertex_sdk_mix,
)
from .config import SETTINGS
from .bundle_bootstrap import ensure_descriptions_bundle
from .asset_routes import create_asset_router
from .ops_alert_subscribers import OpsAlertBotPoller, OpsAlertSubscriberStore
from .windows_node_pool import WindowsNodePool, parse_windows_urls_csv
from services.tg_bot_botapi.user_store import UserStore

log = logging.getLogger(__name__)


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
    _bundle_ok = False
    _payment_enabled = bool(SETTINGS.payment_webhook_secret or SETTINGS.payment_admin_token)

    def _default_windows_urls() -> list[str]:
        raw = ",".join(
            [
                str(SETTINGS.windows_base_url or "").strip(),
                str(SETTINGS.windows_base_urls_csv or "").strip(),
            ]
        ).strip(",")
        return parse_windows_urls_csv(raw)

    def _windows_pool() -> WindowsNodePool:
        return WindowsNodePool(
            redis_client=store.r,
            key_prefix=store.key_prefix,
            lease_ttl_s=SETTINGS.windows_node_lease_ttl_s,
        )

    def _build_windows_nodes_status(*, runtime_nodes: list[dict[str, Any]]) -> WindowsNodesStatusResponse:
        default_urls = _default_windows_urls()
        effective_nodes = runtime_nodes or _windows_pool().get_effective_nodes(default_urls=default_urls)
        runtime_urls = [
            str(node.get("url") or "")
            for node in runtime_nodes
            if bool(node.get("enabled", True)) and str(node.get("url") or "").strip()
        ]
        effective_urls = [
            str(node.get("url") or "")
            for node in effective_nodes
            if bool(node.get("enabled", True)) and str(node.get("url") or "").strip()
        ]
        inflight = _windows_pool().inflight_snapshot(effective_urls)
        return WindowsNodesStatusResponse(
            source="runtime" if runtime_nodes else "env",
            default_urls=default_urls,
            runtime_urls=runtime_urls,
            effective_urls=effective_urls,
            nodes=effective_nodes,
            inflight=inflight,
        )

    # Payment webhook/router DB + persistent ops alert subscribers.
    _user_store: UserStore | None = None
    _ops_alert_store: OpsAlertSubscriberStore | None = None
    _ops_alert_poller_task: asyncio.Task[None] | None = None
    _ops_alert_poller_stop: asyncio.Event | None = None

    @app.on_event("startup")
    async def _init_db() -> None:
        nonlocal _user_store, _ops_alert_store, _ops_alert_poller_task, _ops_alert_poller_stop
        if SETTINGS.credits_db_url:
            _user_store = UserStore(SETTINGS.credits_db_url)
            await _user_store.init()
            _ops_alert_store = OpsAlertSubscriberStore(_user_store.pool)
            await _ops_alert_store.init_schema()

            if SETTINGS.alert_subscribers_enabled and SETTINGS.alert_telegram_bot_token:
                _ops_alert_poller_stop = asyncio.Event()
                poller = OpsAlertBotPoller(
                    bot_token=SETTINGS.alert_telegram_bot_token,
                    store=_ops_alert_store,
                    poll_timeout_s=SETTINGS.alert_subscribers_poll_timeout_s,
                    retry_sleep_s=SETTINGS.alert_subscribers_retry_sleep_s,
                )
                _ops_alert_poller_task = asyncio.create_task(
                    poller.run(_ops_alert_poller_stop),
                    name="ops_alert_bot_poller",
                )
                log.info("ops_alert_poller_started enabled=true")
            elif SETTINGS.alert_subscribers_enabled:
                log.warning("ops_alert_poller_not_started reason=empty_alert_telegram_bot_token")

    @app.on_event("shutdown")
    async def _close_db() -> None:
        nonlocal _ops_alert_poller_task, _ops_alert_poller_stop
        if _ops_alert_poller_stop is not None:
            _ops_alert_poller_stop.set()
        if _ops_alert_poller_task is not None:
            try:
                await asyncio.wait_for(_ops_alert_poller_task, timeout=6.0)
            except Exception:
                _ops_alert_poller_task.cancel()
                try:
                    await _ops_alert_poller_task
                except Exception:
                    pass
        if _user_store is not None:
            await _user_store.close()

    if _payment_enabled:
        # Router uses _user_store which is set by startup event before first request.
        # We pass a lambda so the router always gets the current value.
        class _LazyUserStore:
            """Thin proxy so payment router works even though pool isn't ready at import time."""
            async def ensure_profile(self, *a, **kw):  # type: ignore[override]
                if _user_store is None:
                    raise RuntimeError("payment_router_not_ready: credits db pool is not initialized")
                return await _user_store.ensure_profile(*a, **kw)

            async def confirm_payment(self, *a, **kw):  # type: ignore[override]
                if _user_store is None:
                    raise RuntimeError("payment_router_not_ready: credits db pool is not initialized")
                return await _user_store.confirm_payment(*a, **kw)

            async def manual_activate(self, *a, **kw):  # type: ignore[override]
                if _user_store is None:
                    raise RuntimeError("payment_router_not_ready: credits db pool is not initialized")
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
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        try:
            store.r.ping()
            checks["redis"] = True
        except Exception:
            checks["redis"] = False
            details["redis"] = "ping_failed"

        checks["bundle"] = _bundle_ok
        if not _bundle_ok:
            details["bundle"] = "descriptions_bundle_not_ready"

        if _payment_enabled:
            has_db_url = bool(str(SETTINGS.credits_db_url or "").strip())
            payment_ready = has_db_url and (_user_store is not None)
            checks["payment_db_ready"] = payment_ready
            if not has_db_url:
                details["payment_db_ready"] = "CREDITS_DB_URL missing"
            elif _user_store is None:
                details["payment_db_ready"] = "pool_not_initialized"

        try:
            llm_status = get_runtime_status(store)
            llm_ready = any(
                bool(row.enabled) and int(row.weight) > 0 and int(row.max_inflight) > 0
                for row in llm_status.values()
            )
            checks["llm_admission_ready"] = llm_ready
            if not llm_ready:
                details["llm_admission_ready"] = "no_enabled_types_or_zero_useful_weight"
        except Exception as exc:
            checks["llm_admission_ready"] = False
            details["llm_admission_ready"] = f"runtime_status_error: {exc!r}"

        ok = all(checks.values())
        return {"ok": ok, "checks": checks, "details": details}

    @app.get("/ops/alert-subscribers")
    async def ops_alert_subscribers_status() -> dict[str, Any]:
        if _ops_alert_store is None:
            return {"enabled": False, "count": 0, "items": []}
        items = await _ops_alert_store.list_active(limit=SETTINGS.alert_subscribers_max_chat_ids)
        return {
            "enabled": bool(SETTINGS.alert_subscribers_enabled),
            "count": len(items),
            "items": items,
        }

    @app.get("/windows-nodes", response_model=WindowsNodesStatusResponse)
    def get_windows_nodes() -> WindowsNodesStatusResponse:
        runtime_nodes = _windows_pool().get_runtime_nodes()
        return _build_windows_nodes_status(runtime_nodes=runtime_nodes)

    @app.put("/windows-nodes", response_model=WindowsNodesStatusResponse)
    def put_windows_nodes(req: WindowsNodesUpdateRequest) -> WindowsNodesStatusResponse:
        pool = _windows_pool()
        if req.nodes:
            runtime_nodes = pool.set_runtime_nodes(
                [
                    {
                        "url": str(node.url),
                        "enabled": bool(node.enabled),
                        "disabled_reason": str(node.disabled_reason or ""),
                        "disabled_at": node.disabled_at,
                    }
                    for node in req.nodes
                ]
            )
        else:
            pool.set_active_urls(req.urls)
            runtime_nodes = pool.get_runtime_nodes()
        return _build_windows_nodes_status(runtime_nodes=runtime_nodes)

    def _enqueue_build_task(job_id: str, worker_type: str) -> None:
        wt = normalize_llm_worker_type(worker_type)
        task_map = {
            "sdk": build_job_sdk,
            "openrouter": build_job_openrouter,
            "hybrid": build_job_hybrid,
            "vertex_sdk_mix": build_job_vertex_sdk_mix,
        }
        task = task_map.get(wt)
        if task is None:
            raise RuntimeError(f"unsupported llm_worker_type: {worker_type}")
        task.delay(job_id)

    def _ensure_accepting_new_jobs() -> None:
        if not bool(SETTINGS.system_maintenance_mode):
            return
        msg = str(SETTINGS.system_maintenance_message or "").strip()
        detail = msg or "Service is temporarily unavailable due to maintenance."
        raise HTTPException(status_code=503, detail=detail)

    # ==========================================================
    # NEW: correct naming (audio URL -> enqueue pipeline)
    # ==========================================================
    # Мы используем те же модели SendVideoRequest/SendVideoResponse,
    # чтобы не править сразу весь проект.
    # Позже можешь переименовать модели в schemas.py, но эндпоинт уже будет правильный.
    @app.post("/send_audio_s3", response_model=SendVideoResponse)
    def send_audio_s3(req: SendVideoRequest) -> SendVideoResponse:
        _ensure_accepting_new_jobs()
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
            _enqueue_build_task(st.job_id, worker_type)
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

    @app.post("/jobs/{job_id}/requeue", response_model=RequeueJobResponse)
    def requeue_job(job_id: str, payload: RequeueJobRequest) -> RequeueJobResponse:
        jid = str(job_id or "").strip()
        if not jid:
            raise HTTPException(status_code=400, detail="job_id is empty")

        st = store.get(jid)
        if not st:
            raise HTTPException(status_code=404, detail="job not found")

        prev_status = st.status
        if prev_status == "SUCCEEDED":
            raise HTTPException(status_code=409, detail="job already succeeded")

        reason = str(payload.reason or "").strip() or "admin_requeue_stuck"
        req = st.request or {}
        project_id = str(req.get("project_id") or "")
        requested_worker_raw = str(payload.llm_worker_type or "").strip()
        current_worker_raw = str(req.get("llm_worker_type") or "").strip()

        requested_worker = ""
        current_worker = ""
        try:
            if requested_worker_raw:
                requested_worker = normalize_llm_worker_type(requested_worker_raw)
            if current_worker_raw:
                current_worker = normalize_llm_worker_type(current_worker_raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid llm_worker_type: {e}") from e

        selected_worker = requested_worker or current_worker
        if not selected_worker:
            raise HTTPException(
                status_code=400,
                detail="llm_worker_type is required (missing in job request and payload)",
            )

        try:
            revoked_task_ids = _revoke_celery_tasks_for_job(jid)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"failed_to_revoke_celery_tasks: {e!r}") from e

        reserved_new_slot = False
        is_active = prev_status in {"QUEUED", "RUNNING"}

        if is_active:
            if requested_worker and current_worker and requested_worker != current_worker:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "cannot change llm_worker_type while job is active; "
                        f"current={current_worker} requested={requested_worker}"
                    ),
                )
            selected_worker = current_worker or requested_worker
        else:
            try:
                selected = reserve_worker_type(store, requested=selected_worker)
            except Exception as e:
                msg = str(e)
                if "capacity_exhausted" in msg or "disabled" in msg or "no_enabled_types" in msg:
                    raise HTTPException(status_code=503, detail=f"LLM workers capacity issue: {msg}") from e
                raise HTTPException(status_code=500, detail=f"failed_to_reserve_worker: {msg}") from e
            selected_worker = selected.worker_type
            reserved_new_slot = True

        requeue_attempt = 1
        if isinstance(st.result, dict):
            try:
                prev_attempt = int(st.result.get("admin_requeue_attempt") or 0)
                requeue_attempt = max(1, prev_attempt + 1)
            except Exception:
                requeue_attempt = 1

        queued = False
        try:
            store.patch_request(jid, {"llm_worker_type": selected_worker})
            st2 = store.set_status(
                jid,
                "QUEUED",
                stage="build",
                error=f"admin_requeued: {reason}",
                result={
                    "llm_worker_type": selected_worker,
                    "admin_requeue_attempt": requeue_attempt,
                    "admin_requeue_reason": reason,
                    "admin_requeue_revoked_task_ids": revoked_task_ids,
                },
            )
            if not st2:
                raise HTTPException(status_code=404, detail="job not found")
            queued = True
            _enqueue_build_task(jid, selected_worker)
        except HTTPException:
            if reserved_new_slot and not queued:
                try:
                    release_worker_slot(store, selected_worker)
                except Exception:
                    pass
            raise
        except Exception as e:
            if reserved_new_slot and not queued:
                try:
                    release_worker_slot(store, selected_worker)
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail=f"failed_to_requeue_job: {e!r}") from e

        return RequeueJobResponse(
            job_id=jid,
            previous_status=prev_status,
            new_status="QUEUED",
            stage="build",
            reason=reason,
            llm_worker_type=selected_worker,
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
        ensure_config_initialized(store)
        status = get_runtime_status(store)
        workers = {
            worker_type: LLMWorkerRuntimeStatus.model_validate(row.model_dump(mode="json"))
            for worker_type, row in status.items()
        }
        return LLMWorkersStatusResponse(
            workers=workers,
            default_worker_type=LLM_WORKER_TYPE_VERTEX_SDK_MIX,
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
            default_worker_type=LLM_WORKER_TYPE_VERTEX_SDK_MIX,
        )

    @app.get("/metrics")
    def metrics() -> dict:
        """Lightweight observability endpoint for queue/job/webhook health."""
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
        queue_depth = int(counts.get("QUEUED", 0))
        inflight_jobs = int(counts.get("RUNNING", 0))
        failed_jobs = int(counts.get("FAILED", 0))

        llm_inflight: dict = {}
        llm_inflight_error: str | None = None
        try:
            llm_inflight = get_inflight_counts(store)
        except Exception as exc:
            llm_inflight_error = repr(exc)

        queues: dict = {}
        try:
            inspect = _celery.control.inspect(timeout=1.0)
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            for worker, tasks in active.items():
                queues[worker] = {"active": len(tasks), "reserved": len(reserved.get(worker, []))}
        except Exception:
            queues["error"] = "inspect_failed"

        webhook_outcomes: dict = {}
        activate_outcomes: dict = {}
        render_poll_timeout_outcomes: dict = {}
        metrics_error: str | None = None
        try:
            webhook_outcomes = get_counter_map(store, metric="payment_webhook_outcomes")
            activate_outcomes = get_counter_map(store, metric="payment_activate_outcomes")
            render_poll_timeout_outcomes = get_counter_map(
                store,
                metric="render_poll_timeout_outcomes",
            )
        except Exception as exc:
            metrics_error = repr(exc)

        return {
            "queue_depth": queue_depth,
            "inflight_jobs": inflight_jobs,
            "failed_jobs": failed_jobs,
            "job_status_counts": counts,
            "job_status_error": jobs_error,
            "llm_inflight_by_worker_type": llm_inflight,
            "llm_inflight_error": llm_inflight_error,
            "workers": queues,
            "webhook_outcomes": webhook_outcomes,
            "activate_outcomes": activate_outcomes,
            "render_poll_timeout_outcomes": render_poll_timeout_outcomes,
            "metrics_error": metrics_error,
            "bundle_ok": _bundle_ok,
        }

    @app.get("/metrics/prometheus")
    def metrics_prometheus() -> Response:
        payload, content_type = build_prometheus_metrics_payload(store)
        return Response(content=payload, media_type=content_type)

    # Serve built frontend (if exists)
    _ui_dist = Path(__file__).resolve().parents[2] / "asset_ui" / "dist"
    if _ui_dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/asset-ui", StaticFiles(directory=str(_ui_dist), html=True), name="asset-ui-static")

    return app


app = create_app()
