# services/orchestrator/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _int_env(key: str, default: int) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _windows_render_api_mode_env() -> str:
    # Async render contract is the default and production baseline.
    raw = _env("WINDOWS_RENDER_API_MODE", "render").lower()
    if raw not in {"render", "jobs"}:
        raise ValueError(
            "WINDOWS_RENDER_API_MODE must be one of: render, jobs "
            f"(got {raw!r})"
        )
    return raw


def _credits_db_url_env() -> str:
    explicit = _env("CREDITS_DB_URL", "")
    if explicit:
        return explicit
    host = _env("POSTGRES_HOST", "")
    db = _env("POSTGRES_DB", "")
    user = _env("POSTGRES_USER", "")
    password = _env("POSTGRES_PASSWORD", "")
    sslmode = _env("POSTGRES_SSLMODE", "prefer")
    port = _int_env("POSTGRES_PORT", 5432)
    if not host or not db or not user:
        return ""
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{int(port)}/{db}"
        f"?sslmode={quote_plus(sslmode or 'prefer')}"
    )


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
    windows_base_urls_csv: str = _env("WINDOWS_RENDER_URLS", "")  # comma-separated
    windows_render_api_mode: str = _windows_render_api_mode_env()
    windows_timeout_s: float = float(_env("WINDOWS_TIMEOUT_S", "30") or "30")
    windows_node_lease_ttl_s: int = int(_env("WINDOWS_NODE_LEASE_TTL_S", "7200") or "7200")
    windows_node_disable_after_dispatch_errors: int = int(
        _env("WINDOWS_NODE_DISABLE_AFTER_DISPATCH_ERRORS", "3") or "3"
    )
    windows_node_disable_dispatch_streak_ttl_s: int = int(
        _env("WINDOWS_NODE_DISABLE_DISPATCH_STREAK_TTL_S", "1800") or "1800"
    )
    windows_node_disable_on_poll_timeout: bool = _env("WINDOWS_NODE_DISABLE_ON_POLL_TIMEOUT", "1") not in {
        "0",
        "false",
        "False",
        "no",
        "NO",
    }

    # Polling controls (for async win API)
    windows_poll_interval_s: float = float(_env("WINDOWS_POLL_INTERVAL_S", "2.0") or "2.0")
    windows_poll_timeout_s: float = float(_env("WINDOWS_POLL_TIMEOUT_S", "3600") or "3600")

    # Job artifact cleanup
    job_artifact_max_age_h: int = int(_env("JOB_ARTIFACT_MAX_AGE_H", "72") or "72")  # hours
    job_artifact_cleanup_enabled: bool = _env("JOB_ARTIFACT_CLEANUP_ENABLED", "0") not in {"0", "false", "False", "no", "NO"}

    # ------------------------------------------------------------------ #
    # PostgreSQL (credit system — shared with tg_bot)
    # CREDITS_DB_URL or POSTGRES_HOST/USER/PASSWORD/DB/SSLMODE fallback
    # ------------------------------------------------------------------ #
    credits_db_url: str = _credits_db_url_env()

    # ------------------------------------------------------------------ #
    # Payment webhook
    # ------------------------------------------------------------------ #
    # HMAC-SHA256 secret shared with the payment provider.
    # If empty, the /payments/webhook endpoint returns 403 on all requests.
    payment_webhook_secret: str = _env("PAYMENT_WEBHOOK_SECRET", "")
    # Bearer token for /payments/activate (admin manual activation).
    # If empty, the endpoint is disabled (returns 403).
    payment_admin_token: str = _env("PAYMENT_ADMIN_TOKEN", "")

    # Ops alerts (used for Windows node auto-disable notifications).
    alert_telegram_bot_token: str = _env("ALERT_TELEGRAM_BOT_TOKEN", "")
    alert_telegram_chat_id: str = _env("ALERT_TELEGRAM_CHAT_ID", "")


SETTINGS = Settings()
