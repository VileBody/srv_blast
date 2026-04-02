# services/orchestrator/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _abs_path(p: str, *, repo_root: Path) -> str:
    s = (p or "").strip()
    if not s:
        return ""
    pp = Path(s).expanduser()
    if not pp.is_absolute():
        pp = (repo_root / pp).resolve()
    return str(pp.resolve())


REPO_ROOT = Path(__file__).resolve().parents[2]  # .../services/orchestrator -> repo root


@dataclass(frozen=True)
class Settings:
    # Redis (managed)
    redis_host: str = _env("REDIS_HOST", "localhost")
    redis_port: int = int(_env("REDIS_PORT", "6379") or "6379")
    redis_username: str = _env("REDIS_USERNAME", "")
    redis_password: str = _env("REDIS_PASSWORD", "")

    # Celery
    celery_broker_url: str = _env("CELERY_BROKER_URL", "")
    celery_result_backend: str = _env("CELERY_RESULT_BACKEND", "")

    # Two queues
    celery_queue_build: str = _env("CELERY_QUEUE_BUILD", "build")
    celery_queue_render: str = _env("CELERY_QUEUE_RENDER", "render")

    # Paths inside container / host
    work_dir: str = _env("WORK_DIR", "/app/work")
    output_dir: str = _env("OUTPUT_DIR", "/app/output")

    # Global shared storage (one for all jobs)
    pins_dir: str = _env("PINS_DIR", "/app/pins")

    # Shared catalog
    footage_inventory_json: str = _abs_path(
        _env("FOOTAGE_INVENTORY_JSON", "data/footage_inventory.json"),
        repo_root=REPO_ROOT,
    )
    descriptions_bundle_path: str = _abs_path(
        _env("DESCRIPTIONS_BUNDLE_PATH", "pins/descriptions_bundle.json"),
        repo_root=REPO_ROOT,
    )
    descriptions_bundle_max_assets: str = _env("DESCRIPTIONS_BUNDLE_MAX_ASSETS", "")

    # Pipeline entrypoint (repo-local)
    pipeline_cmd: str = _env("PIPELINE_CMD", "python run.py")

    # Feature flags
    debug_save_llm: bool = _env("DEBUG_SAVE_LLM", "0") not in {"0", "false", "False", "no", "NO"}

    # Windows render node
    windows_base_url: str = _env("WINDOWS_RENDER_URL", "")  # e.g. http://win-node:8000
    windows_timeout_s: float = float(_env("WINDOWS_TIMEOUT_S", "30") or "30")

    # Polling controls (for async win API)
    windows_poll_interval_s: float = float(_env("WINDOWS_POLL_INTERVAL_S", "2.0") or "2.0")
    windows_poll_timeout_s: float = float(_env("WINDOWS_POLL_TIMEOUT_S", "3600") or "3600")

    # Job artifact cleanup
    job_artifact_max_age_h: int = int(_env("JOB_ARTIFACT_MAX_AGE_H", "72") or "72")  # hours
    job_artifact_cleanup_enabled: bool = _env("JOB_ARTIFACT_CLEANUP_ENABLED", "0") not in {"0", "false", "False", "no", "NO"}


SETTINGS = Settings()
