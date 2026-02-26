# services/orchestrator/tasks.py
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict

from .artifacts import make_job_paths
from .celery_app import celery_app
from .config import SETTINGS
from .job_store import JobStore
from .render_manifest import build_windows_job_payload
from .windows_client import WindowsRenderClient
from core.runtime_mode import MODE_PROD, get_runtime_mode


def _is_remote_url(u: str) -> bool:
    s = (u or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


def _download(url: str, dest: Path, *, timeout_s: float = 300.0) -> None:
    # NOTE: сюда приходит presigned https (или другой http). Локальные пути — ошибка выше по стеку.
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=float(timeout_s)) as resp:
        data = resp.read()
    dest.write_bytes(data)


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

    audio_name = (audio_url.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()

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

    req = st.request or {}
    audio_url = str(req.get("audio_s3_url") or "").strip()
    if not audio_url:
        raise RuntimeError("missing audio_s3_url")
    if not _is_remote_url(audio_url):
        # Вот тут “строгость”: не позволяем запускать пайплайн с локальным путём
        raise RuntimeError(f"audio_s3_url must be remote (http/https/s3). got={audio_url!r}")

    audio_name = (audio_url.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()
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

    env["AE_MEDIA_MODE"] = "appdir"

    if mode != "no_gemini":
        from mlcore.gemini_orchestrator import build_all_via_gemini_one_call

        store.set_status(job_id, "RUNNING", stage="llm_stage1")
        backup: Dict[str, str | None] = {}
        for k in ("DATA_DIR", "OUT_DIR", "AUDIO_FILE_PATH", "AUDIO_DIR", "AE_MEDIA_MODE", "JOB_ID"):
            backup[k] = os.environ.get(k)
            os.environ[k] = env[k]

        try:
            build_all_via_gemini_one_call(
                progress_cb=lambda stage: store.set_status(job_id, "RUNNING", stage=str(stage))
            )
        except Exception as e:
            text = _exc_text(e)
            if _looks_like_gemini_internal_500(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("gemini_internal_500"))
            if _looks_like_gemini_overloaded_503(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=30.0, cap_s=900.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("gemini_overloaded_503"))
            if _looks_like_gemini_rate_limited_429(text):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                backoff = _retry_backoff_s(attempt=attempt, base_s=15.0, cap_s=600.0)
                raise self.retry(countdown=backoff, exc=RuntimeError("gemini_rate_limited_429"))
            raise
        finally:
            for k, old in backup.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    store.set_status(job_id, "RUNNING", stage="build")

    args = shlex.split(build_cmd)
    proc = subprocess.run(args, cwd=str(repo_root), env=env, capture_output=True, text=True)
    out = proc.stdout or ""
    err = proc.stderr or ""

    if proc.returncode != 0:
        blob = out + "\n" + err
        if _looks_like_gemini_internal_500(blob):
            attempt = int(getattr(self.request, "retries", 0)) + 1
            backoff = _retry_backoff_s(attempt=attempt, base_s=10.0, cap_s=300.0)
            raise self.retry(countdown=backoff, exc=RuntimeError("gemini_internal_500"))

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
            store.set_status(job_id, "SUCCEEDED", stage="render", result={"windows": res, "output_url": out_url})
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

    if not SETTINGS.windows_base_url:
        raise RuntimeError("WINDOWS_RENDER_URL is not set")

    client = WindowsRenderClient(SETTINGS.windows_base_url, timeout_s=SETTINGS.windows_timeout_s)

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
        store.set_status(job_id, "SUCCEEDED", stage="render", result={"render_id": render_id, "windows": res, "output_url": out_url})
        return {"ok": True, "status": "succeeded", "windows": res}

    if status in {"failed", "error"}:
        raise RuntimeError(f"windows_failed(async_render): {res}")

    poll_windows_render.apply_async(args=[job_id, render_id], countdown=float(SETTINGS.windows_poll_interval_s))
    store.set_status(job_id, "RUNNING", stage="poll", result={"render_id": render_id, "windows": res})
    return {"ok": True, "status": "running", "windows": res}
