# services/orchestrator/tasks.py
from __future__ import annotations

import asyncio
import json
import re
import hashlib
import logging
import os
import shlex
import subprocess
import time
import urllib.request
import urllib.error
from urllib.parse import unquote
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional
import boto3
from botocore.config import Config

# Side-effect import: src/core/config/env.py на импорте форсит прокси-переменные
# (OUTBOUND_PROXY + HTTP(S)_PROXY, см. коммент там). Тянем это на самом верху
# tasks.py, чтобы override отработал при старте воркера — ДО первого S3-вызова в
# любой build-таске (иначе первый boto-клиент подхватил бы протухший HTTPS_PROXY
# из env_file раньше, чем ленивый import gemini_orchestrator внутри таски).
import src.core.config.env as _force_outbound_proxy  # noqa: F401,E402

from celery.signals import task_failure

from core.telegram_api import make_telegram_api

from .artifacts import make_job_paths
from .celery_app import celery_app
from .config import SETTINGS
from .job_store import JobStore
from .llm_workers import reserve_worker_type_for_job
from .observability_metrics import (
    STAGE_DURATION_BUCKETS,
    increment_counter,
    increment_labeled_counter,
    observe_labeled_histogram,
)
from .ops_alert_subscribers import (
    deactivate_chat_id_sync,
    fetch_active_chat_ids_sync,
    is_terminal_telegram_delivery_error,
)
from .render_manifest import build_rust_gen_job_payload, build_windows_job_payload
from .rust_gen_client import RustGenClient
from .runtime_config import get_runtime_values
from .windows_client import WindowsRenderClient
from .windows_node_pool import WindowsNodePool, parse_windows_urls_csv
from services.generation_runtime.store import resume_state_checksum
from core.llm_worker_types import (
    LLM_WORKER_TYPE_HYBRID,
    LLM_WORKER_TYPE_OPENROUTER,
    LLM_WORKER_TYPE_SDK,
    LLM_WORKER_TYPE_VERTEX_SDK_MIX,
    normalize_llm_worker_type,
)
from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS, normalize_subtitles_mode
from core.runtime_mode import MODE_PROD, get_runtime_mode


_REUSE_RESUME_STATE_KEYS = (
    "stage1_asr",
    "stage1_asr_mode",
    "stage1_asr_reference_text",
    "stage1_plan",
    "stage1_plan_source",
    "stage2_subtitles",
    "stage2_subtitles_mode",
    "stage2_switch_timestamps",
    "stage2_timing_mode",
    "stage2_fast_start_seconds",
)


_LLM_ENV_KEYS = (
    "DATA_DIR",
    "OUT_DIR",
    "AUDIO_FILE_PATH",
    "AUDIO_DIR",
    "AUDIO_FILE_NAME",
    "AE_MEDIA_MODE",
    "LLM_WORKER_TYPE",
    "LLM_PROVIDER_MODE",
    "JOB_ID",
    "LYRICS_TEXT",
    "TARGET_FRAGMENT",
    "SUBTITLES_MODE",
    "FOOTAGE_ARTIST_ID",
    "USER_CLIP_START_SEC",
    "USER_CLIP_END_SEC",
    "FOOTAGE_EXCLUDE_FILE_NAMES_JSON",
    "FOOTAGE_ROTATION_THEME",
    "FOOTAGE_ROTATION_GROUP",
    "STAGE2_SELECTION_SEED",
    "BATCH_VARIANT_INDEX",
    "BATCH_VARIANTS_TOTAL",
    "REUSE_TEXT_JOB_ID",
    "GEMINI_MAX_THINKING_TOKENS",
    # hook env vars (F3/F4/F5 + drop anchor)
    "HOOK_ENABLED",
    "USER_DROP_T",
    "F5_HOOK_DEVICE",
    "F4_HOOK_DEVICE",
    "F4_BPM",
    "F3_HOOK",
    "F3_TRANSITION",
    "F3_EXTRA",
    "F3_EXTRA_FULL",
    "F3_HOOK_EXTEND",
    "F2_SHAPE",
    "F2_SEED",
    "F1_SOUND_URL",
    "F1_SOUND_TEXT",
    "BG_MODE",
    "BG_SOLID_COLOR_HEX",
    "SUBTITLES_FORCE_FILL_HEX",
    "F2_SHAPE_COLOR_HEX",
    "SUBTITLES_FOCUS_HEX",
    # photo flow (bg_mode == "photo")
    "PHOTO_STYLE",
    "PHOTO_TRANSITION",
    "PHOTO_INVENTORY_JSON",
    "PHOTO_TAGS_SNAPSHOT_JSON",
)


def _apply_runtime_llm_env_overrides(env: Dict[str, str], store: JobStore) -> None:
    try:
        runtime_values = get_runtime_values(store)
    except Exception as exc:
        log.warning("runtime_llm_config_unavailable err=%r", exc)
        return
    raw_thinking = runtime_values.get("gemini.max_thinking_tokens")
    if raw_thinking is not None:
        env["GEMINI_MAX_THINKING_TOKENS"] = str(int(raw_thinking))


def _is_remote_url(u: str) -> bool:
    s = (u or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


def _windows_default_urls() -> list[str]:
    # Keep a deterministic merged list from WINDOWS_RENDER_URL + WINDOWS_RENDER_URLS.
    return parse_windows_urls_csv((SETTINGS.windows_base_url + "," + SETTINGS.windows_base_urls_csv).strip(","))


def _requested_render_engine(req: Dict[str, Any]) -> str:
    engine = str(req.get("render_engine") or "ae").strip().lower()
    if engine not in {"ae", "rust-gen"}:
        raise RuntimeError(f"unsupported_render_engine: {engine!r}")
    return engine


def _ensure_rust_gen_route(req: Dict[str, Any]) -> None:
    if not bool(getattr(SETTINGS, "rust_gen_enabled", False)):
        raise RuntimeError("rust_gen_disabled")
    if not str(getattr(SETTINGS, "rust_gen_manager_url", "") or "").strip():
        raise RuntimeError("RUST_GEN_MANAGER_URL is not set")
    if bool(getattr(SETTINGS, "rust_gen_canary_enabled", False)):
        allowed = {
            str(mode or "").strip().lower()
            for mode in (getattr(SETTINGS, "rust_gen_canary_subtitle_modes", ()) or ())
            if str(mode or "").strip()
        }
        subtitle_mode = str(req.get("subtitles_mode") or "").strip().lower()
        if allowed and subtitle_mode not in allowed:
            raise RuntimeError(
                f"rust_gen_canary_subtitle_mode_not_enabled: {subtitle_mode or 'unknown'}"
            )


def _job_queue_from_request(req: Dict[str, Any], *, key: str, default: str) -> str:
    value = str(req.get(key) or "").strip()
    if value:
        return value
    return str(default or "").strip()


_LLM_PROVIDER_MODE_GEMINI = "gemini"
_LLM_PROVIDER_MODE_OPENROUTER = "openrouter"
_LLM_PROVIDER_MODE_HEDGED = "hedged"


def _provider_mode_for_worker_type(worker_type: str) -> str:
    wt = normalize_llm_worker_type(worker_type)
    if wt in {LLM_WORKER_TYPE_SDK, LLM_WORKER_TYPE_VERTEX_SDK_MIX}:
        return _LLM_PROVIDER_MODE_GEMINI
    if wt == LLM_WORKER_TYPE_OPENROUTER:
        return _LLM_PROVIDER_MODE_OPENROUTER
    if wt == LLM_WORKER_TYPE_HYBRID:
        return _LLM_PROVIDER_MODE_HEDGED
    raise RuntimeError(f"unsupported llm_worker_type: {worker_type!r}")


def _inc_metric(store: JobStore, *, metric: str, label: str) -> None:
    try:
        increment_counter(store, metric=metric, label=label)
    except Exception:
        pass


log = logging.getLogger(__name__)


def _node_label(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return "unknown"
    try:
        host = (urlparse(u).hostname or "").strip().lower()
        if host:
            return host
    except Exception:
        pass
    return u.lower()


def _inc_labeled_metric(store: JobStore, *, metric: str, labels: dict[str, Any]) -> None:
    try:
        increment_labeled_counter(store, metric=metric, labels=labels)
    except Exception:
        pass


def _reason_label(raw: str) -> str:
    txt = str(raw or "").strip().lower()
    if not txt:
        return "unknown"
    cleaned = re.sub(r"[^a-z0-9_]+", "_", txt)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


def _dispatch_fail_streak_key(store: JobStore, *, node_url: str) -> str:
    digest = hashlib.sha1(str(node_url or "").encode("utf-8")).hexdigest()[:12]
    return f"{store.key_prefix}:windows:dispatch_fail_streak:{digest}"


def _inc_dispatch_fail_streak(store: JobStore, *, node_url: str) -> int:
    key = _dispatch_fail_streak_key(store, node_url=node_url)
    ttl = max(0, int(getattr(SETTINGS, "windows_node_disable_dispatch_streak_ttl_s", 1800) or 1800))
    try:
        raw = store.r.incr(key)
        val = int(raw or 0)
        if ttl > 0:
            try:
                store.r.expire(key, ttl)
            except Exception:
                pass
        return max(0, val)
    except Exception:
        return 0


def _reset_dispatch_fail_streak(store: JobStore, *, node_url: str) -> None:
    key = _dispatch_fail_streak_key(store, node_url=node_url)
    try:
        store.r.delete(key)
    except Exception:
        return


def _notify_ops_telegram(text: str) -> None:
    token = str(getattr(SETTINGS, "alert_telegram_bot_token", "") or "").strip()
    if not token:
        return
    telegram_api = make_telegram_api(
        str(getattr(SETTINGS, "alert_telegram_api_env", "prod") or "prod"),
        name="ALERT_TELEGRAM_API_ENV",
    )
    recipients: set[str] = set()
    static_chat_id = str(getattr(SETTINGS, "alert_telegram_chat_id", "") or "").strip()
    if static_chat_id:
        recipients.add(static_chat_id)
    if bool(getattr(SETTINGS, "alert_subscribers_enabled", True)):
        for chat_id in fetch_active_chat_ids_sync(
            str(getattr(SETTINGS, "credits_db_url", "") or "").strip(),
            limit=max(1, int(getattr(SETTINGS, "alert_subscribers_max_chat_ids", 200) or 200)),
        ):
            if chat_id:
                recipients.add(str(chat_id))
    if not recipients:
        return

    proxy_url = str(getattr(SETTINGS, "tg_file_proxy_url", "") or "").strip()
    opener = None
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )

    msg_text = str(text or "").strip()[:3500]
    for chat_id in sorted(recipients):
        payload = {
            "chat_id": chat_id,
            "text": msg_text,
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=telegram_api.method_url(token=token, method="sendMessage"),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            if opener is None:
                resp_ctx = urllib.request.urlopen(req, timeout=8.0)
            else:
                resp_ctx = opener.open(req, timeout=8.0)
            with resp_ctx as resp:
                _ = resp.read()
        except urllib.error.HTTPError as exc:
            status_code = int(getattr(exc, "code", 0) or 0)
            body = ""
            try:
                body = exc.read().decode("utf-8", "ignore")
            except Exception:
                body = ""
            description = ""
            if body:
                try:
                    parsed = json.loads(body)
                    description = str(parsed.get("description") or "")
                except Exception:
                    description = body[:300]
            log.warning(
                "ops_telegram_notify_failed chat_id=%s status=%s desc=%s",
                chat_id,
                status_code,
                description or repr(exc),
            )
            if is_terminal_telegram_delivery_error(status_code=status_code, description=description):
                deactivate_chat_id_sync(
                    str(getattr(SETTINGS, "credits_db_url", "") or "").strip(),
                    chat_id=int(chat_id),
                )
        except Exception as exc:
            log.warning("ops_telegram_notify_failed chat_id=%s err=%r", chat_id, exc)


def _auto_disable_node(
    *,
    store: JobStore,
    pool: WindowsNodePool,
    node_url: str,
    reason: str,
    job_id: str,
    render_id: str = "",
) -> bool:
    reason_txt = str(reason or "").strip() or "unknown_reason"
    try:
        _nodes, changed = pool.disable_node(
            url=node_url,
            reason=reason_txt,
            default_urls=_windows_default_urls(),
        )
    except Exception as exc:
        _obs_event(
            "windows_node_disable_failed",
            node=_node_label(node_url),
            reason=_reason_label(reason_txt),
            err=repr(exc),
            job_id=job_id,
            render_id=render_id or None,
        )
        return False
    if not changed:
        return False
    node = _node_label(node_url)
    reason_label = _reason_label(reason_txt)
    _inc_labeled_metric(
        store,
        metric="windows_node_state_change_total",
        labels={"node": node, "event": "auto_disabled", "reason": reason_label},
    )
    _obs_event(
        "windows_node_disabled",
        node=node,
        reason=reason_label,
        job_id=job_id,
        render_id=render_id or None,
    )
    _notify_ops_telegram(
        "\n".join(
            [
                "Windows node auto-disabled",
                f"node: {node_url}",
                f"reason: {reason_txt}",
                f"job_id: {job_id}",
                f"render_id: {render_id or '-'}",
            ]
        )
    )
    # Pool-exhaustion alert: if disabling this node left ZERO enabled render nodes
    # in the runtime pool, every subsequent dispatch will fail (unless a
    # WINDOWS_RENDER_URL env fallback is set). Surface it loudly instead of letting
    # the operator discover it only via a failed job.
    try:
        enabled_left = sum(1 for n in (_nodes or []) if bool(n.get("enabled", True)))
    except Exception:
        enabled_left = -1
    if enabled_left == 0:
        log.error(
            "windows_pool_exhausted no_enabled_render_nodes_left last_disabled=%s reason=%s job_id=%s "
            "(dispatch falls back to WINDOWS_RENDER_URL env if set, else fails)",
            node_url, reason_txt, job_id,
        )
        _obs_event(
            "windows_pool_exhausted",
            node=node,
            reason=reason_label,
            job_id=job_id,
            render_id=render_id or None,
        )
        _inc_labeled_metric(
            store,
            metric="windows_node_state_change_total",
            labels={"node": "_pool_", "event": "exhausted", "reason": reason_label},
        )
        _notify_ops_telegram(
            "\n".join(
                [
                    "⚠️ Windows render pool EXHAUSTED — no enabled nodes left!",
                    f"last disabled node: {node_url}",
                    f"reason: {reason_txt}",
                    "All renders will FAIL at dispatch until a node is re-enabled "
                    "or WINDOWS_RENDER_URL env fallback is set.",
                ]
            )
        )
    return True


def _observe_stage_duration(
    store: JobStore,
    *,
    stage: str,
    started_at: float,
    outcome: str,
) -> None:
    try:
        start = float(started_at)
    except Exception:
        return
    if start <= 0:
        return
    dur = max(0.0, time.time() - start)
    try:
        observe_labeled_histogram(
            store,
            metric="stage_duration_seconds",
            value=dur,
            buckets=STAGE_DURATION_BUCKETS,
            labels={
                "stage": str(stage or "").strip().lower() or "unknown",
                "outcome": str(outcome or "").strip().lower() or "unknown",
            },
        )
    except Exception:
        return


def _obs_event(event: str, **fields: Any) -> None:
    items: list[str] = []
    for k, v in fields.items():
        if v is None:
            continue
        items.append(f"{k}={v}")
    tail = " ".join(items)
    if tail:
        log.info("obs_event event=%s %s", str(event or "unknown"), tail)
    else:
        log.info("obs_event event=%s", str(event or "unknown"))


def _extract_artifacts_source(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    direct_candidates = [
        payload.get("project_archive_url"),
        payload.get("artifacts_s3_uri"),
        payload.get("artifacts_s3_url"),
        payload.get("artifacts_url"),
    ]
    for raw in direct_candidates:
        u = str(raw or "").strip()
        if _is_remote_url(u):
            return u

    # Backward-compatible parser for windows message like:
    # "ok; artifacts=s3://bucket/key.tar.gz; local_job_dir_deleted=1"
    msg = str(payload.get("message") or "").strip()
    if not msg:
        return ""
    m = re.search(r"artifacts=(s3://[^;\s]+|https?://[^;\s]+)", msg, flags=re.IGNORECASE)
    if not m:
        return ""
    u = str(m.group(1) or "").strip().rstrip(".,;")
    return u if _is_remote_url(u) else ""


def _parse_s3_url(url: str) -> tuple[str, str]:
    u = (url or "").strip()
    if not u.startswith("s3://"):
        raise RuntimeError(f"expected s3:// url, got {url!r}")
    tail = u[5:]
    if "/" not in tail:
        raise RuntimeError(f"invalid s3 url (missing key): {url!r}")
    bucket, key = tail.split("/", 1)
    bucket = bucket.strip()
    key = key.strip()
    if not bucket or not key:
        raise RuntimeError(f"invalid s3 url: {url!r}")
    return bucket, key


def _make_s3_client():
    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    if bool(access_key) != bool(secret_key):
        raise RuntimeError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be both set or both empty")

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        # proxies={}: S3 (Timeweb, РФ) ходит НАПРЯМУЮ, мимо OUTBOUND-прокси. Прокси
        # зарубежный (для Gemini) и до Timeweb S3 не туннелит (502 Bad Gateway);
        # boto по умолчанию наследует HTTPS_PROXY из env — здесь его отключаем.
        "config": Config(signature_version="s3v4", proxies={}),
    }
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    return boto3.client(**kwargs)


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw not in {"0", "false", "no", "off"}


def _s3_head_exists(*, bucket: str, key: str) -> bool:
    client = _make_s3_client()
    try:
        client.head_object(Bucket=str(bucket), Key=str(key))
        return True
    except Exception as e:
        # boto3/botocore shape: e.response["Error"]["Code"] / HTTPStatusCode
        response = getattr(e, "response", None)
        if isinstance(response, dict):
            err_obj = response.get("Error")
            err_code = ""
            if isinstance(err_obj, dict):
                err_code = str(err_obj.get("Code") or "").strip().lower()
            if err_code in {"404", "notfound", "nosuchkey"}:
                return False
            meta = response.get("ResponseMetadata")
            if isinstance(meta, dict):
                try:
                    if int(meta.get("HTTPStatusCode") or 0) == 404:
                        return False
                except Exception:
                    pass
        raise


def _try_recover_dispatch_from_existing_output(
    *,
    store: JobStore,
    job_id: str,
    errors_by_node: list[str],
) -> Optional[Dict[str, Any]]:
    if not _bool_env("DISPATCH_RECOVERY_FROM_S3_ENABLED", True):
        _inc_labeled_metric(store, metric="dispatch_recovery_total", labels={"outcome": "disabled"})
        return None

    output_bucket = (os.environ.get("S3_BUCKET_OUTPUT_VIDEO") or "").strip()
    output_key = f"renders/{str(job_id).strip()}/output.mp4"
    if not output_bucket:
        _inc_metric(store, metric="dispatch_recovery_outcomes", label="false")
        _inc_labeled_metric(store, metric="dispatch_recovery_total", labels={"outcome": "skip_no_bucket"})
        print(f"[dispatch_recovery] skip_no_bucket job_id={job_id}")
        return None

    try:
        output_exists = _s3_head_exists(bucket=output_bucket, key=output_key)
    except Exception as e:
        _inc_metric(store, metric="dispatch_recovery_outcomes", label="false")
        _inc_labeled_metric(store, metric="dispatch_recovery_total", labels={"outcome": "head_failed"})
        print(f"[dispatch_recovery] head_failed job_id={job_id} err={e!r}")
        return None

    if not output_exists:
        _inc_metric(store, metric="dispatch_recovery_outcomes", label="false")
        _inc_labeled_metric(store, metric="dispatch_recovery_total", labels={"outcome": "output_missing"})
        return None

    marker = "dispatch_timeout_but_output_exists"
    output_url = f"s3://{output_bucket}/{output_key}"
    store.set_status(
        job_id,
        "SUCCEEDED",
        stage="render",
        result={
            "output_url": output_url,
            "dispatch_recovery": {
                "marker": marker,
                "recovered_from_existing_output": True,
                "errors_by_node": list(errors_by_node),
                "checked_at": time.time(),
            },
        },
    )
    _inc_metric(store, metric="dispatch_recovery_outcomes", label="true")
    _inc_labeled_metric(store, metric="dispatch_recovery_total", labels={"outcome": "recovered"})
    _obs_event(
        "dispatch_recovery",
        job_id=job_id,
        marker=marker,
        output_url=output_url,
        errors=len(errors_by_node),
    )
    print(
        "[dispatch_recovery] recovered "
        f"marker={marker} job_id={job_id} output_url={output_url}"
    )
    return {"ok": True, "mode": "dispatch_recovered_existing_output", "output_url": output_url}


def _download(url: str, dest: Path, *, timeout_s: float = 300.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if (url or "").strip().lower().startswith("s3://"):
        bucket, key = _parse_s3_url(url)
        c = _make_s3_client()
        c.download_file(bucket, key, str(dest))
        return

    with urllib.request.urlopen(url, timeout=float(timeout_s)) as resp:
        data = resp.read()
    dest.write_bytes(data)


def _non_negative_int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        val = int(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid {name}: {raw!r}") from e
    if val < 0:
        raise RuntimeError(f"{name} must be >= 0, got {val!r}")
    return val


def _cleanup_old_job_logs(
    *,
    output_dir: str,
    current_job_id: str,
    now_ts: Optional[float] = None,
) -> Dict[str, int]:
    """
    Best-effort cleanup for local per-job logs.
    Removes files older than JOB_LOG_RETENTION_SECONDS from:
      output/jobs/<job_id>/out/logs/**/*
    Current job is skipped.
    """
    ttl_s = _non_negative_int_env("JOB_LOG_RETENTION_SECONDS", 3600)
    out: Dict[str, int] = {
        "ttl_s": int(ttl_s),
        "scanned_job_logs_dirs": 0,
        "deleted_files": 0,
        "skipped_current_job": 0,
    }
    if ttl_s <= 0:
        return out

    output_root = Path(output_dir).expanduser().resolve()
    jobs_root = output_root / "jobs"
    if not jobs_root.exists() or not jobs_root.is_dir():
        return out

    ref_now = float(now_ts) if now_ts is not None else time.time()
    cutoff_ts = ref_now - float(ttl_s)

    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        if current_job_id and job_dir.name == str(current_job_id):
            out["skipped_current_job"] += 1
            continue

        logs_dir = job_dir / "out" / "logs"
        if not logs_dir.exists() or not logs_dir.is_dir():
            continue
        out["scanned_job_logs_dirs"] += 1

        for p in logs_dir.rglob("*"):
            if not p.is_file():
                continue
            try:
                mtime = float(p.stat().st_mtime)
            except FileNotFoundError:
                continue
            if mtime >= cutoff_ts:
                continue
            try:
                p.unlink()
                out["deleted_files"] += 1
            except FileNotFoundError:
                continue

    return out


def _ensure_shared_catalog(repo_root: Path) -> None:
    """
    inventory + bundle are SHARED and must exist before we call Gemini.
    If missing -> run `python footage_config.py` once.
    """
    inv_path_s = (os.environ.get("FOOTAGE_INVENTORY_JSON") or "").strip()
    bun_path_s = (os.environ.get("DESCRIPTIONS_BUNDLE_PATH") or "").strip()

    if not inv_path_s:
        inv_path_s = (os.environ.get("FOOTAGE_INVENTORY_OUT") or "").strip()
    if not bun_path_s:
        bun_path_s = (os.environ.get("DESCRIPTIONS_BUNDLE_OUT") or "").strip()

    if not inv_path_s:
        inv_path_s = str((repo_root / "data" / "footage_inventory.json").resolve())
        os.environ["FOOTAGE_INVENTORY_JSON"] = inv_path_s
    if not bun_path_s:
        bun_path_s = str((repo_root / "pins" / "descriptions_bundle.json").resolve())
        os.environ["DESCRIPTIONS_BUNDLE_PATH"] = bun_path_s

    inv_path = Path(inv_path_s).expanduser()
    if not inv_path.is_absolute():
        inv_path = (repo_root / inv_path).resolve()

    bun_path = Path(bun_path_s).expanduser()
    if not bun_path.is_absolute():
        bun_path = (repo_root / bun_path).resolve()

    if inv_path.exists() and bun_path.exists():
        return

    cmd = os.environ.get("FOOTAGE_CATALOG_CMD", "python footage_config.py").strip() or "python footage_config.py"
    print(f"[catalog] missing -> generating via: {cmd}")
    args = shlex.split(cmd)
    subprocess.check_call(args, cwd=str(repo_root))

    if not inv_path.exists():
        raise RuntimeError(f"[catalog] inventory still missing after build: {inv_path}")
    if not bun_path.exists():
        raise RuntimeError(f"[catalog] bundle still missing after build: {bun_path}")

    print(f"[catalog] ok inventory={inv_path} bundle={bun_path}")


def _patch_audio_layer_to_remote(footage_config_path: Path, *, audio_url: str) -> None:
    """
    Keep footage_config relocatable by injecting remote audio url into the audio_only layer.
    (This does NOT rebuild JSX; it's mainly for debugging / determinism.)
    """
    if not audio_url:
        return
    if not _is_remote_url(audio_url):
        # we refuse to write local paths here
        raise RuntimeError(f"audio_url is not remote, refusing to patch: {audio_url!r}")

    d = json.loads(footage_config_path.read_text(encoding="utf-8"))
    layers = d.get("layers")
    if not isinstance(layers, list):
        return

    audio_name_raw = (audio_url.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()
    audio_name = (unquote(audio_name_raw) or audio_name_raw).strip()

    changed = False
    for it in layers:
        if not isinstance(it, dict):
            continue
        if str(it.get("type")) != "audio_only":
            continue

        if it.get("file_name") != audio_name:
            it["file_name"] = audio_name
            changed = True

        if it.get("file_path") != audio_url:
            it["file_path"] = audio_url
            changed = True

        if bool(it.get("enabled", True)) is not True:
            it["enabled"] = True
            changed = True
        if bool(it.get("audio_enabled", True)) is not True:
            it["audio_enabled"] = True
            changed = True
        if bool(it.get("video_enabled", False)) is not False:
            it["video_enabled"] = False
            changed = True

    if changed:
        footage_config_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _looks_like_gemini_internal_500(text: str) -> bool:
    if not text:
        return False
    # Depending on how the exception is rendered, it can be either:
    # - "google.genai.errors.ServerError: 500 INTERNAL ..."
    # - "ServerError('500 INTERNAL ...')"
    if "500" not in text:
        return False
    return ("INTERNAL" in text) and ("internal error has occurred" in text.lower())


def _looks_like_gemini_overloaded_503(text: str) -> bool:
    """
    Gemini transient overload / high demand.
    Example:
      google.genai.errors.ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand...'}}
    """
    if not text:
        return False
    if "503" not in text:
        return False
    lo = text.lower()
    if "unavailable" not in lo:
        return False
    # Typical message contains either "503 UNAVAILABLE" or "code: 503" and often "high demand".
    # We accept any of these stable indicators to avoid missing retries due to string escaping.
    return ("503 unavailable" in lo) or ("code" in lo) or ("high demand" in lo)


def _looks_like_gemini_rate_limited_429(text: str) -> bool:
    """
    Gemini transient rate limits.
    We keep matching explicit keywords to avoid false positives.
    """
    if not text:
        return False
    if "429" not in text:
        return False
    # Can surface as ClientError/ServerError; we key off explicit code+keyword.
    lo = text.lower()
    return ("resource_exhausted" in lo or "too many requests" in lo) and ("429" in lo)


def _looks_like_gemini_transport_disconnect(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    transport_markers = (
        "remoteprotocolerror",
        "server disconnected without sending a response",
        "connection reset by peer",
        "connection aborted",
        "readerror",
        "connecterror",
        "httpcore.",
        "httpx.",
    )
    if not any(marker in lo for marker in transport_markers):
        return False
    gemini_markers = (
        "google.genai",
        "google/genai",
        "gemini_client.py",
        "models.generate_content",
        "generate_content",
    )
    return any(marker in lo for marker in gemini_markers)


def _maybe_retry_gemini_transport_disconnect(self: Any, store: JobStore, text: str, *, phase: str) -> None:
    if not _looks_like_gemini_transport_disconnect(text):
        return
    try:
        runtime_values = get_runtime_values(store)
    except Exception:
        runtime_values = {}
    if runtime_values.get("gemini.transport_retry_enabled", True) is False:
        return
    try:
        base_s = float(runtime_values.get("gemini.transport_retry_base_s", 10.0) or 10.0)
    except Exception:
        base_s = 10.0
    try:
        cap_s = float(runtime_values.get("gemini.transport_retry_cap_s", 300.0) or 300.0)
    except Exception:
        cap_s = 300.0
    attempt = int(getattr(self.request, "retries", 0)) + 1
    backoff = _retry_backoff_s(attempt=attempt, base_s=max(0.5, base_s), cap_s=max(1.0, cap_s))
    log.warning(
        "gemini_transport_disconnect_retry phase=%s attempt=%d/%d backoff_s=%.1f err=%s",
        phase,
        attempt,
        int(getattr(self, "max_retries", 0) or 0),
        backoff,
        text[:800],
    )
    raise self.retry(countdown=backoff, exc=RuntimeError("gemini_transport_disconnect"))


def _looks_like_openrouter_timeout(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "openrouter_timeout" in lo:
        return True
    return ("openrouter" in lo) and ("timeout" in lo or "timed out" in lo)


def _looks_like_openrouter_overloaded_503(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "openrouter_http_error" in lo and "status=503" in lo:
        return True
    return ("openrouter" in lo) and ("503" in lo) and ("unavailable" in lo or "overloaded" in lo)


def _looks_like_openrouter_rate_limited_429(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "openrouter_http_error" in lo and "status=429" in lo:
        return True
    if "openrouter_bad_response_no_choices" in lo and (
        "'code': 429" in lo or '"code": 429' in lo
    ):
        return True
    if "openrouter_bad_response_no_text_content" in lo and (
        "'code': 429" in lo or '"code": 429' in lo
    ):
        return True
    return ("openrouter" in lo) and ("429" in lo) and (
        "rate limit" in lo or "too many requests" in lo
    )


def _looks_like_openrouter_internal_500(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "openrouter_http_error" in lo and "status=500" in lo:
        return True
    if "openrouter_bad_response_no_choices" in lo and (
        "'code': 500" in lo or '"code": 500' in lo
    ):
        return True
    return ("openrouter" in lo) and ("500" in lo) and ("internal server error" in lo)


def _looks_like_openrouter_provider_unavailable_502(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "openrouter_http_error" in lo and "status=502" in lo:
        return ("provider_unavailable" in lo) or ("network connection lost" in lo)
    if "openrouter_bad_response_no_text_content" in lo and (
        "'code': 502" in lo or '"code": 502' in lo
    ):
        return ("provider_unavailable" in lo) or ("network connection lost" in lo)
    return ("openrouter" in lo) and ("502" in lo) and (
        "provider_unavailable" in lo or "network connection lost" in lo
    )


def _looks_like_openrouter_gateway_timeout_524(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "openrouter_http_error" in lo and "status=524" in lo:
        return True
    if "openrouter_bad_response_no_choices" in lo and (
        "'code': 524" in lo or '"code": 524' in lo
    ):
        return True
    if "openrouter_bad_response_no_text_content" in lo and (
        "'code': 524" in lo or '"code": 524' in lo
    ):
        return True
    return ("openrouter" in lo) and ("524" in lo) and ("timeout" in lo or "provider returned error" in lo)


def _looks_like_openrouter_bad_request_400(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    return "openrouter_http_error" in lo and "status=400" in lo


def _looks_like_stage1a_selected_fragment_missing(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "stage1a_selected_fragment_missing" in lo:
        return True
    return "requires stage1a.selected_fragment" in lo and "got null" in lo


def _looks_like_llm_schema_validation_error(text: str) -> bool:
    """
    LLM produced syntactically/structurally invalid payload for our schema.
    This is terminal at Celery layer; stage-local retries are handled inside
    the LLM orchestrator.
    """
    if not text:
        return False
    lo = text.lower()
    if "openrouter_schema_validation_failed" in lo:
        return True
    if "openrouter_tokens_schema_validation_failed" in lo:
        return True
    if "stage1 scenario validation failed" in lo:
        return True
    if "stage1 scenario validation failed after retry" in lo:
        return True
    if "stage2 failed:" in lo and (
        "validation" in lo
        or "schema" in lo
        or "subtitles.clip." in lo
        or "must equal stage1.audio" in lo
        or "mine must contain exactly one token" in lo
        or "end_idx out of range" in lo
        or "style pick" in lo
        or "style_pool_groups_json" in lo
    ):
        return True
    if "llm_hedged_all_failed" in lo and ("validation" in lo or "schema" in lo):
        return True
    if "validationerror" in lo and ("pydantic" in lo or "schema" in lo):
        return True
    return False


def _looks_like_build_preflight_validation_error(text: str) -> bool:
    """
    Deterministic builder preflight rejects impossible timing/layout.
    We allow one immediate local build-step retry in-process, then fail.
    """
    if not text:
        return False
    lo = text.lower()
    if "preflight:" in lo and "out<=in" in lo:
        return True
    return "preflight_clamp_text_layers" in lo


def _extract_preflight_out_le_in_issue(text: str) -> Dict[str, Any] | None:
    """
    Parse first builder preflight out<=in marker from traceback blob.
    Expected shape (from app/text_comp.py):
      Preflight: out<=in in layer 'Layer Name': 8.51..8.51
    """
    if not text:
        return None
    m = re.search(
        r"Preflight:\s*out<=in\s*in\s*layer\s*['\"](?P<layer>[^'\"]+)['\"]\s*:\s*(?P<in>[-+0-9.eE]+)\.\.(?P<out>[-+0-9.eE]+)",
        str(text),
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    try:
        in_p = float(str(m.group("in") or ""))
        out_p = float(str(m.group("out") or ""))
    except Exception:
        return None
    return {
        "layer_name": str(m.group("layer") or "").strip(),
        "in_point": in_p,
        "out_point": out_p,
    }


def _build_stage2_subtitles_retry_hint(preflight_blob: str) -> str:
    base = (
        "Previous build preflight failed with impossible layer timings (e.g. out<=in). "
        "Regenerate subtitles to keep timings strictly valid and monotonic. "
        "Do not change stage1 clip window; preserve transcript word order; ensure each token t_end > t_start."
    )
    issue = _extract_preflight_out_le_in_issue(preflight_blob)
    if not isinstance(issue, dict):
        return base

    return (
        base
        + "\n\nDETECTED_PREFLIGHT_ISSUE:\n"
        + f"- error_type: out<=in\n"
        + f"- layer_name: {issue['layer_name']}\n"
        + f"- layer_in_point: {float(issue['in_point']):.6f}\n"
        + f"- layer_out_point: {float(issue['out_point']):.6f}\n"
        + "- required_fix: regenerate subtitles timing so this layer has strictly positive duration "
        + "(out_point must be > in_point) while preserving stage1 clip window."
    )


def _exc_text(e: BaseException) -> str:
    """
    Normalize exception into a stable text blob for our retry matchers.
    IMPORTANT: do not rely only on repr(e) since it may escape quotes (\\').
    """
    parts: list[str] = [type(e).__name__]
    try:
        parts.append(str(e))
    except Exception:
        pass
    try:
        parts.append(repr(e))
    except Exception:
        pass
    return "\n".join([p for p in parts if p])


def _retry_backoff_s(*, attempt: int, base_s: float, cap_s: float) -> float:
    """
    Deterministic exponential backoff.
    attempt is 1-based.
    """
    a = max(1, int(attempt))
    return min(float(cap_s), float(base_s) * float(2 ** max(0, a - 1)))


_OVERLOADED_RETRY_BASE_S = 2.0
_OVERLOADED_RETRY_CAP_S = 64.0


def _overloaded_retry_backoff_s(*, attempt: int) -> float:
    """
    Fast overload backoff for model/provider 503 bursts:
    2, 4, 8, 16, 32, 64, 64, ...
    """
    return _retry_backoff_s(
        attempt=attempt,
        base_s=_OVERLOADED_RETRY_BASE_S,
        cap_s=_OVERLOADED_RETRY_CAP_S,
    )


def _drop_resume_stage_key(path: Path, *, key: str) -> bool:
    """
    Remove one stage entry from LLM resume-state file.
    Returns True if key existed and file was updated.
    """
    try:
        if not path.exists():
            return False
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return False
        had_key = key in obj
        obj.pop(key, None)
        if key == "stage2_subtitles":
            obj.pop("stage2_subtitles_mode", None)
        if not had_key:
            return False
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        return False


def _is_transient_windows_error(e: BaseException) -> bool:
    # Network / transport issues.
    if isinstance(e, urllib.error.URLError):
        return True
    if isinstance(e, TimeoutError):
        return True
    if isinstance(e, (ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(e, OSError):
        msg = (str(e) or "").lower()
        if "broken pipe" in msg or "connection reset" in msg or "timed out" in msg:
            return True

    # Retry on 5xx from Windows node.
    if isinstance(e, urllib.error.HTTPError):
        try:
            code = int(getattr(e, "code", 0) or 0)
        except Exception:
            code = 0
        if 500 <= code <= 599:
            return True

    return False


def _job_resume_state_path(*, work_dir: str, job_id: str) -> Path:
    return Path(work_dir).resolve() / "jobs" / str(job_id).strip() / "data" / "llm_resume_state.json"


def _load_resume_state_file(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"resume_state_unreadable path={path} err={e!r}") from e
    if not isinstance(obj, dict):
        raise RuntimeError(f"resume_state_invalid path={path} expected JSON object")
    return obj


def _resume_state_from_job_result(store: JobStore, job_id: str) -> Dict[str, Any]:
    st = store.get(str(job_id or "").strip())
    if not st or not isinstance(st.result, dict):
        return {}
    obj = st.result.get("resume_state")
    return dict(obj) if isinstance(obj, dict) else {}


async def _load_resume_state_from_runtime_db_async(*, db_url: str, job_id: str) -> Dict[str, Any]:
    import asyncpg  # type: ignore

    conn = await asyncpg.connect(dsn=str(db_url or "").strip())
    try:
        row = await conn.fetchrow(
            """
            SELECT resume_state
            FROM generation_versions
            WHERE job_id = $1
              AND resume_state_checksum <> ''
            LIMIT 1
            """,
            str(job_id or "").strip(),
        )
    finally:
        await conn.close()
    if not row:
        return {}
    raw = row["resume_state"]
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _load_resume_state_from_runtime_db(*, job_id: str) -> Dict[str, Any]:
    db_url = str(getattr(SETTINGS, "credits_db_url", "") or "").strip()
    if not db_url or not str(job_id or "").strip():
        return {}
    try:
        return asyncio.run(_load_resume_state_from_runtime_db_async(db_url=db_url, job_id=job_id))
    except Exception as exc:
        log.warning("runtime_resume_state_read_failed source_job_id=%s err=%r", job_id, exc)
        return {}


async def _persist_resume_state_to_runtime_db_async(
    *,
    db_url: str,
    job_id: str,
    resume_state: Dict[str, Any],
    source: str,
    checksum: str,
) -> bool:
    import asyncpg  # type: ignore

    conn = await asyncpg.connect(dsn=str(db_url or "").strip())
    try:
        await conn.execute(
            """
            ALTER TABLE generation_versions
                ADD COLUMN IF NOT EXISTS resume_state JSONB NOT NULL DEFAULT '{}'::jsonb
            """
        )
        await conn.execute(
            """
            ALTER TABLE generation_versions
                ADD COLUMN IF NOT EXISTS resume_state_source TEXT NOT NULL DEFAULT ''
            """
        )
        await conn.execute(
            """
            ALTER TABLE generation_versions
                ADD COLUMN IF NOT EXISTS resume_state_checksum TEXT NOT NULL DEFAULT ''
            """
        )
        await conn.execute(
            """
            ALTER TABLE generation_versions
                ADD COLUMN IF NOT EXISTS resume_state_updated_at TIMESTAMPTZ
            """
        )
        status = await conn.execute(
            """
            UPDATE generation_versions
            SET
                resume_state = $2::jsonb,
                resume_state_source = $3,
                resume_state_checksum = $4,
                resume_state_updated_at = NOW(),
                updated_at = NOW()
            WHERE job_id = $1
            """,
            str(job_id or "").strip(),
            json.dumps(resume_state or {}, ensure_ascii=False),
            str(source or ""),
            str(checksum or ""),
        )
        updated = int(str(status or "UPDATE 0").split()[-1])
        if updated > 0:
            await conn.execute(
                """
                INSERT INTO run_events (run_id, surface, job_id, event_type, payload)
                SELECT
                    v.run_id,
                    r.surface,
                    v.job_id,
                    'resume_state_persisted',
                    $2::jsonb
                FROM generation_versions v
                JOIN generation_runs r ON r.run_id = v.run_id
                WHERE v.job_id = $1
                """,
                str(job_id or "").strip(),
                json.dumps({"source": source, "checksum": checksum}, ensure_ascii=False),
            )
        return updated > 0
    finally:
        await conn.close()


def _persist_resume_state_snapshot(
    *,
    store: JobStore,
    job_id: str,
    resume_state_path: Path,
    source: str,
) -> Dict[str, Any]:
    if not resume_state_path.exists():
        return {}
    state = _load_resume_state_file(resume_state_path)
    if not state:
        return {}
    checksum = resume_state_checksum(state)
    persisted_to_runtime = False
    db_url = str(getattr(SETTINGS, "credits_db_url", "") or "").strip()
    if db_url:
        try:
            persisted_to_runtime = asyncio.run(
                _persist_resume_state_to_runtime_db_async(
                    db_url=db_url,
                    job_id=job_id,
                    resume_state=state,
                    source=source,
                    checksum=checksum,
                )
            )
        except Exception as exc:
            log.warning("runtime_resume_state_persist_failed job=%s source=%s err=%r", job_id, source, exc)
    store.set_status(
        job_id,
        "RUNNING",
        result={
            "resume_state": state,
            "resume_state_source": source,
            "resume_state_checksum": checksum,
            "resume_state_updated_at": time.time(),
            "resume_state_runtime_persisted": bool(persisted_to_runtime),
        },
    )
    return {
        "resume_state": state,
        "resume_state_source": source,
        "resume_state_checksum": checksum,
        "resume_state_runtime_persisted": bool(persisted_to_runtime),
    }


def _seed_resume_state_from_source_job(
    *,
    work_dir: str,
    source_job_id: str,
    target_resume_state_path: Path,
    store: JobStore | None = None,
    include_footage: bool = False,
) -> None:
    src_job = str(source_job_id or "").strip()
    if not src_job:
        raise RuntimeError("reuse_text_job_id is empty")

    sources_checked: list[str] = []
    src_obj: Dict[str, Any] = {}

    src_obj = _load_resume_state_from_runtime_db(job_id=src_job)
    sources_checked.append("runtime_db")

    if not src_obj and store is not None:
        src_obj = _resume_state_from_job_result(store, src_job)
        sources_checked.append("job_result")

    src_path = _job_resume_state_path(work_dir=work_dir, job_id=src_job)
    if not src_obj and src_path.exists():
        src_obj = _load_resume_state_file(src_path)
        sources_checked.append("legacy_file")
    elif not src_obj:
        sources_checked.append("legacy_file_missing")

    if not isinstance(src_obj, dict) or not src_obj:
        raise RuntimeError(
            "reuse_text_source_resume_unavailable "
            f"source_job_id={src_job!r} checked={sources_checked!r} path={src_path}"
        )

    # stage1_asr is the only reused stage the orchestrator cannot rebuild cheaply
    # (the expensive ASR). Every other reuse key degrades gracefully when absent:
    #   - stage1_plan / stage1_plan_source: rebuilt for FREE from
    #     stage1_asr.selected_fragment (non-legacy) or re-derived via stage1b
    #     (legacy) — see gemini_orchestrator stage1 block.
    #   - stage2_* payloads: re-run if missing.
    # A SUCCEEDED non-legacy reuse job can legitimately persist WITHOUT stage1_plan
    # (it reuses/rebuilds it at runtime), so hard-failing on its absence would
    # crash every downstream bigtest case for no reason. Copy whatever is present
    # and only fail when stage1_asr itself is missing (nothing meaningful to reuse).
    if "stage1_asr" not in src_obj:
        raise RuntimeError(
            "reuse_text_source_resume_missing_keys "
            f"source_job_id={src_job!r} missing=['stage1_asr']"
        )
    skipped = [k for k in _REUSE_RESUME_STATE_KEYS if k not in src_obj]
    if skipped:
        log.info(
            "reuse_text_seed_partial source_job_id=%s skipped=%r "
            "(orchestrator will rebuild/recompute these)",
            src_job,
            skipped,
        )

    dst_obj: Dict[str, Any] = {}
    if target_resume_state_path.exists():
        try:
            old_obj = json.loads(target_resume_state_path.read_text(encoding="utf-8"))
            if isinstance(old_obj, dict):
                dst_obj.update(old_obj)
        except Exception:
            dst_obj = {}

    for k in _REUSE_RESUME_STATE_KEYS:
        if k in src_obj:
            dst_obj[k] = src_obj[k]
    if include_footage:
        # Bigtest: also copy footage style so every case uses the same genre/theme.
        # Only copied when present — if absent the orchestrator re-runs style LLM.
        for k in ("stage2_style", "stage2_style_rotation"):
            if k in src_obj:
                dst_obj[k] = src_obj[k]
    else:
        # Normal reuse: never carry footage selection from source job so each
        # new generation gets fresh clips (intentional diversity).
        # stage2_style is NOT removed — the destination may have its own partial
        # value from a prior run; only stage2_footage (raw clip list) is cleared.
        dst_obj.pop("stage2_footage", None)

    target_resume_state_path.parent.mkdir(parents=True, exist_ok=True)
    target_resume_state_path.write_text(
        json.dumps(dst_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _poll_started_at_from_state(st: Any) -> float:
    """
    Windows polling timeout should start from dispatch/poll, not from build start.
    We store poll_started_at into JobState.result on first transition to stage="poll".
    """
    try:
        res = st.result if hasattr(st, "result") else None
        if isinstance(res, dict):
            v = res.get("poll_started_at") or res.get("dispatch_started_at")
            if v is not None:
                return float(v)
    except Exception:
        pass

    try:
        v2 = getattr(st, "started_at", None) or getattr(st, "updated_at", None)
        if v2 is not None:
            return float(v2)
    except Exception:
        pass

    return time.time()


def _build_job_impl(self, job_id: str, *, worker_type: str | None) -> Dict[str, Any]:
    if get_runtime_mode() != MODE_PROD:
        raise RuntimeError("Celery build_job is allowed only in MODE=prod")

    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError(f"job not found: {job_id}")

    build_started_at = time.time()

    repo_root = Path(__file__).resolve().parents[2].resolve()
    _ensure_shared_catalog(repo_root)

    paths = make_job_paths(work_dir=SETTINGS.work_dir, output_dir=SETTINGS.output_dir, job_id=job_id)
    try:
        cleanup_info = _cleanup_old_job_logs(output_dir=SETTINGS.output_dir, current_job_id=str(job_id))
        deleted = int(cleanup_info.get("deleted_files", 0))
        if deleted > 0:
            print(
                "[cleanup] removed_old_job_logs "
                f"files={deleted} "
                f"scanned_dirs={int(cleanup_info.get('scanned_job_logs_dirs', 0))} "
                f"ttl_s={int(cleanup_info.get('ttl_s', 0))}"
            )
    except Exception as e:
        print(f"[cleanup][WARN] old job logs cleanup skipped: {e}")

    llm_resume_state_path = paths.data_dir / "llm_resume_state.json"

    req = st.request or {}
    req_worker_type_raw = str(req.get("llm_worker_type") or "").strip()
    task_worker_type = normalize_llm_worker_type(worker_type) if str(worker_type or "").strip() else ""
    req_worker_type = normalize_llm_worker_type(req_worker_type_raw) if req_worker_type_raw else ""
    if task_worker_type and req_worker_type and task_worker_type != req_worker_type:
        raise RuntimeError(
            f"llm_worker_type_mismatch expected={task_worker_type!r} got={req_worker_type!r}"
        )
    reservation_mode = str(req.get("llm_reservation_mode") or "").strip().lower()
    llm_worker_type = req_worker_type or task_worker_type
    if reservation_mode == "worker":
        store.set_status(
            job_id,
            "RUNNING",
            stage="llm_wait_capacity",
            result={"build_started_at": build_started_at, "llm_reservation_mode": "worker"},
        )
        try:
            reserved = reserve_worker_type_for_job(
                store,
                job_id=job_id,
                requested=llm_worker_type or None,
            )
            llm_worker_type = str(reserved.worker_type or "").strip()
            if not req_worker_type or req_worker_type != llm_worker_type:
                store.patch_request(job_id, {"llm_worker_type": llm_worker_type})
                refreshed = store.get(job_id)
                req = (refreshed.request if refreshed else req) or req
        except Exception as exc:
            msg = str(exc)
            if "capacity_exhausted" in msg:
                attempt = int(getattr(self.request, "retries", 0)) + 1
                if attempt in {1, 5}:
                    _notify_ops_telegram(
                        "[orchestrator] llm capacity saturation\n"
                        f"job_id={job_id}\n"
                        f"worker_type={llm_worker_type}\n"
                        f"attempt={attempt}\n"
                        "action=retry_build_when_capacity_frees"
                    )
                backoff = _retry_backoff_s(attempt=attempt, base_s=5.0, cap_s=120.0)
                raise self.retry(countdown=backoff, exc=RuntimeError(msg))
            raise
    elif not llm_worker_type:
        llm_worker_type = LLM_WORKER_TYPE_SDK
    store.set_status(
        job_id,
        "RUNNING",
        stage="build",
        result={
            "build_started_at": build_started_at,
            "llm_reservation_mode": reservation_mode or "legacy",
            "llm_worker_type": llm_worker_type,
        },
    )
    llm_provider_mode = _provider_mode_for_worker_type(llm_worker_type)
    audio_url = str(req.get("audio_s3_url") or "").strip()
    project_id = str(req.get("project_id") or "").strip()
    lyrics_text = str(req.get("lyrics_text") or "")
    target_fragment = str(req.get("target_fragment") or "")
    reuse_text_job_id = str(req.get("reuse_text_job_id") or "").strip()
    reuse_stage2_footage = bool(req.get("reuse_stage2_footage"))
    stage2_selection_seed_override = str(req.get("stage2_selection_seed_override") or "").strip()
    exclude_raw = req.get("exclude_file_names")
    exclude_file_names: List[str] = []
    if isinstance(exclude_raw, list):
        seen_exclude: set[str] = set()
        for it in exclude_raw:
            name = str(it or "").strip()
            if not name or name in seen_exclude:
                continue
            seen_exclude.add(name)
            exclude_file_names.append(name)

    rotation_theme = str(req.get("rotation_theme") or "").strip()
    rotation_tags_group = str(req.get("rotation_tags_group") or "").strip()
    # Modes: (theme+group) = exact slot lock; (theme only) = theme lock, group
    # picked by lyrics; (neither) = no override. A group without a theme is
    # invalid.
    if rotation_tags_group and not rotation_theme:
        raise RuntimeError("rotation_tags_group requires rotation_theme")

    variant_index: Optional[int] = None
    variant_total: Optional[int] = None
    try:
        if req.get("variant_index") is not None:
            variant_index = int(req.get("variant_index"))
    except Exception:
        variant_index = None
    try:
        if req.get("variants_total") is not None:
            variant_total = int(req.get("variants_total"))
    except Exception:
        variant_total = None
    if variant_index is not None and variant_index <= 0:
        raise RuntimeError(f"variant_index must be > 0, got {variant_index!r}")
    if variant_total is not None and variant_total <= 0:
        raise RuntimeError(f"variants_total must be > 0, got {variant_total!r}")
    if variant_index is not None and variant_total is not None and variant_index > variant_total:
        raise RuntimeError(
            f"variant_index must be <= variants_total (got {variant_index} > {variant_total})"
        )

    subtitles_mode = normalize_subtitles_mode(
        str(req.get("subtitles_mode") or ""),
        default=SUBTITLES_MODE_LEGACY_BLOCKS,
    )
    footage_artist_id = str(req.get("footage_artist_id") or "").strip()
    user_clip_start_sec: Optional[float] = None
    user_clip_end_sec: Optional[float] = None
    if req.get("user_clip_start_sec") is not None or req.get("user_clip_end_sec") is not None:
        try:
            if req.get("user_clip_start_sec") is not None:
                user_clip_start_sec = float(req.get("user_clip_start_sec"))
            if req.get("user_clip_end_sec") is not None:
                user_clip_end_sec = float(req.get("user_clip_end_sec"))
        except Exception as e:
            raise RuntimeError(
                f"invalid user clip window values start={req.get('user_clip_start_sec')!r} "
                f"end={req.get('user_clip_end_sec')!r}"
            ) from e
        if (user_clip_start_sec is None) != (user_clip_end_sec is None):
            raise RuntimeError("user clip window requires both user_clip_start_sec and user_clip_end_sec")
        if user_clip_start_sec is not None and user_clip_end_sec is not None:
            if user_clip_start_sec < 0.0:
                raise RuntimeError(f"user_clip_start_sec must be >= 0, got {user_clip_start_sec!r}")
            if user_clip_end_sec <= user_clip_start_sec:
                raise RuntimeError(
                    f"user_clip_end_sec must be > user_clip_start_sec "
                    f"(got {user_clip_start_sec!r}..{user_clip_end_sec!r})"
                )
    if not audio_url:
        raise RuntimeError("missing audio_s3_url")
    if not _is_remote_url(audio_url):
        # Вот тут “строгость”: не позволяем запускать пайплайн с локальным путём
        raise RuntimeError(f"audio_s3_url must be remote (http/https/s3). got={audio_url!r}")

    audio_name_raw = (audio_url.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()
    audio_name = (unquote(audio_name_raw) or audio_name_raw).strip()
    local_audio = paths.data_dir / "inputs" / "audio" / audio_name
    _download(audio_url, local_audio, timeout_s=600.0)

    # Build LLM cache key after audio is on disk (hash requires the bytes).
    # telegram_id comes from chat_id injected by the bot into the job request.
    _llm_ck = None
    _llm_cache_lock_held = False
    try:
        from . import llm_cache as _llm_cache_mod
        _asr_mode = "forced_alignment" if str(lyrics_text or "").strip() else "asr"
        _user_drop_t_for_cache: Optional[float] = None
        if req.get("user_drop_t") is not None:
            try:
                _user_drop_t_for_cache = float(req["user_drop_t"])
            except Exception:
                pass
        _llm_ck = _llm_cache_mod.build_cache_key(
            telegram_id=str(req.get("chat_id") or "").strip(),
            audio_hash=_llm_cache_mod.compute_audio_hash(local_audio),
            clip_start_sec=user_clip_start_sec,
            clip_end_sec=user_clip_end_sec,
            asr_mode=_asr_mode,
            lyrics_text=lyrics_text,
            subtitles_mode=subtitles_mode,
            user_drop_t=_user_drop_t_for_cache,
        )
    except Exception as _ck_err:
        log.warning("llm_cache key_build_failed job=%s err=%r", job_id, _ck_err)

    mode = str(req.get("mode") or "with_gemini")
    build_cmd = (
        f"{SETTINGS.pipeline_cmd} "
        f"--out-dir {paths.out_dir.as_posix()} "
        f"--full-edit {paths.data_dir.as_posix()}/full_edit_config.json "
        f"--footage {paths.data_dir.as_posix()}/footage_config.json "
        f"--skip-llm"
    )

    env = os.environ.copy()
    env["DATA_DIR"] = str(paths.data_dir)
    env["OUT_DIR"] = str(paths.out_dir)
    env["JOB_ID"] = str(job_id)
    _apply_runtime_llm_env_overrides(env, store)

    # make pipeline use THIS job audio
    env["AUDIO_FILE_PATH"] = str(local_audio)
    env["AUDIO_DIR"] = str(local_audio.parent)
    # Keep the final AE audio file_name deterministic and filesystem-safe on Windows.
    audio_ext = (Path(audio_name).suffix or Path(audio_name_raw).suffix or ".mp3").lower()
    if not audio_ext.startswith("."):
        audio_ext = f".{audio_ext}"
    env["AUDIO_FILE_NAME"] = f"audio_source{audio_ext}"

    env["AE_MEDIA_MODE"] = "appdir"
    env["LLM_WORKER_TYPE"] = llm_worker_type
    env["LLM_PROVIDER_MODE"] = llm_provider_mode
    env["LYRICS_TEXT"] = lyrics_text
    env["TARGET_FRAGMENT"] = target_fragment
    env["SUBTITLES_MODE"] = subtitles_mode
    if footage_artist_id:
        env["FOOTAGE_ARTIST_ID"] = footage_artist_id

    bg_mode = str(req.get("bg_mode") or "footage").strip().lower() or "footage"
    if bg_mode not in {"footage", "solid", "solid_strobe", "photo"}:
        raise RuntimeError(f"invalid bg_mode={bg_mode!r}")
    bg_solid_color_key = str(req.get("bg_solid_color") or "").strip().lower()
    bg_solid_hex_by_key = {"white": "#FFFFFF", "black": "#000000", "green": "#00FF00"}
    if bg_mode == "solid":
        if bg_solid_color_key not in bg_solid_hex_by_key:
            raise RuntimeError(
                f"bg_mode=solid requires bg_solid_color in {sorted(bg_solid_hex_by_key)}, "
                f"got {bg_solid_color_key!r}"
            )
        env["BG_MODE"] = "solid"
        env["BG_SOLID_COLOR_HEX"] = bg_solid_hex_by_key[bg_solid_color_key]
        # Default subtitle fill is white; flip to black on white background so
        # text stays readable. Other bg colors leave subtitles untouched.
        if bg_solid_color_key == "white":
            env["SUBTITLES_FORCE_FILL_HEX"] = "#000000"
    elif bg_mode == "solid_strobe":
        # B/W strobe bg + Difference-blend text. The auto-invert needs WHITE text,
        # so force white fill (custom subtitle color is ignored in this mode).
        env["BG_MODE"] = "solid_strobe"
        env["SUBTITLES_FORCE_FILL_HEX"] = "#FFFFFF"
    elif bg_mode == "photo":
        # Photo flow (4:3). The picker is media-agnostic, so we just point it at
        # the PHOTO pool (same buckets/ranking) and switch the build to the photo
        # template. Hard-validate the two F3-style selections (No Fallback Policy).
        env["BG_MODE"] = "photo"
        photo_style = str(req.get("photo_style") or "none").strip().lower() or "none"
        photo_transition = str(req.get("photo_transition") or "flash").strip().lower() or "flash"
        _photo_styles = {"none", "warm", "cold", "vintage", "bw", "vhs"}
        _photo_transitions = {"flash", "none", "slide", "zoom", "whip"}
        if photo_style not in _photo_styles:
            raise RuntimeError(f"bg_mode=photo: invalid photo_style={photo_style!r}")
        if photo_transition not in _photo_transitions:
            raise RuntimeError(f"bg_mode=photo: invalid photo_transition={photo_transition!r}")
        env["PHOTO_STYLE"] = photo_style
        env["PHOTO_TRANSITION"] = photo_transition
        # Photo pool paths (defaults; overridable per-deploy). We DON'T override
        # FOOTAGE_INVENTORY_JSON / FOOTAGE_STYLE_METADATA_DB_PATHS_JSON here: the
        # in-process env bridge only pushes _LLM_ENV_KEYS and POPS the rest, which
        # would strip the footage pool on normal jobs. Instead the build step
        # (run.py) reads BG_MODE=photo + these PHOTO_* paths and routes the picker
        # to the photo pool itself.
        env["PHOTO_INVENTORY_JSON"] = str(
            os.environ.get("PHOTO_INVENTORY_JSON") or "data/photo_inventory.json"
        ).strip()
        env["PHOTO_TAGS_SNAPSHOT_JSON"] = str(
            os.environ.get("PHOTO_TAGS_SNAPSHOT_JSON") or "data/photo_tags_snapshot.json"
        ).strip()
    else:
        env["BG_MODE"] = "footage"

    # Customization colors (override the bg-driven default above). subtitle →
    # SUBTITLES_FORCE_FILL_HEX (all modes); accent → F2 shape + focus word.
    def _norm_hex(v: Any) -> Optional[str]:
        s = str(v or "").strip()
        if not s:
            return None
        if not s.startswith("#"):
            s = "#" + s
        return s
    _sub_color = _norm_hex(req.get("subtitle_color_hex"))
    if _sub_color:
        env["SUBTITLES_FORCE_FILL_HEX"] = _sub_color
    _accent_color = _norm_hex(req.get("accent_color_hex"))
    if _accent_color:
        env["F2_SHAPE_COLOR_HEX"] = _accent_color
        env["SUBTITLES_FOCUS_HEX"] = _accent_color
    if user_clip_start_sec is not None and user_clip_end_sec is not None:
        env["USER_CLIP_START_SEC"] = str(float(user_clip_start_sec))
        env["USER_CLIP_END_SEC"] = str(float(user_clip_end_sec))
    # Hook feature pass-through. HOOK_ENABLED is a boolean flag the pipeline
    # uses to decide whether to surface hook-specific AE-FX downstream.
    # USER_DROP_T, when present, replaces the algorithmic top-1 drop candidate
    # in hook_aware Stage2 mode (the original computed value is still saved
    # to disk for A/B comparison).
    hook_enabled_raw = req.get("hook_enabled")
    if hook_enabled_raw is True or str(hook_enabled_raw).strip().lower() == "true":
        env["HOOK_ENABLED"] = "true"
    user_drop_t_raw = req.get("user_drop_t")
    if user_drop_t_raw is not None:
        try:
            user_drop_t = float(user_drop_t_raw)
        except Exception as e:
            raise RuntimeError(f"invalid user_drop_t={user_drop_t_raw!r}") from e
        if user_drop_t < 0.0:
            raise RuntimeError(f"user_drop_t must be >= 0 (got {user_drop_t!r})")
        if user_clip_start_sec is not None and user_clip_end_sec is not None:
            if not (user_clip_start_sec <= user_drop_t <= user_clip_end_sec):
                raise RuntimeError(
                    f"user_drop_t={user_drop_t!r} must be within user clip window "
                    f"[{user_clip_start_sec}, {user_clip_end_sec}]"
                )
        env["USER_DROP_T"] = str(user_drop_t)
    # F5 Cognition («Мысль») device pass-through. When set, switches on the F5
    # hook pipeline in mlcore.hooks.f5_cognition.orchestrator_hook (it reads
    # F5_HOOK_DEVICE from the build subprocess env). Absent => no F5 hook.
    hook_device_raw = req.get("hook_device")
    if hook_device_raw is not None:
        hook_device = str(hook_device_raw).strip().lower()
        allowed_devices = {
            "punchline",
            "missing_word",
            "lyric_echo",
            "question_to_track",
            "inverse_lyric",
        }
        if hook_device not in allowed_devices:
            raise RuntimeError(
                f"invalid hook_device={hook_device_raw!r}; allowed={sorted(allowed_devices)}"
            )
        env["F5_HOOK_DEVICE"] = hook_device
    # F4 «Движение» device pass-through. When set, the orchestrator emits
    # full_edit_config["f4"] {device, bpm} and project_builder injects the AE
    # overlay JSX. Absent => no F4 hook.
    f4_device_raw = req.get("f4_device")
    if f4_device_raw is not None:
        f4_device = str(f4_device_raw).strip().lower()
        allowed_f4_devices = {"swipe", "tap", "pinch", "holdfinger", "head"}
        if f4_device not in allowed_f4_devices:
            raise RuntimeError(
                f"invalid f4_device={f4_device_raw!r}; allowed={sorted(allowed_f4_devices)}"
            )
        env["F4_HOOK_DEVICE"] = f4_device
    # BPM the bot used for the F4 clip-window reframe — overlay must use the same.
    _f4_bpm_raw = req.get("f4_bpm")
    if _f4_bpm_raw is not None:
        try:
            _f4_bpm_val = float(_f4_bpm_raw)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"invalid f4_bpm={_f4_bpm_raw!r}") from e
        if _f4_bpm_val > 0.0:
            env["F4_BPM"] = repr(_f4_bpm_val)
    # F3 «Эффект» visual-FX selection pass-through. Set => orchestrator emits
    # full_edit_config["f3"] and project_builder injects the AE overlay JSX.
    # Requires USER_DROP_T (drop anchor); absent ids => no F3 fx.
    _f3_allowed = {
        "hook": {"hook_light", "shutter_effect", "flash_slow_shutter", "negative_zoom"},
        "transition": {"snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts", "layer_shake"},
        "extra": {
            "xerox", "analog_glitch", "neon_extract", "old_camera",
            "blackwhite", "crystal_glow", "night_vision", "wave",
        },
    }
    for _req_key, _env_key, _group in (
        ("effect_hook", "F3_HOOK", "hook"),
        ("effect_transition", "F3_TRANSITION", "transition"),
        ("effect_extra", "F3_EXTRA", "extra"),
    ):
        _raw = req.get(_req_key)
        if _raw is not None and str(_raw).strip():
            _val = str(_raw).strip().lower()
            if _val not in _f3_allowed[_group]:
                raise RuntimeError(
                    f"invalid {_req_key}={_raw!r}; allowed={sorted(_f3_allowed[_group])}"
                )
            env[_env_key] = _val
    # Stretch the EXTRA (grade, e.g. xerox) over the whole video instead of just
    # the pre-drop region — bumps uniqueness. Bool flag → "1" env.
    if bool(req.get("effect_extra_full")):
        env["F3_EXTRA_FULL"] = "1"
    _f3_extend_raw = req.get("effect_hook_extend")
    if _f3_extend_raw is not None and str(_f3_extend_raw).strip():
        _ext = str(_f3_extend_raw).strip().lower()
        _ext_ok = _ext == "to_end" or (
            _ext.startswith("after_drop:") and _ext.split(":", 1)[1].isdigit()
            and int(_ext.split(":", 1)[1]) >= 1
        )
        if not _ext_ok:
            raise RuntimeError(
                f"invalid effect_hook_extend={_f3_extend_raw!r}; expected 'to_end' or 'after_drop:N'"
            )
        env["F3_HOOK_EXTEND"] = _ext
    # F2 «Объект» packaged-combo selection pass-through. Set => orchestrator
    # emits full_edit_config["f2"] (shape on pre-drop cuts + hook_light at
    # drop + seeded-random F3 transition on post-drop cuts) and
    # project_builder injects the AE overlay JSX. Requires USER_DROP_T;
    # absent => no F2 combo.
    _f2_allowed_shapes = {"rhomb", "square", "star1", "star2", "elipse"}
    _f2_shape_raw = req.get("f2_shape")
    if _f2_shape_raw is not None and str(_f2_shape_raw).strip():
        _f2_shape = str(_f2_shape_raw).strip().lower()
        if _f2_shape not in _f2_allowed_shapes:
            raise RuntimeError(
                f"invalid f2_shape={_f2_shape_raw!r}; allowed={sorted(_f2_allowed_shapes)}"
            )
        env["F2_SHAPE"] = _f2_shape
    # F1 «Звук» pass-through: S3/HTTP URL of the user-uploaded pre-drop sound.
    # Set => orchestrator emits full_edit_config["f1"] (audio + visual combo).
    # Requires USER_DROP_T; absent => no F1.
    _f1_sound_raw = req.get("f1_sound_url")
    if _f1_sound_raw is not None and str(_f1_sound_raw).strip():
        _f1_sound = str(_f1_sound_raw).strip()
        if not _is_remote_url(_f1_sound):
            raise RuntimeError(
                f"f1_sound_url must be remote (http/https/s3). got={_f1_sound!r}"
            )
        env["F1_SOUND_URL"] = _f1_sound
        # Optional subtitle text for the F1 sound (only meaningful with a sound).
        _f1_text_raw = req.get("f1_sound_text")
        if _f1_text_raw is not None and str(_f1_text_raw).strip():
            env["F1_SOUND_TEXT"] = str(_f1_text_raw).strip()
    if exclude_file_names:
        env["FOOTAGE_EXCLUDE_FILE_NAMES_JSON"] = json.dumps(exclude_file_names, ensure_ascii=False)
    if rotation_theme:
        env["FOOTAGE_ROTATION_THEME"] = rotation_theme
        if rotation_tags_group:
            env["FOOTAGE_ROTATION_GROUP"] = rotation_tags_group
    # Wave 1 Поток B: the picker's global per-bucket cooldown ledger (footage_usage)
    # needs the DSN + the serving chat. Passed explicitly so it doesn't depend on
    # CREDITS_DB_URL being present in the raw subprocess env.
    _usage_db_url = str(getattr(SETTINGS, "credits_db_url", "") or "").strip()
    if _usage_db_url:
        env["FOOTAGE_USAGE_DB_URL"] = _usage_db_url
    env["FOOTAGE_USAGE_CHAT_ID"] = str(req.get("chat_id") or "").strip()
    seed_variant = variant_index if variant_index is not None else 1
    seed_base = project_id or f"job-{job_id}"
    env["STAGE2_SELECTION_SEED"] = f"{seed_base}:v{seed_variant}"
    if stage2_selection_seed_override:
        # Bigtest cases 1-27: pin to case-0's seed so footage_picker picks the
        # same clips regardless of the per-case batch_id randomisation.
        env["STAGE2_SELECTION_SEED"] = stage2_selection_seed_override
    env["BATCH_VARIANT_INDEX"] = str(seed_variant)
    if variant_total is not None:
        env["BATCH_VARIANTS_TOTAL"] = str(int(variant_total))
    if reuse_text_job_id:
        env["REUSE_TEXT_JOB_ID"] = reuse_text_job_id

    build_all_fn = None
    if mode != "no_gemini":
        from mlcore.gemini_orchestrator import build_all_via_gemini_one_call
        build_all_fn = build_all_via_gemini_one_call

        store.set_status(job_id, "RUNNING", stage="llm_stage1")
        backup: Dict[str, str | None] = {}
        for k in _LLM_ENV_KEYS:
            backup[k] = os.environ.get(k)
            if k in env:
                os.environ[k] = env[k]
            else:
                os.environ.pop(k, None)

        try:
            if reuse_text_job_id:
                store.set_status(job_id, "RUNNING", stage="llm_seed_reuse_text")
                _seed_resume_state_from_source_job(
                    work_dir=SETTINGS.work_dir,
                    source_job_id=reuse_text_job_id,
                    target_resume_state_path=llm_resume_state_path,
                    store=store,
                    include_footage=reuse_stage2_footage,
                )
                _persist_resume_state_snapshot(
                    store=store,
                    job_id=job_id,
                    resume_state_path=llm_resume_state_path,
                    source="reuse_text_seed",
                )
            elif _llm_ck is not None:
                # Try to pre-populate resume state from S3 cache so the orchestrator
                # can skip already-computed stages. reuse_text_job_id takes priority
                # (explicit operator override), so we only do this in the else branch.
                try:
                    cache_hits = _llm_cache_mod.try_populate_resume_state(
                        _llm_ck, llm_resume_state_path
                    )
                    if any(cache_hits.values()):
                        log.info(
                            "llm_cache_populated job=%s hits=%s",
                            job_id,
                            {k: v for k, v in cache_hits.items() if v},
                        )
                        _persist_resume_state_snapshot(
                            store=store,
                            job_id=job_id,
                            resume_state_path=llm_resume_state_path,
                            source="llm_cache_seed",
                        )
                except Exception as _pop_err:
                    log.warning("llm_cache populate_error job=%s err=%r", job_id, _pop_err)
                try:
                    _llm_cache_lock_held = _llm_cache_mod.try_acquire_lock(store.r, _llm_ck)
                except Exception as _lock_err:
                    log.warning("llm_cache lock_error job=%s err=%r", job_id, _lock_err)
            def _progress(stage: Any) -> None:
                s = str(stage)
                # Bigtest safety-breaker signal: when this job was asked to reuse
                # text (reuse_text_job_id set) but Stage1 ASR is actually being
                # invoked, the resume failed. Persist a sticky result flag so the
                # bot can halt the whole /bigtest batch before more tokens burn.
                if reuse_text_job_id and s == "llm_stage1a_asr_invoke":
                    store.set_status(
                        job_id, "RUNNING", stage=s,
                        result={"reuse_stage1_miss": True},
                    )
                else:
                    store.set_status(job_id, "RUNNING", stage=s)

            build_all_fn(
                progress_cb=_progress,
                resume_state_path=llm_resume_state_path,
            )
            if _llm_ck is not None and _llm_cache_lock_held:
                try:
                    _llm_cache_mod.save_resume_state_to_cache(_llm_ck, llm_resume_state_path)
                except Exception as _save_err:
                    log.warning("llm_cache save_error job=%s err=%r", job_id, _save_err)
            _persist_resume_state_snapshot(
                store=store,
                job_id=job_id,
                resume_state_path=llm_resume_state_path,
                source="llm_success",
            )
        except Exception as e:
            try:
                _persist_resume_state_snapshot(
                    store=store,
                    job_id=job_id,
                    resume_state_path=llm_resume_state_path,
                    source="llm_exception_partial",
                )
            except Exception as persist_exc:
                log.warning("resume_state_partial_persist_failed job=%s err=%r", job_id, persist_exc)
            text = _exc_text(e)
            _maybe_retry_gemini_transport_disconnect(self, store, text, phase="build_all")
            if _looks_like_gemini_internal_500(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("gemini_internal_500"))
            if _looks_like_gemini_overloaded_503(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _overloaded_retry_backoff_s(attempt=attempt)
                raise self.retry(countdown=backoff, exc=RuntimeError("gemini_overloaded_503"))
            if _looks_like_gemini_rate_limited_429(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("gemini_rate_limited_429"))
            if _looks_like_openrouter_timeout(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_timeout"))
            if _looks_like_openrouter_gateway_timeout_524(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                raise self.retry(
                    countdown=backoff,
                    exc=RuntimeError("openrouter_gateway_timeout_524"),
                )
            if _looks_like_openrouter_internal_500(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_internal_500"))
            if _looks_like_openrouter_provider_unavailable_502(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                raise self.retry(
                    countdown=backoff, exc=RuntimeError("openrouter_provider_unavailable_502")
                )
            if _looks_like_openrouter_bad_request_400(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                log.warning(
                    "openrouter_retry_on_http_400 attempt=%d/%d backoff_s=%.1f err=%s",
                    attempt,
                    int(getattr(self, "max_retries", 0) or 0),
                    backoff,
                    text[:800],
                )
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_bad_request_400"))
            if _looks_like_openrouter_overloaded_503(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _overloaded_retry_backoff_s(attempt=attempt)
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_overloaded_503"))
            if _looks_like_openrouter_rate_limited_429(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_rate_limited_429"))
            if _looks_like_stage1a_selected_fragment_missing(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=8.0, cap_s=180.0)
                raise self.retry(
                    countdown=backoff,
                    exc=RuntimeError("stage1a_selected_fragment_missing"),
                )
            raise
        finally:
            if _llm_cache_lock_held and _llm_ck is not None:
                try:
                    _llm_cache_mod.release_lock(store.r, _llm_ck)
                except Exception as _rel_err:
                    log.warning("llm_cache lock_release_error job=%s err=%r", job_id, _rel_err)
                _llm_cache_lock_held = False
            for k, old in backup.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    store.set_status(job_id, "RUNNING", stage="build")

    args = shlex.split(build_cmd)

    def _run_build_subprocess_once() -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=str(repo_root), env=env, capture_output=True, text=True)

    def _maybe_retry_transient(blob: str) -> None:
        _maybe_retry_gemini_transport_disconnect(self, store, blob, phase="build_subprocess")
        if _looks_like_gemini_internal_500(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("gemini_internal_500"))
        if _looks_like_openrouter_timeout(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_timeout"))
        if _looks_like_openrouter_gateway_timeout_524(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_gateway_timeout_524"))
        if _looks_like_openrouter_internal_500(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_internal_500"))
        if _looks_like_openrouter_provider_unavailable_502(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_provider_unavailable_502"))
        if _looks_like_openrouter_bad_request_400(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            log.warning(
                "openrouter_retry_on_http_400 attempt=%d/%d backoff_s=%.1f err=%s",
                attempt,
                int(getattr(self, "max_retries", 0) or 0),
                backoff,
                blob[:800],
            )
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_bad_request_400"))
        if _looks_like_openrouter_overloaded_503(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _overloaded_retry_backoff_s(attempt=attempt)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_overloaded_503"))
        if _looks_like_openrouter_rate_limited_429(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_rate_limited_429"))
        if _looks_like_stage1a_selected_fragment_missing(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=8.0, cap_s=180.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("stage1a_selected_fragment_missing"))

    proc = _run_build_subprocess_once()
    out = proc.stdout or ""
    err = proc.stderr or ""

    if proc.returncode != 0:
        blob_first = out + "\n" + err
        _maybe_retry_transient(blob_first)

        # Preflight validation:
        # - with_gemini: targeted subtitles rerun (+retry hint), then one immediate local build retry
        # - no_gemini: one immediate local build retry
        if _looks_like_build_preflight_validation_error(blob_first):
            if build_all_fn is not None:
                store.set_status(job_id, "RUNNING", stage="llm_stage2_subtitles_retry")
                _drop_resume_stage_key(llm_resume_state_path, key="stage2_subtitles")
                try:
                    _persist_resume_state_snapshot(
                        store=store,
                        job_id=job_id,
                        resume_state_path=llm_resume_state_path,
                        source="preflight_drop_stage2_subtitles",
                    )
                except Exception as persist_exc:
                    log.warning("resume_state_drop_persist_failed job=%s err=%r", job_id, persist_exc)
                retry_hint = _build_stage2_subtitles_retry_hint(blob_first)
                llm_backup: Dict[str, str | None] = {}
                for k in _LLM_ENV_KEYS:
                    llm_backup[k] = os.environ.get(k)
                    if k in env:
                        os.environ[k] = env[k]
                    else:
                        os.environ.pop(k, None)
                old_retry_hint = os.environ.get("STAGE2_SUBTITLES_RETRY_HINT")
                try:
                    os.environ["STAGE2_SUBTITLES_RETRY_HINT"] = retry_hint
                    try:
                        build_all_fn(
                            progress_cb=lambda stage: store.set_status(job_id, "RUNNING", stage=str(stage)),
                            resume_state_path=llm_resume_state_path,
                        )
                        if _llm_ck is not None and _llm_cache_lock_held:
                            try:
                                _llm_cache_mod.save_resume_state_to_cache(
                                    _llm_ck, llm_resume_state_path
                                )
                            except Exception as _save_err:
                                log.warning(
                                    "llm_cache save_error job=%s phase=preflight_retry err=%r",
                                    job_id, _save_err,
                                )
                        _persist_resume_state_snapshot(
                            store=store,
                            job_id=job_id,
                            resume_state_path=llm_resume_state_path,
                            source="llm_preflight_retry_success",
                        )
                    except Exception as e:
                        try:
                            _persist_resume_state_snapshot(
                                store=store,
                                job_id=job_id,
                                resume_state_path=llm_resume_state_path,
                                source="llm_preflight_retry_exception_partial",
                            )
                        except Exception as persist_exc:
                            log.warning("resume_state_retry_partial_persist_failed job=%s err=%r", job_id, persist_exc)
                        text = _exc_text(e)
                        _maybe_retry_gemini_transport_disconnect(self, store, text, phase="stage2_subtitles_retry")
                        if _looks_like_gemini_internal_500(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                            raise self.retry(countdown=backoff, exc=RuntimeError("gemini_internal_500"))
                        if _looks_like_gemini_overloaded_503(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _overloaded_retry_backoff_s(attempt=attempt)
                            raise self.retry(countdown=backoff, exc=RuntimeError("gemini_overloaded_503"))
                        if _looks_like_gemini_rate_limited_429(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                            raise self.retry(countdown=backoff, exc=RuntimeError("gemini_rate_limited_429"))
                        if _looks_like_openrouter_timeout(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_timeout"))
                        if _looks_like_openrouter_gateway_timeout_524(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                            raise self.retry(
                                countdown=backoff,
                                exc=RuntimeError("openrouter_gateway_timeout_524"),
                            )
                        if _looks_like_openrouter_internal_500(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_internal_500"))
                        if _looks_like_openrouter_provider_unavailable_502(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                            raise self.retry(
                                countdown=backoff,
                                exc=RuntimeError("openrouter_provider_unavailable_502"),
                            )
                        if _looks_like_openrouter_bad_request_400(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                            log.warning(
                                "openrouter_retry_on_http_400 attempt=%d/%d backoff_s=%.1f err=%s",
                                attempt,
                                int(getattr(self, "max_retries", 0) or 0),
                                backoff,
                                text[:800],
                            )
                            raise self.retry(
                                countdown=backoff,
                                exc=RuntimeError("openrouter_bad_request_400"),
                            )
                        if _looks_like_openrouter_overloaded_503(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _overloaded_retry_backoff_s(attempt=attempt)
                            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_overloaded_503"))
                        if _looks_like_openrouter_rate_limited_429(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_rate_limited_429"))
                        if _looks_like_stage1a_selected_fragment_missing(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=8.0, cap_s=180.0)
                            raise self.retry(
                                countdown=backoff,
                                exc=RuntimeError("stage1a_selected_fragment_missing"),
                            )
                        raise
                finally:
                    if old_retry_hint is None:
                        os.environ.pop("STAGE2_SUBTITLES_RETRY_HINT", None)
                    else:
                        os.environ["STAGE2_SUBTITLES_RETRY_HINT"] = old_retry_hint
                    for k, old in llm_backup.items():
                        if old is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = old

                store.set_status(job_id, "RUNNING", stage="build")

            proc_retry = _run_build_subprocess_once()
            out_retry = proc_retry.stdout or ""
            err_retry = proc_retry.stderr or ""

            if proc_retry.returncode == 0:
                proc = proc_retry
                out = out_retry
                err = err_retry
            else:
                blob_retry = out_retry + "\n" + err_retry
                if _looks_like_build_preflight_validation_error(blob_retry):
                    raise RuntimeError(
                        "build_preflight_validation_error_after_immediate_retry\n"
                        f"cmd={build_cmd}\n"
                        f"--- first stdout (tail) ---\n{out[-8000:]}\n"
                        f"--- first stderr (tail) ---\n{err[-8000:]}\n"
                        f"--- second stdout (tail) ---\n{out_retry[-8000:]}\n"
                        f"--- second stderr (tail) ---\n{err_retry[-8000:]}\n"
                    )
                _maybe_retry_transient(blob_retry)
                raise RuntimeError(
                    "pipeline_failed_after_immediate_preflight_retry "
                    f"rc={proc_retry.returncode}\ncmd={build_cmd}\n"
                    f"--- first stdout (tail) ---\n{out[-8000:]}\n"
                    f"--- first stderr (tail) ---\n{err[-8000:]}\n"
                    f"--- second stdout (tail) ---\n{out_retry[-8000:]}\n"
                    f"--- second stderr (tail) ---\n{err_retry[-8000:]}\n"
                )

        else:
            raise RuntimeError(
                f"pipeline_failed rc={proc.returncode}\ncmd={build_cmd}\n"
                f"--- stdout (tail) ---\n{out[-8000:]}\n"
                f"--- stderr (tail) ---\n{err[-8000:]}\n"
            )

    # The AE and native workers consume the same bot payload. JSX remains
    # required for AE jobs only; Rust uses the canonical JSON directly.
    render_engine = _requested_render_engine(req)
    artifacts_ready = paths.render_payload.exists() and (
        render_engine == "rust-gen" or paths.render_jsx.exists()
    )
    if not artifacts_ready:
        raise RuntimeError(
            "pipeline_ok_but_missing_artifacts: "
            f"render_jsx_exists={paths.render_jsx.exists()} "
            f"render_payload_exists={paths.render_payload.exists()} "
            f"expected_render_jsx={str(paths.render_jsx)} "
            f"expected_render_payload={str(paths.render_payload)}\n"
            f"cmd={build_cmd}\n"
            f"--- stdout (tail) ---\n{out[-8000:]}\n"
            f"--- stderr (tail) ---\n{err[-8000:]}\n"
        )

    # Best-effort: keep configs consistent
    _patch_audio_layer_to_remote(paths.footage_config, audio_url=audio_url)
    _observe_stage_duration(store, stage="build", started_at=build_started_at, outcome="succeeded")
    _obs_event("build_completed", job_id=job_id, worker_type=worker_type)

    store.set_status(
        job_id,
        "RUNNING",
        stage="dispatch",
        result={
            "build": {
                "audio_url_remote": audio_url,
                "audio_path_local": str(local_audio),
                "audio_file_name": env["AUDIO_FILE_NAME"],
            }
        },
    )
    render_queue = _job_queue_from_request(
        req,
        key="render_queue",
        default=SETTINGS.celery_queue_render,
    )
    dispatch_task = dispatch_to_rust_gen if render_engine == "rust-gen" else dispatch_to_windows
    if render_queue:
        dispatch_task.apply_async(args=[job_id], queue=render_queue)
    else:
        dispatch_task.delay(job_id)
    return {"ok": True, "stage": "build_done", "paths": paths.manifest()}


# Task names whose first positional arg is the job_id, used by the orphan-reaper
# below to flip a job to FAILED when its worker dies mid-execution.
_JOB_ID_FIRST_ARG_TASKS = frozenset({
    "orchestrator.build_job",
    "orchestrator.build_job_sdk",
    "orchestrator.build_job_openrouter",
    "orchestrator.build_job_hybrid",
    "orchestrator.build_job_vertex_sdk_mix",
})


@task_failure.connect
def _reap_orphaned_job_on_failure(sender=None, task_id=None, exception=None,
                                  args=None, kwargs=None, einfo=None, **_extra):
    """Flip a job to FAILED when its build task dies without a terminal update.

    A native SIGSEGV (librosa/grpc/etc.) kills the Celery child mid-task, so the
    in-task try/finally never runs and the job is left stuck RUNNING forever
    (the bot then polls `llm_stage2_parallel` indefinitely). Celery's parent
    still raises WorkerLostError → fires this signal, which lets us mark the job
    FAILED so the bot surfaces the error instead of hanging. Also covers any
    other unhandled task exception that escaped the impl's own error handling.
    """
    try:
        name = getattr(sender, "name", "") or ""
        if name not in _JOB_ID_FIRST_ARG_TASKS:
            return
        job_id = None
        if args:
            job_id = args[0]
        elif kwargs:
            job_id = kwargs.get("job_id")
        job_id = str(job_id or "").strip()
        if not job_id:
            return
        store = JobStore.from_env()
        cur = store.get(job_id)
        # Don't clobber a job that already reached a terminal state (e.g. the
        # impl caught the error and set FAILED, or a retry later SUCCEEDED).
        if cur is not None and str(getattr(cur, "status", "")).upper() in {"SUCCEEDED", "FAILED"}:
            return
        err = f"worker_lost_or_unhandled: {type(exception).__name__}: {exception}"
        store.set_status(job_id, "FAILED", stage="worker_lost", error=err[:500])
        log.error("orphaned_job_reaped job_id=%s task=%s err=%s", job_id, name, err)
    except Exception as e:  # never let the signal handler itself raise
        try:
            log.warning("orphan_reaper_failed task_id=%s err=%s", task_id, str(e))
        except Exception:
            pass


@celery_app.task(name="orchestrator.build_job", bind=True, max_retries=8)
def build_job(self, job_id: str) -> Dict[str, Any]:
    # Backward-compatible task name kept for already deployed callers.
    return _build_job_impl(self, job_id, worker_type=None)


@celery_app.task(name="orchestrator.build_job_sdk", bind=True, max_retries=8)
def build_job_sdk(self, job_id: str) -> Dict[str, Any]:
    return _build_job_impl(self, job_id, worker_type="sdk")


@celery_app.task(name="orchestrator.build_job_openrouter", bind=True, max_retries=8)
def build_job_openrouter(self, job_id: str) -> Dict[str, Any]:
    return _build_job_impl(self, job_id, worker_type="openrouter")


@celery_app.task(name="orchestrator.build_job_hybrid", bind=True, max_retries=8)
def build_job_hybrid(self, job_id: str) -> Dict[str, Any]:
    return _build_job_impl(self, job_id, worker_type="hybrid")


@celery_app.task(name="orchestrator.build_job_vertex_sdk_mix", bind=True, max_retries=8)
def build_job_vertex_sdk_mix(self, job_id: str) -> Dict[str, Any]:
    return _build_job_impl(self, job_id, worker_type="vertex_sdk_mix")


_FOOTAGE_TAGGING_PROGRESS_KEY = "footage_tagging:progress"
# Photo pool (media_type=photo) progress keys — separate single-flight from the
# footage pool so both can run/poll independently.
_PHOTO_TAGGING_PROGRESS_KEY = "photo_tagging:progress"
_PHOTO_ACTIVATION_PROGRESS_KEY = "photo_activation:progress"


def _footage_tagging_source_prefix() -> str:
    # Single source of truth for the footage pool prefix — shared with the
    # manual index builder so index-scan == tag-scan == Asset-UI browse.
    from scripts.build_static_assets_index import resolve_pool_source_prefix

    return resolve_pool_source_prefix()


def _photo_tagging_source_prefix() -> str:
    explicit = (os.environ.get("ASSET_UI_PHOTO_SOURCE_PREFIX") or "").strip().strip("/")
    if explicit:
        return explicit
    photo_prefix = (os.environ.get("S3_PHOTO_PREFIX") or "").strip().strip("/")
    if photo_prefix:
        return photo_prefix.split("/", 1)[0]
    return "photo_collection"


def _norm_media_type(media_type: Any) -> str:
    mt = str(media_type or "video").strip().lower() or "video"
    if mt not in ("video", "photo"):
        raise RuntimeError(f"invalid media_type={mt!r} (expected video|photo)")
    return mt


@celery_app.task(name="orchestrator.tag_untagged_footage", bind=True, max_retries=0)
def tag_untagged_footage(self, limit: int = 0, media_type: str = "video") -> Dict[str, Any]:
    """Tag every untagged S3 asset via Groq and upsert into footage_tags.

    media_type='video' (default) tags the footage pool; 'photo' tags the photo
    pool (run_photo_tagging_batch, S3_PHOTO_PREFIX, source='photo' snapshot).
    Progress is published to Redis (per-pool key) so the admin UI can poll status.
    Single-flight is enforced by the API endpoint before enqueue, not here.
    """
    mt = _norm_media_type(media_type)
    progress_key = _PHOTO_TAGGING_PROGRESS_KEY if mt == "photo" else _FOOTAGE_TAGGING_PROGRESS_KEY

    # Publish "running" and a Redis handle FIRST, before any validation, so an
    # early failure (missing env, bad config) surfaces as state="failed" with a
    # message in the UI instead of leaving the UI stuck on "queued".
    r = JobStore.from_env().r

    def _publish(state: str, **extra: Any) -> None:
        payload = {"state": state, "updated_at": time.time(), "media_type": mt, **extra}
        try:
            r.set(progress_key, json.dumps(payload), ex=86400)
        except Exception:
            pass

    def _progress(done: int, total: int, written: int) -> None:
        _publish("running", done=int(done), total=int(total), written=int(written))

    _publish("running", done=0, total=0, written=0)
    try:
        bucket = str(os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
        db_url = str(getattr(SETTINGS, "credits_db_url", "") or "").strip()
        if not bucket:
            raise RuntimeError("S3_BUCKET_ASSET_STORAGE not configured")
        if not db_url:
            raise RuntimeError("Postgres not configured (CREDITS_DB_URL / POSTGRES_*)")

        if mt == "photo":
            from mlcore.photo_tagger import run_photo_tagging_batch

            summary = run_photo_tagging_batch(
                bucket=bucket,
                source_prefix=_photo_tagging_source_prefix(),
                db_url=db_url,
                limit=int(limit or 0),
                progress_cb=_progress,
            )
        else:
            from mlcore.footage_tagger import run_tagging_batch

            summary = run_tagging_batch(
                bucket=bucket,
                source_prefix=_footage_tagging_source_prefix(),
                db_url=db_url,
                limit=int(limit or 0),
                progress_cb=_progress,
            )
    except Exception as exc:
        _publish("failed", error=str(exc))
        raise

    # Auto-export the snapshot the picker reads, so freshly tagged clips are
    # visible without a manual export step. Best-effort: a failure here doesn't
    # fail the tagging run (the upserts already landed in Postgres). On a
    # multi-node deploy this refreshes the local node; distribution to other
    # nodes still happens at deploy time.
    try:
        snap = _export_footage_tags_snapshot(db_url=db_url, source=mt)
        summary = {**summary, "snapshot": snap.get("path"), "snapshot_rows": snap.get("rows")}
    except Exception as exc:
        log.warning("footage_tags snapshot auto-export failed: %r", exc)
        summary = {**summary, "snapshot_error": str(exc)}

    # Also refresh the tag-overrides snapshot (blacklist) the picker reads, so an
    # admin blacklist change applied since the last run takes effect without a
    # separate manual export.
    try:
        ov = _export_tag_overrides_snapshot(db_url=db_url)
        summary = {**summary, "tag_overrides_rows": ov.get("rows")}
    except Exception as exc:
        log.warning("tag_overrides snapshot auto-export failed: %r", exc)

    _publish("done", **summary)
    return summary


def _export_tag_overrides_snapshot(*, db_url: str) -> Dict[str, Any]:
    """Write data/tag_overrides.json (blacklist the picker reads) from Postgres,
    preserving any existing file-based tag_assignments."""
    from mlcore.footage_overrides_db import (
        build_tag_overrides_doc,
        fetch_blacklisted_tags,
        init_schema,
    )

    out_path = Path(os.environ.get("FOOTAGE_TAG_OVERRIDES_PATH", "data/tag_overrides.json"))
    existing_assignments: List[Any] = []
    if out_path.exists():
        try:
            existing_assignments = json.loads(out_path.read_text(encoding="utf-8")).get("tag_assignments", []) or []
        except Exception:
            existing_assignments = []

    async def _go():
        import asyncpg  # type: ignore

        conn = await asyncpg.connect(dsn=db_url)
        try:
            await init_schema(conn)
            return await fetch_blacklisted_tags(conn)
        finally:
            await conn.close()

    blacklist = asyncio.run(_go())
    doc = build_tag_overrides_doc(blacklist, existing_assignments)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(out_path), "rows": len(doc["blacklisted_tags"])}


def _register_pool_assets(*, db_url: str, static_index_path: Any, source: str = "video") -> Dict[str, Any]:
    """Register the freshly-built static index into the Postgres footage_assets
    registry (upsert + prune) so the pool has a durable, queryable source of
    truth that survives deploys. Returns {written, pruned, total, pickable}.

    The JSON index remains the render-hot-path cache; this makes Postgres the
    place asset_ui / health read ONE canonical count (pool ∩ tags = pickable).
    """
    from mlcore.footage_assets_db import pool_health, records_from_index, replace_source_assets

    idx_obj = json.loads(Path(static_index_path).read_text(encoding="utf-8"))
    assets = idx_obj.get("assets") if isinstance(idx_obj, dict) else idx_obj
    records = records_from_index(assets or [], source=source)

    async def _go():
        import asyncpg  # type: ignore

        conn = await asyncpg.connect(dsn=db_url)
        try:
            res = await replace_source_assets(conn, records, source=source)
            health = await pool_health(conn, source=source)
            res["pickable"] = health.get("pickable")
            return res
        finally:
            await conn.close()

    return asyncio.run(_go())


def _export_footage_tags_snapshot(*, db_url: str, source: str = "video") -> Dict[str, Any]:
    """Write the picker's tag snapshot from Postgres, scoped to one pool.

    source='video' → data/footage_tags_snapshot.json (FOOTAGE_* env);
    source='photo' → data/photo_tags_snapshot.json (PHOTO_TAGS_SNAPSHOT_JSON env).
    """
    from mlcore.footage_tags_db import build_snapshot, filter_snapshot_to_pool, pick_snapshot_path

    if source == "photo":
        out_path = Path(
            (os.environ.get("PHOTO_TAGS_SNAPSHOT_JSON") or "data/photo_tags_snapshot.json").strip()
        )
    else:
        out_path = Path(pick_snapshot_path(
            explicit=os.environ.get("FOOTAGE_TAGS_SNAPSHOT_PATH", ""),
            metadata_paths_json=os.environ.get("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", ""),
        ))

    async def _go():
        import asyncpg  # type: ignore

        from mlcore.footage_assets_db import fetch_pool_clip_ids

        conn = await asyncpg.connect(dsn=db_url)
        try:
            rows = await build_snapshot(conn, source=source)
            # Drop orphan tags (clips deleted from the pool) so the picker/reports
            # never see tags for clips that no longer exist. Fail-safe: no-op when
            # the registry is empty. Non-destructive (footage_tags rows stay).
            pool_ids = await fetch_pool_clip_ids(conn, source=source)
            return filter_snapshot_to_pool(rows, pool_ids), len(rows), len(pool_ids)
        finally:
            await conn.close()

    rows, total_rows, pool_n = asyncio.run(_go())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("footage_tags_snapshot rows=%d (from %d tags, pool=%d) -> %s",
             len(rows), total_rows, pool_n, out_path)
    return {"path": str(out_path), "rows": len(rows), "orphans_dropped": max(0, total_rows - len(rows))}


_FOOTAGE_ACTIVATION_PROGRESS_KEY = "footage_activation:progress"


@celery_app.task(name="orchestrator.activate_footage_base", bind=True, max_retries=0)
def activate_footage_base(self, limit: int = 0, media_type: str = "video") -> Dict[str, Any]:
    """Full self-serve ingest after a web upload: rebuild the S3 static index ->
    rebuild the picker inventory -> tag untagged clips (Groq) -> export the tags
    & blacklist snapshots. Makes freshly-uploaded assets enter the selection pool
    without any server-side command. Progress phases published to Redis.

    media_type='video' (default) ingests the footage pool; 'photo' ingests the
    photo pool (photo index/inventory/prefix + photo tagger + source='photo'
    snapshot), parallel and independent from the footage pool.
    """
    from pathlib import Path as _Path

    mt = _norm_media_type(media_type)
    is_photo = mt == "photo"
    progress_key = _PHOTO_ACTIVATION_PROGRESS_KEY if is_photo else _FOOTAGE_ACTIVATION_PROGRESS_KEY

    bucket = str(os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
    db_url = str(getattr(SETTINGS, "credits_db_url", "") or "").strip()
    r = JobStore.from_env().r

    def _publish(state: str, **extra: Any) -> None:
        try:
            r.set(progress_key, json.dumps({"state": state, "updated_at": time.time(), "media_type": mt, **extra}), ex=86400)
        except Exception:
            pass

    _publish("running", phase="starting")
    try:
        if not bucket:
            raise RuntimeError("S3_BUCKET_ASSET_STORAGE not configured")
        if not db_url:
            raise RuntimeError("Postgres not configured (CREDITS_DB_URL / POSTGRES_*)")

        repo_root = _Path(__file__).resolve().parents[2]
        if is_photo:
            static_index_path = _Path(
                os.environ.get("PHOTO_ASSETS_INDEX_JSON", str(repo_root / "data" / "photo_assets_index_1to1.json"))
            )
            inventory_out = _Path(os.environ.get("PHOTO_INVENTORY_JSON", str(repo_root / "data" / "photo_inventory.json")))
            bundle_out = _Path(os.environ.get("PHOTO_DESCRIPTIONS_BUNDLE_OUT", str(repo_root / "pins" / "photo_descriptions_bundle.json")))
            prefix = _photo_tagging_source_prefix()
        else:
            static_index_path = _Path(
                os.environ.get("STATIC_ASSETS_INDEX_JSON", str(repo_root / "data" / "static_assets_index_1to1.json"))
            )
            inventory_out = _Path(os.environ.get("FOOTAGE_INVENTORY_OUT", str(repo_root / "data" / "footage_inventory.json")))
            bundle_out = _Path(os.environ.get("DESCRIPTIONS_BUNDLE_OUT", str(repo_root / "pins" / "descriptions_bundle.json")))
            prefix = _footage_tagging_source_prefix()

        # 1) rebuild the S3 static index (pool source of truth)
        def _idx_progress(done: int, total: int) -> None:
            _publish("running", phase="indexing", done=int(done), total=int(total))

        _publish("running", phase="indexing", done=0, total=0)
        if is_photo:
            from scripts.build_photo_assets_index import build_photo_index

            idx = build_photo_index(bucket=bucket, prefix=prefix, out_path=static_index_path, progress_cb=_idx_progress)
        else:
            from scripts.build_static_assets_index import build_index

            idx = build_index(bucket=bucket, prefix=prefix, out_path=static_index_path, progress_cb=_idx_progress)

        # 1b) register the pool into Postgres (durable registry that survives
        # deploys; the JSON index is only a render cache). Non-fatal: a registry
        # hiccup must not block the pipeline that already has the JSON pool.
        pool_registry: Dict[str, Any] = {}
        try:
            _publish("running", phase="registering_pool")
            pool_registry = _register_pool_assets(db_url=db_url, static_index_path=static_index_path, source=mt)
        except Exception as exc:
            log.warning("activate: pool registry upsert failed (non-fatal): %r", exc)
            pool_registry = {"error": str(exc)}

        # 2) rebuild the picker inventory from the static index
        _publish("running", phase="inventory", indexed=idx.get("assets_count"))
        from footage_config import build_inventory_and_bundle

        max_assets_env = (os.environ.get("DESCRIPTIONS_BUNDLE_MAX_ASSETS") or "").strip()
        build_inventory_and_bundle(
            repo_root=repo_root,
            footage_dir=_Path(os.environ.get("FOOTAGE_DIR", str(repo_root / "footage"))),
            static_assets_index_path=static_index_path,
            inventory_out_path=inventory_out,
            bundle_out_path=bundle_out,
            max_assets_in_bundle=int(max_assets_env) if max_assets_env else None,
            media_type=mt,
        )

        # 3) tag untagged clips
        def _tag_progress(done: int, total: int, written: int) -> None:
            _publish("running", phase="tagging", done=int(done), total=int(total), written=int(written))

        _publish("running", phase="tagging", done=0, total=0, written=0)
        if is_photo:
            from mlcore.photo_tagger import run_photo_tagging_batch

            tag_summary = run_photo_tagging_batch(
                bucket=bucket, source_prefix=prefix, db_url=db_url,
                limit=int(limit or 0), progress_cb=_tag_progress,
            )
        else:
            from mlcore.footage_tagger import run_tagging_batch

            tag_summary = run_tagging_batch(
                bucket=bucket, source_prefix=prefix, db_url=db_url,
                limit=int(limit or 0), progress_cb=_tag_progress,
            )

        # 4) export the snapshots the picker reads
        _publish("running", phase="snapshot")
        snap = _export_footage_tags_snapshot(db_url=db_url, source=mt)
        try:
            _export_tag_overrides_snapshot(db_url=db_url)
        except Exception as exc:
            log.warning("activate: tag_overrides export failed: %r", exc)

        summary = {
            "indexed": idx.get("assets_count"),
            "index_failed": idx.get("failed"),
            **tag_summary,
            "snapshot_rows": snap.get("rows"),
            # canonical pool numbers from the Postgres registry (one source of truth):
            "pool_registered": pool_registry.get("total"),
            "pool_pruned": pool_registry.get("pruned"),
            "pool_pickable": pool_registry.get("pickable"),
        }
    except Exception as exc:
        _publish("failed", error=str(exc))
        raise
    _publish("done", **summary)
    return summary


@celery_app.task(name="orchestrator.dispatch_to_windows", bind=True, max_retries=10)
def dispatch_to_windows(self, job_id: str) -> Dict[str, Any]:
    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError("job_not_found")

    pool = WindowsNodePool(
        redis_client=store.r,
        key_prefix=store.key_prefix,
        lease_ttl_s=SETTINGS.windows_node_lease_ttl_s,
    )
    active_urls = pool.get_active_urls(default_urls=_windows_default_urls())
    if not active_urls:
        raise RuntimeError("WINDOWS_RENDER_URL / WINDOWS_RENDER_URLS / runtime pool is not set")

    req = st.request or {}
    audio_url = str(req.get("audio_s3_url") or "").strip()
    if not audio_url:
        raise RuntimeError("missing audio_s3_url in job request (needed for windows media download)")

    # 🔥 строгая проверка — больше никаких “/app/...”
    if not _is_remote_url(audio_url):
        raise RuntimeError(
            "dispatch_to_windows requires remote audio URL (http/https/s3). "
            f"got={audio_url!r}. "
            "This usually means your API client sent a local path, or you are running an old worker container."
        )

    paths = make_job_paths(work_dir=SETTINGS.work_dir, output_dir=SETTINGS.output_dir, job_id=job_id)

    # Preflight: artifacts MUST exist (worker-build and worker-render must share /app/output).
    if not paths.render_jsx.exists() or not paths.render_payload.exists():
        raise RuntimeError(
            "missing_render_artifacts: "
            f"render_jsx_exists={paths.render_jsx.exists()} "
            f"render_payload_exists={paths.render_payload.exists()} "
            f"expected_render_jsx={str(paths.render_jsx)} "
            f"expected_render_payload={str(paths.render_payload)}. "
            "This usually means your containers do not share the same /app/output volume, "
            "or the build stage did not produce these files."
        )

    output_bucket = (os.environ.get("S3_BUCKET_OUTPUT_VIDEO") or "").strip()

    # entry_comp: the footage build renders "Main Render"; the photo build
    # (bg_mode=photo) writes its own payload carrying entry_comp="Photo Render".
    # Read it from the render payload so the node renders the right comp.
    entry_comp = "Main Render"
    try:
        _rp = json.loads(paths.render_payload.read_text(encoding="utf-8"))
        entry_comp = str(_rp.get("entry_comp") or "Main Render")
    except Exception:
        entry_comp = "Main Render"

    win_payload = build_windows_job_payload(
        job_id=job_id,
        render_jsx_path=paths.render_jsx,
        render_payload_path=paths.render_payload,
        audio_url=audio_url,
        entry_comp=entry_comp,
        output_relpath="work/output.mp4",
        output_s3_bucket=output_bucket,
        output_s3_key=f"renders/{job_id}/output.mp4",
    )

    selected_url = ""
    res: Dict[str, Any] | None = None
    errors_by_node: list[str] = []
    remaining = list(active_urls)
    dispatch_started_at = time.time()
    api_mode = str(SETTINGS.windows_render_api_mode or "").strip().lower()
    if api_mode != "render":
        raise RuntimeError(
            "windows_dispatch_contract_mismatch: "
            "orchestrator dispatch now requires async render contract "
            "(set WINDOWS_RENDER_API_MODE=render; /jobs sync dispatch is disabled)"
        )

    while remaining:
        candidate = pool.reserve_best(remaining)
        if not candidate:
            break
        node = _node_label(candidate)
        _inc_labeled_metric(
            store,
            metric="dispatch_attempt_total",
            labels={"node": node, "api_mode": "render", "outcome": "attempt"},
        )
        _obs_event("dispatch_attempt", job_id=job_id, node=node, api_mode="render")

        store.set_status(
            job_id,
            "RUNNING",
            stage="dispatch",
            result={
                "dispatch": {
                    "windows_url": candidate,
                    "audio_url_used": audio_url,
                    "pool_urls": active_urls,
                    "api_mode": SETTINGS.windows_render_api_mode,
                },
                "dispatch_started_at": dispatch_started_at,
            },
        )

        client = WindowsRenderClient(
            candidate,
            timeout_s=SETTINGS.windows_timeout_s,
            api_mode="render",
        )
        try:
            maybe_res = client.dispatch_render(win_payload)
            if not isinstance(maybe_res, dict):
                _inc_labeled_metric(
                    store,
                    metric="dispatch_attempt_total",
                    labels={"node": node, "api_mode": "render", "outcome": "bad_response"},
                )
                _obs_event("dispatch_bad_response", job_id=job_id, node=node, api_mode="render")
                pool.release(candidate)
                raise RuntimeError(f"windows_bad_response: {maybe_res!r}")
            selected_url = candidate
            res = maybe_res
            _reset_dispatch_fail_streak(store, node_url=candidate)
            _inc_labeled_metric(
                store,
                metric="dispatch_attempt_total",
                labels={"node": node, "api_mode": "render", "outcome": "accepted"},
            )
            _obs_event("dispatch_accepted", job_id=job_id, node=node, api_mode="render")
            break
        except Exception as e:
            pool.release(candidate)
            errors_by_node.append(f"{candidate}: {e!r}")
            remaining = [u for u in remaining if u != candidate]
            fail_streak = _inc_dispatch_fail_streak(store, node_url=candidate)

            is_transient = _is_transient_windows_error(e)
            code = 0
            if isinstance(e, urllib.error.HTTPError):
                try:
                    code = int(getattr(e, "code", 0) or 0)
                except Exception:
                    code = 0
            is_contract_404 = code == 404
            err_outcome = "contract_404" if is_contract_404 else ("transient_error" if is_transient else "non_transient_error")
            _inc_labeled_metric(
                store,
                metric="dispatch_attempt_total",
                labels={"node": node, "api_mode": "render", "outcome": err_outcome},
            )
            _obs_event(
                "dispatch_error",
                job_id=job_id,
                node=node,
                api_mode="render",
                outcome=err_outcome,
                err=repr(e),
                fail_streak=fail_streak,
            )

            disable_threshold = max(
                0,
                int(getattr(SETTINGS, "windows_node_disable_after_dispatch_errors", 0) or 0),
            )
            should_disable = False
            disable_reason = ""
            if is_contract_404:
                should_disable = True
                disable_reason = "dispatch_contract_404"
            elif not is_transient:
                should_disable = True
                disable_reason = "dispatch_non_transient_error"
            elif disable_threshold > 0 and fail_streak >= disable_threshold:
                should_disable = True
                disable_reason = f"dispatch_transient_streak_{fail_streak}"
            if should_disable:
                _auto_disable_node(
                    store=store,
                    pool=pool,
                    node_url=candidate,
                    reason=disable_reason,
                    job_id=job_id,
                )

            if not remaining:
                if is_transient or is_contract_404:
                    recovered = _try_recover_dispatch_from_existing_output(
                        store=store,
                        job_id=job_id,
                        errors_by_node=errors_by_node,
                    )
                    if recovered is not None:
                        _observe_stage_duration(
                            store,
                            stage="dispatch",
                            started_at=dispatch_started_at,
                            outcome="recovered",
                        )
                        return recovered
                    attempt = int(getattr(self.request, "retries", 0)) + 1
                    backoff = _retry_backoff_s(attempt=attempt, base_s=5.0, cap_s=120.0)
                    _inc_labeled_metric(
                        store,
                        metric="dispatch_attempt_total",
                        labels={"node": "all_nodes", "api_mode": "render", "outcome": "retry"},
                    )
                    _observe_stage_duration(
                        store,
                        stage="dispatch",
                        started_at=dispatch_started_at,
                        outcome="retry",
                    )
                    _obs_event(
                        "dispatch_retry",
                        job_id=job_id,
                        api_mode="render",
                        attempt=attempt,
                        backoff_s=backoff,
                        errors=len(errors_by_node),
                    )
                    raise self.retry(
                        countdown=backoff,
                        exc=RuntimeError(f"windows_dispatch_transient: all_nodes_failed={errors_by_node!r}"),
                    )
                _inc_labeled_metric(
                    store,
                    metric="dispatch_attempt_total",
                    labels={"node": "all_nodes", "api_mode": "render", "outcome": "failed"},
                )
                _observe_stage_duration(
                    store,
                    stage="dispatch",
                    started_at=dispatch_started_at,
                    outcome="failed",
                )
                _obs_event(
                    "dispatch_failed",
                    job_id=job_id,
                    api_mode="render",
                    errors=len(errors_by_node),
                )
                raise RuntimeError(f"windows_dispatch_failed: all_nodes_failed={errors_by_node!r}") from e

            if not is_transient and not is_contract_404:
                raise

    if not selected_url or res is None:
        _inc_labeled_metric(
            store,
            metric="dispatch_attempt_total",
            labels={"node": "none", "api_mode": "render", "outcome": "failed_no_node"},
        )
        _observe_stage_duration(
            store,
            stage="dispatch",
            started_at=dispatch_started_at,
            outcome="failed",
        )
        raise RuntimeError(f"windows_dispatch_failed: no_node_selected errors={errors_by_node!r}")

    if str(res.get("_api") or "").strip().lower() != "render":
        pool.release(selected_url)
        _inc_labeled_metric(
            store,
            metric="dispatch_attempt_total",
            labels={"node": _node_label(selected_url), "api_mode": "render", "outcome": "contract_mismatch"},
        )
        _observe_stage_duration(
            store,
            stage="dispatch",
            started_at=dispatch_started_at,
            outcome="failed",
        )
        raise RuntimeError(f"windows_dispatch_contract_mismatch: expected async render response, got {res!r}")

    render_id = str(res.get("render_id") or "").strip()
    if not render_id:
        pool.release(selected_url)
        _inc_labeled_metric(
            store,
            metric="dispatch_attempt_total",
            labels={"node": _node_label(selected_url), "api_mode": "render", "outcome": "missing_render_id"},
        )
        _observe_stage_duration(
            store,
            stage="dispatch",
            started_at=dispatch_started_at,
            outcome="failed",
        )
        raise RuntimeError(f"windows_bad_response(no render_id): {res}")
    _observe_stage_duration(
        store,
        stage="dispatch",
        started_at=dispatch_started_at,
        outcome="accepted",
    )
    _obs_event(
        "dispatch_completed",
        job_id=job_id,
        node=_node_label(selected_url),
        api_mode="render",
        render_id=render_id,
    )

    # Start poll timeout clock from HERE (not from build start).
    store.set_status(
        job_id,
        "RUNNING",
        stage="poll",
        result={"render_id": render_id, "windows": res, "poll_started_at": time.time()},
    )
    render_queue = _job_queue_from_request(
        req,
        key="render_poll_queue",
        default=SETTINGS.celery_queue_render_poll,
    )
    kwargs: Dict[str, Any] = {
        "args": [job_id, render_id],
        "countdown": float(SETTINGS.windows_poll_interval_s),
    }
    if render_queue:
        kwargs["queue"] = render_queue
    poll_windows_render.apply_async(**kwargs)
    return {"ok": True, "mode": "async_render", "render_id": render_id, "windows": res}


@celery_app.task(name="orchestrator.poll_windows_render", bind=True, max_retries=50)
def poll_windows_render(self, job_id: str, render_id: str) -> Dict[str, Any]:
    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError("job_not_found")

    pool = WindowsNodePool(
        redis_client=store.r,
        key_prefix=store.key_prefix,
        lease_ttl_s=SETTINGS.windows_node_lease_ttl_s,
    )
    active_urls = pool.get_active_urls(default_urls=_windows_default_urls())

    # Use the render endpoint pinned at dispatch time so in-flight polls
    # survive runtime pool updates / node switchovers.
    pinned_url = ""
    if isinstance(st.result, dict):
        dispatch_info = st.result.get("dispatch")
        if isinstance(dispatch_info, dict):
            pinned_url = str(dispatch_info.get("windows_url") or "").strip()
    windows_url = pinned_url or (active_urls[0] if active_urls else "")
    if not windows_url:
        raise RuntimeError("no pinned windows endpoint in job and runtime pool is empty")
    node = _node_label(windows_url)

    client = WindowsRenderClient(
        windows_url,
        timeout_s=SETTINGS.windows_timeout_s,
        api_mode="render",
    )

    started_at = _poll_started_at_from_state(st)
    now = time.time()
    if (now - started_at) > float(SETTINGS.windows_poll_timeout_s):
        _inc_metric(store, metric="render_poll_timeout_outcomes", label="before_poll")
        _inc_labeled_metric(store, metric="render_poll_timeout_total", labels={"phase": "before_poll"})
        _inc_labeled_metric(
            store,
            metric="render_poll_total",
            labels={"node": node, "outcome": "timeout_before_poll"},
        )
        _observe_stage_duration(store, stage="poll", started_at=started_at, outcome="timeout")
        _obs_event("poll_timeout", job_id=job_id, node=node, phase="before_poll", render_id=render_id)
        if bool(getattr(SETTINGS, "windows_node_disable_on_poll_timeout", True)):
            _auto_disable_node(
                store=store,
                pool=pool,
                node_url=windows_url,
                reason="poll_timeout_before_poll",
                job_id=job_id,
                render_id=render_id,
            )
        raise RuntimeError(f"windows_poll_timeout render_id={render_id}")

    try:
        res = client.get_render_status(render_id)
    except Exception as e:
        _inc_labeled_metric(
            store,
            metric="render_poll_total",
            labels={"node": node, "outcome": "transient_error" if _is_transient_windows_error(e) else "error"},
        )
        if _is_transient_windows_error(e):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            remaining = float(SETTINGS.windows_poll_timeout_s) - (time.time() - started_at)
            if remaining <= 0:
                _inc_metric(
                    store,
                    metric="render_poll_timeout_outcomes",
                    label="during_status_retry",
                )
                _inc_labeled_metric(
                    store,
                    metric="render_poll_timeout_total",
                    labels={"phase": "during_status_retry"},
                )
                _observe_stage_duration(store, stage="poll", started_at=started_at, outcome="timeout")
                _obs_event(
                    "poll_timeout",
                    job_id=job_id,
                    node=node,
                    phase="during_status_retry",
                    render_id=render_id,
                )
                if bool(getattr(SETTINGS, "windows_node_disable_on_poll_timeout", True)):
                    _auto_disable_node(
                        store=store,
                        pool=pool,
                        node_url=windows_url,
                        reason="poll_timeout_during_status_retry",
                        job_id=job_id,
                        render_id=render_id,
                    )
                raise RuntimeError(f"windows_poll_timeout(render_status) render_id={render_id}") from e
            backoff = _retry_backoff_s(attempt=attempt, base_s=2.0, cap_s=30.0)
            backoff = min(backoff, max(1.0, remaining))
            _obs_event(
                "poll_retry",
                job_id=job_id,
                node=node,
                attempt=attempt,
                backoff_s=backoff,
                render_id=render_id,
            )
            raise self.retry(countdown=backoff, exc=RuntimeError(f"windows_poll_transient: {e!r}"))
        pool.release(windows_url)
        raise
    if not isinstance(res, dict):
        pool.release(windows_url)
        _inc_labeled_metric(
            store,
            metric="render_poll_total",
            labels={"node": node, "outcome": "bad_response"},
        )
        raise RuntimeError(f"windows_poll_bad_response: {res!r}")

    status = str(res.get("status") or "").lower()

    if status in {"succeeded", "success", "done", "ok"}:
        out_url = res.get("output_url") or res.get("output_s3_url") or None
        artifacts_url = _extract_artifacts_source(res) or None
        result_payload: Dict[str, Any] = {"render_id": render_id, "windows": res, "output_url": out_url}
        if artifacts_url:
            result_payload["project_archive_url"] = artifacts_url
        store.set_status(job_id, "SUCCEEDED", stage="render", result=result_payload)
        _inc_labeled_metric(
            store,
            metric="render_poll_total",
            labels={"node": node, "outcome": "succeeded"},
        )
        _observe_stage_duration(store, stage="poll", started_at=started_at, outcome="succeeded")
        _observe_stage_duration(store, stage="render", started_at=started_at, outcome="succeeded")
        _obs_event("render_outcome", job_id=job_id, node=node, outcome="succeeded", render_id=render_id)
        pool.release(windows_url)
        return {"ok": True, "status": "succeeded", "windows": res}

    if status in {"failed", "error"}:
        pool.release(windows_url)
        _inc_labeled_metric(
            store,
            metric="render_poll_total",
            labels={"node": node, "outcome": "failed"},
        )
        _observe_stage_duration(store, stage="poll", started_at=started_at, outcome="failed")
        _observe_stage_duration(store, stage="render", started_at=started_at, outcome="failed")
        _obs_event("render_outcome", job_id=job_id, node=node, outcome="failed", render_id=render_id)
        raise RuntimeError(f"windows_failed(async_render): {res}")

    _inc_labeled_metric(
        store,
        metric="render_poll_total",
        labels={"node": node, "outcome": "running"},
    )
    req = st.request if isinstance(st.request, dict) else {}
    render_queue = _job_queue_from_request(
        req,
        key="render_poll_queue",
        default=SETTINGS.celery_queue_render_poll,
    )
    kwargs: Dict[str, Any] = {
        "args": [job_id, render_id],
        "countdown": float(SETTINGS.windows_poll_interval_s),
    }
    if render_queue:
        kwargs["queue"] = render_queue
    poll_windows_render.apply_async(**kwargs)
    store.set_status(job_id, "RUNNING", stage="poll", result={"render_id": render_id, "windows": res})
    return {"ok": True, "status": "running", "windows": res}


@celery_app.task(name="orchestrator.dispatch_to_rust_gen", bind=True, max_retries=10)
def dispatch_to_rust_gen(self, job_id: str) -> Dict[str, Any]:
    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError("job_not_found")

    req = st.request if isinstance(st.request, dict) else {}
    if _requested_render_engine(req) != "rust-gen":
        raise RuntimeError("rust_gen_dispatch_requires_render_engine=rust-gen")
    _ensure_rust_gen_route(req)

    audio_url = str(req.get("audio_s3_url") or "").strip()
    if not audio_url or not _is_remote_url(audio_url):
        raise RuntimeError("dispatch_to_rust_gen requires remote audio_s3_url (http/https/s3)")

    paths = make_job_paths(work_dir=SETTINGS.work_dir, output_dir=SETTINGS.output_dir, job_id=job_id)
    if not paths.render_payload.exists():
        raise RuntimeError(
            "missing_render_payload: "
            f"expected_render_payload={str(paths.render_payload)}. "
            "Build and render workers must share the output volume."
        )

    output_bucket = (os.environ.get("S3_BUCKET_OUTPUT_VIDEO") or "").strip()
    dispatch_started_at = time.time()
    mode = str(req.get("subtitles_mode") or "unknown").strip().lower() or "unknown"
    labels = {"engine": "rust_gen", "subtitle_mode": mode}
    _inc_labeled_metric(store, metric="rust_gen_dispatch_total", labels={**labels, "outcome": "attempt"})
    _obs_event("rust_gen_dispatch_attempt", job_id=job_id, subtitle_mode=mode)

    payload = build_rust_gen_job_payload(
        job_id=job_id,
        render_payload_path=paths.render_payload,
        audio_url=audio_url,
        output_s3_bucket=output_bucket,
        presign_ttl_s=int(getattr(SETTINGS, "rust_gen_presign_ttl_s", 7200) or 7200),
    )
    client = RustGenClient(
        str(SETTINGS.rust_gen_manager_url),
        token=str(getattr(SETTINGS, "rust_gen_manager_token", "") or ""),
        timeout_s=float(getattr(SETTINGS, "rust_gen_timeout_s", 30.0) or 30.0),
    )
    try:
        res = client.dispatch_render(payload)
    except Exception as exc:
        _inc_labeled_metric(store, metric="rust_gen_dispatch_total", labels={**labels, "outcome": "error"})
        _observe_stage_duration(store, stage="rust_gen_dispatch", started_at=dispatch_started_at, outcome="failed")
        _obs_event("rust_gen_dispatch_error", job_id=job_id, err=repr(exc))
        raise

    render_id = str(res.get("render_id") or "").strip()
    status = str(res.get("status") or "").strip().lower()
    if not render_id or status not in {"accepted", "queued", "running"}:
        _inc_labeled_metric(store, metric="rust_gen_dispatch_total", labels={**labels, "outcome": "bad_response"})
        _observe_stage_duration(store, stage="rust_gen_dispatch", started_at=dispatch_started_at, outcome="failed")
        raise RuntimeError(f"rust_gen_bad_response: {res!r}")

    _inc_labeled_metric(store, metric="rust_gen_dispatch_total", labels={**labels, "outcome": "accepted"})
    _observe_stage_duration(store, stage="rust_gen_dispatch", started_at=dispatch_started_at, outcome="accepted")
    store.set_status(
        job_id,
        "RUNNING",
        stage="poll",
        result={
            "render_id": render_id,
            "rust_gen": res,
            "dispatch": {"engine": "rust-gen", "manager_url": str(SETTINGS.rust_gen_manager_url)},
            "poll_started_at": time.time(),
        },
    )
    poll_queue = _job_queue_from_request(req, key="render_poll_queue", default=SETTINGS.celery_queue_render_poll)
    kwargs: Dict[str, Any] = {
        "args": [job_id, render_id],
        "countdown": float(getattr(SETTINGS, "rust_gen_poll_interval_s", 2.0) or 2.0),
    }
    if poll_queue:
        kwargs["queue"] = poll_queue
    poll_rust_gen_render.apply_async(**kwargs)
    _obs_event("rust_gen_dispatch_accepted", job_id=job_id, render_id=render_id)
    return {"ok": True, "mode": "rust-gen", "render_id": render_id, "rust_gen": res}


@celery_app.task(name="orchestrator.poll_rust_gen_render", bind=True, max_retries=50)
def poll_rust_gen_render(self, job_id: str, render_id: str) -> Dict[str, Any]:
    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError("job_not_found")
    req = st.request if isinstance(st.request, dict) else {}
    if _requested_render_engine(req) != "rust-gen":
        raise RuntimeError("rust_gen_poll_requires_render_engine=rust-gen")
    _ensure_rust_gen_route(req)

    started_at = _poll_started_at_from_state(st)
    timeout_s = float(getattr(SETTINGS, "rust_gen_poll_timeout_s", 3600.0) or 3600.0)
    mode = str(req.get("subtitles_mode") or "unknown").strip().lower() or "unknown"
    labels = {"engine": "rust_gen", "subtitle_mode": mode}
    if time.time() - started_at > timeout_s:
        _inc_labeled_metric(store, metric="rust_gen_poll_total", labels={**labels, "outcome": "timeout"})
        _observe_stage_duration(store, stage="rust_gen_poll", started_at=started_at, outcome="timeout")
        raise RuntimeError(f"rust_gen_poll_timeout render_id={render_id}")

    client = RustGenClient(
        str(SETTINGS.rust_gen_manager_url),
        token=str(getattr(SETTINGS, "rust_gen_manager_token", "") or ""),
        timeout_s=float(getattr(SETTINGS, "rust_gen_timeout_s", 30.0) or 30.0),
    )
    try:
        res = client.get_render_status(render_id)
    except Exception as exc:
        _inc_labeled_metric(store, metric="rust_gen_poll_total", labels={**labels, "outcome": "error"})
        raise self.retry(
            countdown=min(30.0, float(getattr(SETTINGS, "rust_gen_poll_interval_s", 2.0) or 2.0) * 2.0),
            exc=RuntimeError(f"rust_gen_poll_error: {exc!r}"),
        )

    status = str(res.get("status") or "").strip().lower()
    job = res.get("job") if isinstance(res.get("job"), dict) else {}
    artifact_refs = job.get("artifact_refs") if isinstance(job, dict) else {}
    artifact_refs = artifact_refs if isinstance(artifact_refs, dict) else {}
    if status in {"succeeded", "success", "done", "ok"}:
        out_url = str(artifact_refs.get("video") or "").strip() or None
        result: Dict[str, Any] = {"render_id": render_id, "rust_gen": res, "output_url": out_url}
        manifest = str(artifact_refs.get("manifest") or "").strip()
        if manifest:
            result["output_manifest_url"] = manifest
        store.set_status(job_id, "SUCCEEDED", stage="render", result=result)
        _inc_labeled_metric(store, metric="rust_gen_poll_total", labels={**labels, "outcome": "succeeded"})
        _observe_stage_duration(store, stage="rust_gen_poll", started_at=started_at, outcome="succeeded")
        _obs_event("rust_gen_render_outcome", job_id=job_id, render_id=render_id, outcome="succeeded")
        return {"ok": True, "status": "succeeded", "rust_gen": res}
    if status in {"failed", "error", "cancelled", "canceled"}:
        _inc_labeled_metric(store, metric="rust_gen_poll_total", labels={**labels, "outcome": "failed"})
        _observe_stage_duration(store, stage="rust_gen_poll", started_at=started_at, outcome="failed")
        raise RuntimeError(f"rust_gen_failed: {res!r}")

    _inc_labeled_metric(store, metric="rust_gen_poll_total", labels={**labels, "outcome": "running"})
    poll_queue = _job_queue_from_request(req, key="render_poll_queue", default=SETTINGS.celery_queue_render_poll)
    kwargs: Dict[str, Any] = {
        "args": [job_id, render_id],
        "countdown": float(getattr(SETTINGS, "rust_gen_poll_interval_s", 2.0) or 2.0),
    }
    if poll_queue:
        kwargs["queue"] = poll_queue
    poll_rust_gen_render.apply_async(**kwargs)
    store.set_status(job_id, "RUNNING", stage="poll", result={"render_id": render_id, "rust_gen": res})
    return {"ok": True, "status": "running", "rust_gen": res}
