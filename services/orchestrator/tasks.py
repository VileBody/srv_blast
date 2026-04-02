# services/orchestrator/tasks.py
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
import urllib.error
from urllib.parse import unquote
from pathlib import Path
from typing import Any, Dict, List, Optional
import boto3
from botocore.config import Config

from .artifacts import make_job_paths
from .celery_app import celery_app
from .config import SETTINGS
from .job_store import JobStore
from .render_manifest import build_windows_job_payload
from .windows_client import WindowsRenderClient
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


def _is_remote_url(u: str) -> bool:
    s = (u or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


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
        "config": Config(signature_version="s3v4"),
    }
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    return boto3.client(**kwargs)


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
    return ("openrouter" in lo) and ("429" in lo) and (
        "rate limit" in lo or "too many requests" in lo
    )


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


def _seed_resume_state_from_source_job(
    *,
    work_dir: str,
    source_job_id: str,
    target_resume_state_path: Path,
) -> None:
    src_job = str(source_job_id or "").strip()
    if not src_job:
        raise RuntimeError("reuse_text_job_id is empty")

    src_path = _job_resume_state_path(work_dir=work_dir, job_id=src_job)
    if not src_path.exists():
        raise RuntimeError(f"reuse_text_source_resume_missing source_job_id={src_job!r} path={src_path}")

    try:
        src_obj = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"reuse_text_source_resume_unreadable source_job_id={src_job!r} err={e!r}") from e
    if not isinstance(src_obj, dict):
        raise RuntimeError(f"reuse_text_source_resume_invalid source_job_id={src_job!r} expected JSON object")

    missing = [k for k in _REUSE_RESUME_STATE_KEYS if k not in src_obj]
    if missing:
        raise RuntimeError(
            "reuse_text_source_resume_missing_keys "
            f"source_job_id={src_job!r} missing={missing!r}"
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
        dst_obj[k] = src_obj[k]
    # Force footage/style re-selection for variant diversification.
    dst_obj.pop("stage2_style", None)
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


@celery_app.task(name="orchestrator.build_job", bind=True, max_retries=8)
def build_job(self, job_id: str) -> Dict[str, Any]:
    if get_runtime_mode() != MODE_PROD:
        raise RuntimeError("Celery build_job is allowed only in MODE=prod")

    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError(f"job not found: {job_id}")

    store.set_status(job_id, "RUNNING", stage="build")

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
    audio_url = str(req.get("audio_s3_url") or "").strip()
    project_id = str(req.get("project_id") or "").strip()
    lyrics_text = str(req.get("lyrics_text") or "")
    target_fragment = str(req.get("target_fragment") or "")
    reuse_text_job_id = str(req.get("reuse_text_job_id") or "").strip()
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
    if not audio_url:
        raise RuntimeError("missing audio_s3_url")
    if not _is_remote_url(audio_url):
        # Вот тут “строгость”: не позволяем запускать пайплайн с локальным путём
        raise RuntimeError(f"audio_s3_url must be remote (http/https/s3). got={audio_url!r}")

    audio_name_raw = (audio_url.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()
    audio_name = (unquote(audio_name_raw) or audio_name_raw).strip()
    local_audio = paths.data_dir / "inputs" / "audio" / audio_name
    _download(audio_url, local_audio, timeout_s=600.0)

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

    # make pipeline use THIS job audio
    env["AUDIO_FILE_PATH"] = str(local_audio)
    env["AUDIO_DIR"] = str(local_audio.parent)
    # Keep the final AE audio file_name deterministic and filesystem-safe on Windows.
    audio_ext = (Path(audio_name).suffix or Path(audio_name_raw).suffix or ".mp3").lower()
    if not audio_ext.startswith("."):
        audio_ext = f".{audio_ext}"
    env["AUDIO_FILE_NAME"] = f"audio_source{audio_ext}"

    env["AE_MEDIA_MODE"] = "appdir"
    env["LYRICS_TEXT"] = lyrics_text
    env["TARGET_FRAGMENT"] = target_fragment
    env["SUBTITLES_MODE"] = subtitles_mode
    env["FOOTAGE_EXCLUDE_FILE_NAMES_JSON"] = json.dumps(exclude_file_names, ensure_ascii=False)
    seed_variant = variant_index if variant_index is not None else 1
    seed_base = project_id or f"job-{job_id}"
    env["STAGE2_SELECTION_SEED"] = f"{seed_base}:v{seed_variant}"
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
        for k in (
            "DATA_DIR",
            "OUT_DIR",
            "AUDIO_FILE_PATH",
            "AUDIO_DIR",
            "AUDIO_FILE_NAME",
            "AE_MEDIA_MODE",
            "JOB_ID",
            "LYRICS_TEXT",
            "TARGET_FRAGMENT",
            "SUBTITLES_MODE",
            "FOOTAGE_EXCLUDE_FILE_NAMES_JSON",
            "STAGE2_SELECTION_SEED",
            "BATCH_VARIANT_INDEX",
            "BATCH_VARIANTS_TOTAL",
            "REUSE_TEXT_JOB_ID",
        ):
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
                )
            build_all_fn(
                progress_cb=lambda stage: store.set_status(job_id, "RUNNING", stage=str(stage)),
                resume_state_path=llm_resume_state_path,
            )
        except Exception as e:
            text = _exc_text(e)
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
            if _looks_like_openrouter_overloaded_503(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _overloaded_retry_backoff_s(attempt=attempt)
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_overloaded_503"))
            if _looks_like_openrouter_rate_limited_429(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_rate_limited_429"))
            raise
        finally:
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
        if _looks_like_gemini_internal_500(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("gemini_internal_500"))
        if _looks_like_openrouter_timeout(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_timeout"))
        if _looks_like_openrouter_overloaded_503(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _overloaded_retry_backoff_s(attempt=attempt)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_overloaded_503"))
        if _looks_like_openrouter_rate_limited_429(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_rate_limited_429"))

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
                retry_hint = _build_stage2_subtitles_retry_hint(blob_first)
                llm_env_keys = (
                    "DATA_DIR",
                    "OUT_DIR",
                    "AUDIO_FILE_PATH",
                    "AUDIO_DIR",
                    "AUDIO_FILE_NAME",
                    "AE_MEDIA_MODE",
                    "JOB_ID",
                    "LYRICS_TEXT",
                    "TARGET_FRAGMENT",
                    "SUBTITLES_MODE",
                    "FOOTAGE_EXCLUDE_FILE_NAMES_JSON",
                    "STAGE2_SELECTION_SEED",
                    "BATCH_VARIANT_INDEX",
                    "BATCH_VARIANTS_TOTAL",
                    "REUSE_TEXT_JOB_ID",
                )
                llm_backup: Dict[str, str | None] = {}
                for k in llm_env_keys:
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
                    except Exception as e:
                        text = _exc_text(e)
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
                        if _looks_like_openrouter_overloaded_503(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _overloaded_retry_backoff_s(attempt=attempt)
                            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_overloaded_503"))
                        if _looks_like_openrouter_rate_limited_429(text):
                            attempt = int(getattr(self.request, "retries", 0)) + 1
                            backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                            raise self.retry(countdown=backoff, exc=RuntimeError("openrouter_rate_limited_429"))
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

    # Hard contract: build must emit artifacts for Windows dispatch.
    if not paths.render_jsx.exists() or not paths.render_payload.exists():
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
    dispatch_to_windows.delay(job_id)
    return {"ok": True, "stage": "build_done", "paths": paths.manifest()}


@celery_app.task(name="orchestrator.dispatch_to_windows", bind=True, max_retries=10)
def dispatch_to_windows(self, job_id: str) -> Dict[str, Any]:
    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError("job_not_found")

    if not SETTINGS.windows_base_url:
        raise RuntimeError("WINDOWS_RENDER_URL is not set")

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

    win_payload = build_windows_job_payload(
        job_id=job_id,
        render_jsx_path=paths.render_jsx,
        render_payload_path=paths.render_payload,
        audio_url=audio_url,
        entry_comp="Main Render",
        output_relpath="work/output.mp4",
        output_s3_bucket=os.environ.get("S3_BUCKET_OUTPUT_VIDEO", ""),
        output_s3_key=f"renders/{job_id}/output.mp4",
    )

    store.set_status(
        job_id,
        "RUNNING",
        stage="dispatch",
        result={
            "dispatch": {"windows_url": SETTINGS.windows_base_url, "audio_url_used": audio_url},
            "dispatch_started_at": time.time(),
        },
    )

    client = WindowsRenderClient(SETTINGS.windows_base_url, timeout_s=SETTINGS.windows_timeout_s)
    try:
        res = client.dispatch_render(win_payload)
    except Exception as e:
        if _is_transient_windows_error(e):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=5.0, cap_s=120.0)
            raise self.retry(countdown=backoff, exc=RuntimeError(f"windows_dispatch_transient: {e!r}"))
        raise

    if isinstance(res, dict) and res.get("_api") == "jobs":
        ok = bool(res.get("success", False))
        if ok:
            out_url = res.get("output_url") or res.get("output_s3_url") or None
            artifacts_url = _extract_artifacts_source(res) or None
            result_payload: Dict[str, Any] = {"windows": res, "output_url": out_url}
            if artifacts_url:
                result_payload["project_archive_url"] = artifacts_url
            store.set_status(job_id, "SUCCEEDED", stage="render", result=result_payload)
            return {"ok": True, "mode": "sync_jobs", "windows": res}
        raise RuntimeError(f"windows_failed(sync_jobs): {res}")

    if not isinstance(res, dict):
        raise RuntimeError(f"windows_bad_response: {res!r}")

    render_id = str(res.get("render_id") or "").strip()
    if not render_id:
        raise RuntimeError(f"windows_bad_response(no render_id): {res}")

    # Start poll timeout clock from HERE (not from build start).
    store.set_status(
        job_id,
        "RUNNING",
        stage="poll",
        result={"render_id": render_id, "windows": res, "poll_started_at": time.time()},
    )
    poll_windows_render.apply_async(args=[job_id, render_id], countdown=float(SETTINGS.windows_poll_interval_s))
    return {"ok": True, "mode": "async_render", "render_id": render_id, "windows": res}


@celery_app.task(name="orchestrator.poll_windows_render", bind=True, max_retries=50)
def poll_windows_render(self, job_id: str, render_id: str) -> Dict[str, Any]:
    store = JobStore.from_env()
    st = store.get(job_id)
    if not st:
        raise RuntimeError("job_not_found")

    # Use the render endpoint pinned at dispatch time so in-flight polls
    # survive a WINDOWS_RENDER_URL switchover/rollback.
    pinned_url = ""
    if isinstance(st.result, dict):
        dispatch_info = st.result.get("dispatch")
        if isinstance(dispatch_info, dict):
            pinned_url = str(dispatch_info.get("windows_url") or "").strip()
    windows_url = pinned_url or SETTINGS.windows_base_url
    if not windows_url:
        raise RuntimeError("WINDOWS_RENDER_URL is not set and no pinned endpoint in job")

    client = WindowsRenderClient(windows_url, timeout_s=SETTINGS.windows_timeout_s)

    started_at = _poll_started_at_from_state(st)
    now = time.time()
    if (now - started_at) > float(SETTINGS.windows_poll_timeout_s):
        raise RuntimeError(f"windows_poll_timeout render_id={render_id}")

    try:
        res = client.get_render_status(render_id)
    except Exception as e:
        if _is_transient_windows_error(e):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            remaining = float(SETTINGS.windows_poll_timeout_s) - (time.time() - started_at)
            if remaining <= 0:
                raise RuntimeError(f"windows_poll_timeout(render_status) render_id={render_id}") from e
            backoff = _retry_backoff_s(attempt=attempt, base_s=2.0, cap_s=30.0)
            backoff = min(backoff, max(1.0, remaining))
            raise self.retry(countdown=backoff, exc=RuntimeError(f"windows_poll_transient: {e!r}"))
        raise
    if not isinstance(res, dict):
        raise RuntimeError(f"windows_poll_bad_response: {res!r}")

    status = str(res.get("status") or "").lower()

    if status in {"succeeded", "success", "done", "ok"}:
        out_url = res.get("output_url") or res.get("output_s3_url") or None
        artifacts_url = _extract_artifacts_source(res) or None
        result_payload: Dict[str, Any] = {"render_id": render_id, "windows": res, "output_url": out_url}
        if artifacts_url:
            result_payload["project_archive_url"] = artifacts_url
        store.set_status(job_id, "SUCCEEDED", stage="render", result=result_payload)
        return {"ok": True, "status": "succeeded", "windows": res}

    if status in {"failed", "error"}:
        raise RuntimeError(f"windows_failed(async_render): {res}")

    poll_windows_render.apply_async(args=[job_id, render_id], countdown=float(SETTINGS.windows_poll_interval_s))
    store.set_status(job_id, "RUNNING", stage="poll", result={"render_id": render_id, "windows": res})
    return {"ok": True, "status": "running", "windows": res}
