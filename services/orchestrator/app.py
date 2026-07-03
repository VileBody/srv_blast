# services/orchestrator/app.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from core.llm_worker_types import (
    LLM_WORKER_TYPE_VERTEX_SDK_MIX,
    normalize_llm_worker_type,
)
from core.queue_estimate import (
    DEFAULT_QUEUE_ESTIMATE_WINDOW,
    build_queue_estimate,
    normalize_queue_estimate_window,
)
from .job_store import JobStore
from .llm_workers import (
    ensure_config_initialized,
    ensure_enqueue_worker_available,
    get_inflight_counts,
    get_runtime_status,
    select_worker_type,
    set_config,
)
from .observability_metrics import get_counter_map
from .prometheus_metrics import build_prometheus_metrics_payload
from .payment_webhook import make_payment_router
from .runtime_config import (
    build_capacity_policy_snapshot,
    build_llm_saturation,
    get_runtime_config,
    get_runtime_values,
    set_runtime_config,
)
from .schemas import (
    ActiveJobsResponse,
    ActiveJobSummary,
    HookAnalyzeRequest,
    HookAnalyzeResponse,
    JobState,
    JobsBatchRequest,
    JobsBatchResponse,
    KillJobRequest,
    KillJobResponse,
    LLMWorkerRuntimeStatus,
    LLMWorkersConfigRequest,
    LLMWorkersStatusResponse,
    QueueEstimateResponse,
    RankBucketsRequest,
    RankBucketsResponse,
    RankedBucket,
    RequeueJobRequest,
    RequeueJobResponse,
    SendVideoRequest,
    SendVideoResponse,
    WindowsNodesStatusResponse,
    WindowsNodesUpdateRequest,
)
from .tasks import (
    build_job,
    build_job_hybrid,
    build_job_openrouter,
    build_job_sdk,
    build_job_vertex_sdk_mix,
)
from .backpressure_policy import compute_capacity_policy
from .config import SETTINGS, derive_render_poll_queue
from .bundle_bootstrap import ensure_descriptions_bundle
from .asset_routes import create_asset_router
from .ops_alert_subscribers import OpsAlertBotPoller, OpsAlertSubscriberStore
from .windows_node_pool import WindowsNodePool, parse_windows_urls_csv
from services.tg_bot_botapi.user_store import UserStore

log = logging.getLogger(__name__)


def _maintenance_bypass_allowed(req: object) -> bool:
    expected = str(getattr(SETTINGS, "system_maintenance_bypass_token", "") or "").strip()
    provided = str(getattr(req, "maintenance_bypass_token", "") or "").strip()
    return bool(expected) and bool(provided) and provided == expected


def _maintenance_message_detail() -> str:
    detail = str(getattr(SETTINGS, "system_maintenance_message", "") or "").strip()
    return detail or "Service is temporarily unavailable due to maintenance."


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


def _ranker_cache_ttl_s() -> int:
    """TTL for cached bucket rankings. Default 30 days; the ranking is
    deterministic per (lyrics, mood, catalog, model) so a long TTL is safe —
    the key itself invalidates on any input change."""
    import os

    raw = (os.environ.get("FOOTAGE_RANKER_CACHE_TTL_S") or "").strip()
    try:
        return max(60, int(raw)) if raw else 2592000
    except Exception:
        return 2592000


def _ranker_cache_enabled() -> bool:
    import os

    raw = (os.environ.get("FOOTAGE_RANKER_CACHE_ENABLED") or "").strip().lower()
    if not raw:
        return True  # Redis-backed, cheap, fail-safe → on by default
    return raw in {"1", "true", "yes", "on"}


def _ranker_cache_get(redis_client: Any, key: str) -> "list[str] | None":
    """Return the cached ranked id list, or None on miss / any Redis error.
    Never raises — a cache problem must not break the ranker endpoint."""
    if not _ranker_cache_enabled():
        return None
    try:
        import json

        raw = redis_client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, list) and all(isinstance(x, str) for x in data):
            return data
        return None
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("ranker_cache_get_failed key=%s err=%r", key, exc)
        return None


def _ranker_cache_set(redis_client: Any, key: str, ranked_ids: "list[str]") -> None:
    """Store the ranked id list with TTL. Never raises."""
    if not _ranker_cache_enabled():
        return
    try:
        import json

        redis_client.set(key, json.dumps(list(ranked_ids)), ex=_ranker_cache_ttl_s())
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("ranker_cache_set_failed key=%s err=%r", key, exc)


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
                    api_env=SETTINGS.alert_telegram_api_env,
                    proxy_url=SETTINGS.tg_file_proxy_url,
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

    def _resolve_job_routing(*, request_payload: Dict[str, Any]) -> Dict[str, str]:
        local_origin_node = str(SETTINGS.orchestrator_node_name or "").strip()
        local_build_queue = str(SETTINGS.celery_queue_build or "").strip()
        local_render_queue = str(SETTINGS.celery_queue_render or "").strip()
        local_render_poll_queue = str(SETTINGS.celery_queue_render_poll or "").strip() or derive_render_poll_queue(
            local_render_queue
        )

        origin_node = str(request_payload.get("origin_node") or "").strip() or local_origin_node
        build_queue = str(request_payload.get("build_queue") or "").strip() or local_build_queue
        render_queue = str(request_payload.get("render_queue") or "").strip() or local_render_queue
        render_poll_queue = str(request_payload.get("render_poll_queue") or "").strip()

        reuse_text_job_id = str(request_payload.get("reuse_text_job_id") or "").strip()
        if reuse_text_job_id:
            source_state = store.get(reuse_text_job_id)
            if not source_state:
                raise HTTPException(
                    status_code=409,
                    detail=f"reuse_text_source_job_not_found: {reuse_text_job_id}",
                )
            source_req = source_state.request or {}
            source_origin_node = str(source_req.get("origin_node") or "").strip()
            source_build_queue = str(source_req.get("build_queue") or "").strip()
            source_render_queue = str(source_req.get("render_queue") or "").strip()
            source_render_poll_queue = str(source_req.get("render_poll_queue") or "").strip()
            if source_origin_node:
                origin_node = source_origin_node
            if source_build_queue:
                build_queue = source_build_queue
            if source_render_queue:
                render_queue = source_render_queue
            if source_render_poll_queue:
                render_poll_queue = source_render_poll_queue

        if not render_poll_queue:
            render_poll_queue = derive_render_poll_queue(render_queue or local_render_queue)
        if not render_poll_queue:
            render_poll_queue = local_render_poll_queue

        if not build_queue:
            raise HTTPException(status_code=500, detail="CELERY_QUEUE_BUILD is empty")
        if not render_queue:
            raise HTTPException(status_code=500, detail="CELERY_QUEUE_RENDER is empty")
        if not render_poll_queue:
            raise HTTPException(status_code=500, detail="CELERY_QUEUE_RENDER_POLL is empty")

        return {
            "origin_node": origin_node,
            "build_queue": build_queue,
            "render_queue": render_queue,
            "render_poll_queue": render_poll_queue,
        }

    def _enqueue_build_task(job_id: str, worker_type: str | None, *, queue: str = "") -> None:
        wt = normalize_llm_worker_type(worker_type) if str(worker_type or "").strip() else ""
        task_map = {
            "": build_job,
            "sdk": build_job_sdk,
            "openrouter": build_job_openrouter,
            "hybrid": build_job_hybrid,
            "vertex_sdk_mix": build_job_vertex_sdk_mix,
        }
        task = task_map.get(wt, build_job)
        if task is None:
            raise RuntimeError(f"unsupported llm_worker_type: {worker_type}")
        target_queue = str(queue or "").strip()
        default_queue = str(SETTINGS.celery_queue_build or "").strip()
        if target_queue and target_queue != default_queue:
            task.apply_async(args=[job_id], queue=target_queue)
            return
        task.delay(job_id)

    def _ensure_accepting_new_jobs(req: object | None = None) -> None:
        if not bool(SETTINGS.system_maintenance_mode):
            if bool(getattr(SETTINGS, "orchestrator_enqueue_enabled", True)):
                return
            node_name = str(getattr(SETTINGS, "orchestrator_node_name", "") or "").strip()
            detail = "enqueue disabled on this orchestrator"
            if node_name:
                detail = f"enqueue disabled on node={node_name}"
            raise HTTPException(status_code=503, detail=detail)
        if _maintenance_bypass_allowed(req) and bool(getattr(SETTINGS, "orchestrator_enqueue_enabled", True)):
            return
        detail = _maintenance_message_detail()
        raise HTTPException(status_code=503, detail=detail)

    def _ensure_supported_mode(mode: str) -> None:
        selected_mode = str(mode or "with_gemini").strip().lower() or "with_gemini"
        if selected_mode == "with_gemini":
            return
        if selected_mode == "no_gemini":
            raise HTTPException(
                status_code=400,
                detail="mode=no_gemini is disabled; use mode=with_gemini",
            )
        raise HTTPException(
            status_code=400,
            detail=f"unsupported mode={selected_mode!r}; expected with_gemini",
        )

    # ==========================================================
    # NEW: correct naming (audio URL -> enqueue pipeline)
    # ==========================================================
    # Мы используем те же модели SendVideoRequest/SendVideoResponse,
    # чтобы не править сразу весь проект.
    # Позже можешь переименовать модели в schemas.py, но эндпоинт уже будет правильный.
    @app.post("/send_audio_s3", response_model=SendVideoResponse)
    def send_audio_s3(req: SendVideoRequest) -> SendVideoResponse:
        _ensure_accepting_new_jobs(req)
        _ensure_supported_mode(req.mode)
        request_payload = req.model_dump(mode="json", exclude_none=True)
        routing = _resolve_job_routing(request_payload=request_payload)
        request_payload.update(routing)
        st, created = store.new_job(
            request=request_payload,
            idempotency_key=req.idempotency_key,
        )
        if not created:
            return SendVideoResponse(job_id=st.job_id, status=st.status, created=False)

        try:
            requested_worker_type = ensure_enqueue_worker_available(store, requested=req.llm_worker_type)

            request_patch: Dict[str, Any] = {
                "llm_reservation_mode": "worker",
                "origin_node": routing["origin_node"],
                "build_queue": routing["build_queue"],
                "render_queue": routing["render_queue"],
                "render_poll_queue": routing["render_poll_queue"],
            }
            if requested_worker_type:
                request_patch["llm_worker_type"] = requested_worker_type

            store.patch_request(st.job_id, request_patch)

            result_payload: Dict[str, Any] = {
                "routing": {
                    "origin_node": routing["origin_node"],
                    "build_queue": routing["build_queue"],
                    "render_queue": routing["render_queue"],
                    "render_poll_queue": routing["render_poll_queue"],
                },
                "llm_reservation_mode": "worker",
            }
            if requested_worker_type:
                result_payload["llm_worker_type"] = requested_worker_type
            store.set_status(
                st.job_id,
                "QUEUED",
                stage="build",
                result=result_payload,
            )
            _enqueue_build_task(st.job_id, requested_worker_type or None, queue=routing["build_queue"])
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

    # ==========================================================
    # Hook focus-clip analysis (F4 «Движение» picker).
    # Slim bots have no librosa → they call this; the orchestrator (runtime
    # image, has librosa) downloads the clip audio, runs analyze_focus_clip and
    # returns top drop candidates + bpm. Sync def => runs in the threadpool.
    # ==========================================================
    @app.post("/footage/rank-buckets", response_model=RankBucketsResponse)
    def rank_footage_buckets(req: RankBucketsRequest) -> RankBucketsResponse:
        """Rank footage buckets by relevance to the track lyrics. One cheap LLM
        call (Gemini Flash) with graceful heuristic fallback — never 500s."""
        import os

        # get_bucket_catalog is required for ANY response. If even that fails,
        # return an empty ranking rather than 500 — a 500 here pushes the bot into
        # the legacy artist-theme fallback (the symptom we're fixing).
        try:
            from mlcore.footage_bucket_catalog import get_bucket_catalog
            catalog = get_bucket_catalog()
        except Exception:
            log.exception("rank-buckets: catalog load failed — empty ranking")
            return RankBucketsResponse(buckets=[], used_llm=False)
        by_id = {b.bucket_id: b for b in catalog}

        def _safe_default_ids() -> list:
            # Deterministic, never-raising fallback: mood-matching buckets first
            # (if a mood was given), then the rest, in catalog order.
            m = (req.mood or "").strip().lower()
            matched = (
                [b.bucket_id for b in catalog if str(getattr(b, "mood", "") or "").strip().lower() == m]
                if m else []
            )
            seen = set(matched)
            return matched + [b.bucket_id for b in catalog if b.bucket_id not in seen]

        # The whole ranking is wrapped: ANY failure (ranker import, lexicon file,
        # heuristic bug, Redis cache) → safe default. This endpoint must NEVER
        # 500 and never return empty, whatever the input or environment.
        used_llm = False
        ranked_ids: list = []
        try:
            from mlcore.footage_bucket_ranker import (
                gemini_rank_call,
                rank_buckets,
                ranker_cache_key,
            )
            # Default: DETERMINISTIC ranking via the lyrics lexicon — no LLM call,
            # no cache (instant + stable). FOOTAGE_RANKER_LLM=1 → Gemini classifier
            # (cached, graceful fallback).
            use_llm = (os.environ.get("FOOTAGE_RANKER_LLM") or "0").strip().lower() in ("1", "true", "yes", "on")
            if not use_llm:
                ranked_ids = rank_buckets(lyrics=req.lyrics, mood=req.mood, catalog=catalog, llm_call=None)
            else:
                ranker_model = (os.environ.get("FOOTAGE_RANKER_MODEL") or "gemini-2.0-flash").strip()
                _cache_key = ranker_cache_key(
                    lyrics=req.lyrics, mood=req.mood, catalog=catalog, model=ranker_model
                )
                used_llm = True
                cached_ids = _ranker_cache_get(store.r, _cache_key)
                if cached_ids is not None:
                    used_llm = False  # served from cache, no LLM call this request
                    ranked_ids = [i for i in cached_ids if i in by_id]
                    if not ranked_ids:
                        cached_ids = None  # catalog drifted under the key → recompute
                if cached_ids is None:
                    try:
                        # raise_on_llm_error=True so a heuristic produced while Gemini
                        # is down is NOT cached (would be served for the full TTL).
                        ranked_ids = rank_buckets(
                            lyrics=req.lyrics,
                            mood=req.mood,
                            catalog=catalog,
                            llm_call=gemini_rank_call,
                            raise_on_llm_error=True,
                        )
                        used_llm = True
                        _ranker_cache_set(store.r, _cache_key, ranked_ids)
                    except Exception:
                        used_llm = False
                        ranked_ids = rank_buckets(
                            lyrics=req.lyrics, mood=req.mood, catalog=catalog, llm_call=None
                        )
        except Exception:
            log.exception("rank-buckets: ranking failed — safe default (mood-first catalog order)")
            ranked_ids = _safe_default_ids()
            used_llm = False

        # An empty ranking would also strand the bot → safe default.
        if not ranked_ids:
            ranked_ids = _safe_default_ids()

        if req.top and req.top > 0:
            ranked_ids = ranked_ids[: int(req.top)]
        items = [
            RankedBucket(
                bucket_id=b.bucket_id, theme=b.theme, tags_group=b.tags_group,
                mood=b.mood, label=b.label,
            )
            for b in (by_id[i] for i in ranked_ids if i in by_id)
        ]
        return RankBucketsResponse(buckets=items, used_llm=used_llm)

    @app.post("/hook/analyze", response_model=HookAnalyzeResponse)
    def hook_analyze(req: HookAnalyzeRequest) -> HookAnalyzeResponse:
        import tempfile
        from urllib.parse import urlparse

        url = str(req.audio_s3_url).strip()
        if not url.startswith("s3://"):
            raise HTTPException(status_code=400, detail="audio_s3_url must be an s3:// url")
        parsed = urlparse(url)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise HTTPException(status_code=400, detail=f"invalid s3 url: {url!r}")

        try:
            from src.storage.s3 import get_s3_client
            from mlcore.audio_analysis import analyze_focus_clip, to_jsonable  # noqa: F401

            with tempfile.TemporaryDirectory(prefix="hook_analyze_") as td:
                suffix = Path(key).suffix or ".mp3"
                local = Path(td) / f"audio{suffix}"
                get_s3_client().download_file(bucket, key, str(local))
                result = analyze_focus_clip(
                    audio_path=local,
                    clip_start_abs=float(req.clip_start_sec),
                    clip_end_abs=float(req.clip_end_sec),
                )
        except Exception as e:
            log.exception("hook_analyze failed url=%s window=%.3f..%.3f",
                          url, req.clip_start_sec, req.clip_end_sec)
            raise HTTPException(status_code=500, detail=f"hook analyze failed: {e}")

        cands = [
            {
                "t": float(c.t),
                "confidence": float(c.confidence),
                "snapped_to_beat": bool(c.snapped_to_beat),
                "source": str(c.source),
            }
            for c in (result.drop_candidates or [])[:3]
        ]
        return HookAnalyzeResponse(bpm=float(result.bpm), drop_candidates=cands)

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
        _ensure_accepting_new_jobs()
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
        pinned_origin_node = str(req.get("origin_node") or SETTINGS.orchestrator_node_name or "").strip()
        pinned_build_queue = str(req.get("build_queue") or SETTINGS.celery_queue_build or "").strip()
        pinned_render_queue = str(req.get("render_queue") or SETTINGS.celery_queue_render or "").strip()
        pinned_render_poll_queue = str(req.get("render_poll_queue") or "").strip()
        if not pinned_render_poll_queue:
            pinned_render_poll_queue = derive_render_poll_queue(pinned_render_queue or SETTINGS.celery_queue_render)
        if not pinned_render_poll_queue:
            pinned_render_poll_queue = str(SETTINGS.celery_queue_render_poll or "").strip()
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

        try:
            revoked_task_ids = _revoke_celery_tasks_for_job(jid)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"failed_to_revoke_celery_tasks: {e!r}") from e

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
                normalized_requested = selected_worker or None
                selected = select_worker_type(store, requested=normalized_requested) if normalized_requested else None
                ensure_enqueue_worker_available(store, requested=normalized_requested)
            except Exception as e:
                msg = str(e)
                if "capacity_exhausted" in msg or "disabled" in msg or "no_enabled_types" in msg:
                    raise HTTPException(status_code=503, detail=f"LLM workers capacity issue: {msg}") from e
                raise HTTPException(status_code=500, detail=f"failed_to_select_worker: {msg}") from e
            selected_worker = selected.worker_type if selected is not None else ""

        requeue_attempt = 1
        if isinstance(st.result, dict):
            try:
                prev_attempt = int(st.result.get("admin_requeue_attempt") or 0)
                requeue_attempt = max(1, prev_attempt + 1)
            except Exception:
                requeue_attempt = 1

        try:
            store.patch_request(
                jid,
                {
                    "llm_worker_type": selected_worker,
                    "llm_reservation_mode": "worker",
                    "origin_node": pinned_origin_node,
                    "build_queue": pinned_build_queue,
                    "render_queue": pinned_render_queue,
                    "render_poll_queue": pinned_render_poll_queue,
                },
            )
            result_payload: Dict[str, Any] = {
                "routing": {
                    "origin_node": pinned_origin_node,
                    "build_queue": pinned_build_queue,
                    "render_queue": pinned_render_queue,
                    "render_poll_queue": pinned_render_poll_queue,
                },
                "llm_reservation_mode": "worker",
                "admin_requeue_attempt": requeue_attempt,
                "admin_requeue_reason": reason,
                "admin_requeue_revoked_task_ids": revoked_task_ids,
            }
            if selected_worker:
                result_payload["llm_worker_type"] = selected_worker
            st2 = store.set_status(
                jid,
                "QUEUED",
                stage="build",
                error=f"admin_requeued: {reason}",
                result=result_payload,
            )
            if not st2:
                raise HTTPException(status_code=404, detail="job not found")
            _enqueue_build_task(jid, selected_worker or None, queue=pinned_build_queue)
        except HTTPException:
            raise
        except Exception as e:
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

    @app.get("/jobs/{job_id}/queue-estimate", response_model=QueueEstimateResponse)
    def get_job_queue_estimate(
        job_id: str,
        window: int = DEFAULT_QUEUE_ESTIMATE_WINDOW,
    ) -> QueueEstimateResponse:
        jid = str(job_id or "").strip()
        if not jid:
            raise HTTPException(status_code=400, detail="job_id is empty")

        if not store.get(jid):
            raise HTTPException(status_code=404, detail="job not found")

        snapshot = build_queue_estimate(
            store.list_jobs(),
            job_id=jid,
            window_size=normalize_queue_estimate_window(window),
        )
        if not snapshot:
            raise HTTPException(status_code=404, detail="job not found")
        return QueueEstimateResponse.model_validate(snapshot)

    @app.post("/jobs/batch", response_model=JobsBatchResponse)
    def get_jobs_batch(payload: JobsBatchRequest) -> JobsBatchResponse:
        job_ids: list[str] = []
        seen: set[str] = set()
        for raw in list(payload.job_ids or []):
            jid = str(raw or "").strip()
            if not jid or jid in seen:
                continue
            seen.add(jid)
            job_ids.append(jid)
        if not job_ids:
            raise HTTPException(status_code=400, detail="job_ids is empty")

        jobs: list[JobState] = []
        missing: list[str] = []
        for jid in job_ids:
            st = store.get(jid)
            if not st:
                missing.append(jid)
                continue
            jobs.append(st)
        if missing:
            missing_blob = ",".join(missing[:20])
            raise HTTPException(status_code=404, detail=f"jobs not found: {missing_blob}")
        return JobsBatchResponse(jobs=jobs, total=len(jobs))

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

    @app.get("/runtime-config")
    def get_runtime_config_endpoint() -> dict[str, Any]:
        try:
            return get_runtime_config(store)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"runtime_config_get_failed: {e!r}") from e

    @app.put("/runtime-config")
    def put_runtime_config_endpoint(payload: Dict[str, Any]) -> dict[str, Any]:
        values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail="payload must be an object or {values: object}")
        try:
            return set_runtime_config(store, values)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"runtime_config_update_failed: {e}") from e

    @app.get("/metrics")
    def metrics() -> dict:
        """Lightweight observability endpoint for queue/job/webhook health."""
        from .celery_app import celery_app as _celery

        counts: dict = {"NEW": 0, "QUEUED": 0, "RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0}
        stage_counts: dict[str, int] = {}
        render_backlog = 0
        build_backlog = 0
        jobs_error: str | None = None
        try:
            for job in store.list_jobs():
                s = str(getattr(job, "status", "") or "")
                if s in counts:
                    counts[s] += 1
                stage = str(getattr(job, "stage", "") or "none").strip().lower() or "none"
                stage_counts[stage] = int(stage_counts.get(stage, 0)) + 1
                if s in {"NEW", "QUEUED", "RUNNING"}:
                    if "render" in stage or stage in {"dispatch", "poll", "render_dispatch", "render_poll"}:
                        render_backlog += 1
                    else:
                        build_backlog += 1
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
        llm_saturation: dict[str, dict[str, object]] = {}
        llm_saturation_ratio: dict[str, dict[str, Any]] = {}
        llm_runtime_error: str | None = None
        try:
            llm_runtime: dict[str, Any] = {}
            for worker_type, row in get_runtime_status(store).items():
                enabled = bool(row.enabled)
                weight = int(row.weight)
                inflight = int(row.inflight)
                max_inflight = int(row.max_inflight)
                llm_runtime[str(worker_type)] = row.model_dump(mode="json")
                llm_saturation[str(worker_type)] = {
                    "enabled": enabled,
                    "weight": weight,
                    "inflight": inflight,
                    "max_inflight": max_inflight,
                    "available_slots": int(row.available_slots),
                    "saturated": bool(enabled and max_inflight > 0 and inflight >= max_inflight),
                }
            llm_saturation_ratio = build_llm_saturation(llm_runtime)
        except Exception as exc:
            if not llm_inflight_error:
                llm_inflight_error = repr(exc)
            llm_runtime_error = repr(exc)

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
        runtime_config_error: str | None = None
        runtime_policy_snapshot: dict[str, Any] = {}
        capacity_policy = compute_capacity_policy(
            render_backlog=int(render_backlog),
            build_backlog=int(build_backlog),
            llm_saturation_by_worker_type=llm_saturation,
            render_backlog_degraded_threshold=int(SETTINGS.render_backlog_degraded_threshold),
            render_backlog_scaleout_threshold=int(SETTINGS.render_backlog_scaleout_threshold),
            build_backlog_degraded_threshold=int(SETTINGS.build_backlog_degraded_threshold),
            build_backlog_manual_maintenance_threshold=int(SETTINGS.build_backlog_manual_maintenance_threshold),
        )
        try:
            runtime_values = get_runtime_values(store)
            capacity_policy = compute_capacity_policy(
                render_backlog=int(render_backlog),
                build_backlog=int(build_backlog),
                llm_saturation_by_worker_type=llm_saturation,
                render_backlog_degraded_threshold=int(
                    runtime_values.get("backpressure.render_backlog_degraded", SETTINGS.render_backlog_degraded_threshold)
                ),
                render_backlog_scaleout_threshold=int(
                    runtime_values.get("backpressure.render_backlog_add_windows_node", SETTINGS.render_backlog_scaleout_threshold)
                ),
                build_backlog_degraded_threshold=int(
                    runtime_values.get("backpressure.build_backlog_degraded", SETTINGS.build_backlog_degraded_threshold)
                ),
                build_backlog_manual_maintenance_threshold=int(
                    runtime_values.get(
                        "backpressure.build_backlog_maintenance_recommended",
                        SETTINGS.build_backlog_manual_maintenance_threshold,
                    )
                ),
            )
            runtime_policy_snapshot = build_capacity_policy_snapshot(
                values=runtime_values,
                job_status_counts=counts,
                job_stage_counts=stage_counts,
                llm_saturation_by_worker_type=llm_saturation_ratio,
            )
            if capacity_policy.get("state") != "normal":
                user_copy = str(runtime_policy_snapshot.get("user_degraded_copy") or "").strip()
                if user_copy:
                    capacity_policy["user_message"] = user_copy
            capacity_policy["runtime_config_snapshot"] = runtime_policy_snapshot
        except Exception as exc:
            runtime_config_error = repr(exc)
            runtime_values = {}
            try:
                runtime_policy_snapshot = build_capacity_policy_snapshot(
                    values=runtime_values,
                    job_status_counts=counts,
                    job_stage_counts=stage_counts,
                    llm_saturation_by_worker_type=llm_saturation_ratio,
                )
            except Exception:
                runtime_policy_snapshot = {}

        return {
            "queue_depth": queue_depth,
            "inflight_jobs": inflight_jobs,
            "failed_jobs": failed_jobs,
            "job_status_counts": counts,
            "job_stage_counts": stage_counts,
            "job_status_error": jobs_error,
            "render_backlog": int(render_backlog),
            "build_backlog": int(build_backlog),
            "capacity_policy": capacity_policy,
            "queue_topology": {
                "build_queue_default": str(SETTINGS.celery_queue_build or "").strip(),
                "render_queue_default": str(SETTINGS.celery_queue_render or "").strip(),
                "render_poll_queue_default": str(SETTINGS.celery_queue_render_poll or "").strip(),
                "render_poll_split_active": bool(
                    str(SETTINGS.celery_queue_render_poll or "").strip()
                    and str(SETTINGS.celery_queue_render_poll or "").strip()
                    != str(SETTINGS.celery_queue_render or "").strip()
                ),
            },
            "llm_inflight_by_worker_type": llm_inflight,
            "llm_saturation_by_worker_type": llm_saturation,
            "llm_saturation_ratio_by_worker_type": llm_saturation_ratio,
            "llm_inflight_error": llm_inflight_error,
            "llm_runtime_error": llm_runtime_error,
            "runtime_capacity_policy": runtime_policy_snapshot,
            "runtime_config_error": runtime_config_error,
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
