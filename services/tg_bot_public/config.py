from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from core.telegram_api import TELEGRAM_API_ENV_TEST, normalize_telegram_api_env


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def _int_env(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except Exception:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _delivery_mode_env(name: str, default: str = "polling") -> str:
    mode = str(_env(name, default) or "").strip().lower()
    if mode not in {"polling", "webhook"}:
        raise RuntimeError(f"{name} must be 'polling' or 'webhook', got {mode!r}")
    return mode


def _telegram_api_env(name: str = "TG_BOT_API_ENV") -> str:
    return normalize_telegram_api_env(_env(name, "prod"), name=name)


def _maintenance_mode_env(default: bool = False) -> bool:
    """Global maintenance switch shared across services.

    SYSTEM_MAINTENANCE_MODE has priority; if it is not set, we fallback to TG flag.
    """
    raw = _env("SYSTEM_MAINTENANCE_MODE", "")
    if raw:
        return _bool_env("SYSTEM_MAINTENANCE_MODE", default)
    return bool(default)


def _maintenance_message_env(default: str = "Мы на техработах. Скоро вернемся.") -> str:
    raw = _env("SYSTEM_MAINTENANCE_MESSAGE", "")
    if raw:
        return raw
    return str(default or "").strip() or "Мы на техработах. Скоро вернемся."


def _default_processing_node_id() -> str:
    host = _env("HOSTNAME", "")
    if host:
        return host
    try:
        return str(socket.gethostname() or "").strip() or "unknown-node"
    except Exception:
        return "unknown-node"


def _normalize_username(raw: str) -> str:
    u = str(raw or "").strip().lower()
    if not u:
        return ""
    if not u.startswith("@"):
        u = "@" + u
    return u


def _username_allowlist_env(name: str) -> tuple[str, ...]:
    raw = _env(name, "")
    if not raw:
        return tuple()
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        u = _normalize_username(part)
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return tuple(out)


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    raw = _env(name, default)
    if not raw:
        return tuple()
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        item = str(part or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


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


def _active_credits_db_url_env() -> str:
    if _telegram_api_env() == TELEGRAM_API_ENV_TEST:
        return _env("TG_TEST_CREDITS_DB_URL", "")
    return _credits_db_url_env()


def _active_tg_bot_token_env() -> str:
    if _telegram_api_env() == TELEGRAM_API_ENV_TEST:
        return _env("TG_TEST_BOT_TOKEN", "")
    return _env("TG_BOT_TOKEN", "")


def _active_tg_bot_username_env() -> str:
    if _telegram_api_env() == TELEGRAM_API_ENV_TEST:
        return _env("TG_TEST_BOT_USERNAME", "")
    return _env("TG_BOT_USERNAME", "blast808bot")


def _active_preview_source_bot_token_env() -> str:
    if _telegram_api_env() == TELEGRAM_API_ENV_TEST:
        return _env("TG_TEST_PREVIEW_SOURCE_BOT_TOKEN", "")
    return _env("TG_PREVIEW_SOURCE_BOT_TOKEN", "")


@dataclass(frozen=True)
class Settings:
    tg_bot_api_env: str = _telegram_api_env()
    tg_test_bot_token: str = _env("TG_TEST_BOT_TOKEN", "")
    tg_test_bot_username: str = _env("TG_TEST_BOT_USERNAME", "")
    tg_test_bypass_subscription: bool = _bool_env("TG_TEST_BYPASS_SUBSCRIPTION", False)
    tg_bot_token: str = _active_tg_bot_token_env()
    tg_preview_source_bot_token: str = _active_preview_source_bot_token_env()
    tg_bot_username: str = _active_tg_bot_username_env()
    tg_file_proxy_url: str = _env("TG_FILE_PROXY_URL", "")
    orchestrator_public_url: str = _env("ORCHESTRATOR_PUBLIC_URL", "http://orchestrator-api:8000")

    bot_poll_interval_s: float = _float_env("BOT_POLL_INTERVAL_S", 5.0)
    tg_processing_node_id: str = _env("TG_PROCESSING_NODE_ID", _default_processing_node_id())
    tg_processing_lock_ttl_s: int = _int_env("TG_PROCESSING_LOCK_TTL_S", 240)
    tg_delivery_mode: str = _delivery_mode_env("TG_DELIVERY_MODE", "polling")
    tg_webhook_url: str = _env("TG_WEBHOOK_URL", "")
    tg_webhook_secret: str = _env("TG_WEBHOOK_SECRET", "")
    tg_webhook_path: str = _env("TG_WEBHOOK_PATH", "/telegram/webhook")
    tg_webhook_bind_host: str = _env("TG_WEBHOOK_BIND_HOST", "0.0.0.0")
    tg_webhook_port: int = _int_env("TG_WEBHOOK_PORT", 8081)
    tg_webhook_dedup_ttl_s: int = _int_env("TG_WEBHOOK_DEDUP_TTL_S", 86400)
    tg_webhook_ip_address: str = _env("TG_WEBHOOK_IP_ADDRESS", "")
    tg_webhook_delete_on_shutdown: bool = _bool_env("TG_WEBHOOK_DELETE_ON_SHUTDOWN", False)
    bot_status_update_interval_s: float = _float_env("BOT_STATUS_UPDATE_INTERVAL_S", 20.0)
    bot_recovery_poll_interval_s: float = _float_env("BOT_RECOVERY_POLL_INTERVAL_S", 60.0)
    bot_job_timeout_h: float = _float_env("BOT_JOB_TIMEOUT_H", 4.0)
    bot_referral_timeout_h: float = _float_env("BOT_REFERRAL_TIMEOUT_H", 72.0)
    tg_state_ttl_h: float = _float_env("TG_STATE_TTL_H", 720.0)
    tg_state_cleanup_interval_s: float = _float_env("TG_STATE_CLEANUP_INTERVAL_S", 900.0)
    tg_state_cleanup_batch_size: int = _int_env("TG_STATE_CLEANUP_BATCH_SIZE", 200)
    tg_state_index_cleanup_batch_size: int = _int_env("TG_STATE_INDEX_CLEANUP_BATCH_SIZE", 500)
    bot_tmp_dir: str = _env("BOT_TMP_DIR", "/app/work/tg_tmp")
    bot_fs_cleanup_interval_s: float = _float_env("BOT_FS_CLEANUP_INTERVAL_S", 900.0)
    bot_fs_cleanup_batch_size: int = _int_env("BOT_FS_CLEANUP_BATCH_SIZE", 2000)
    bot_tmp_incoming_retention_h: float = _float_env("BOT_TMP_INCOMING_RETENTION_H", 24.0)
    bot_tmp_prepared_retention_h: float = _float_env("BOT_TMP_PREPARED_RETENTION_H", 24.0)
    bot_tmp_result_retention_h: float = _float_env("BOT_TMP_RESULT_RETENTION_H", 6.0)
    bot_output_artifact_retention_h: float = _float_env("BOT_OUTPUT_ARTIFACT_RETENTION_H", 24.0)
    bot_output_debug_artifact_retention_h: float = _float_env("BOT_OUTPUT_DEBUG_ARTIFACT_RETENTION_H", 168.0)
    bot_output_artifact_allowlist: tuple[str, ...] = _csv_env(
        "BOT_OUTPUT_ARTIFACT_ALLOWLIST",
        "stage2_subtitles.json,stage2_subtitles_*.json,gemini_raw_stage2_subtitles_*.json,stage2_footage.json,stage2_footage_*.json,stage2_footage_rotation_diag.json,stage2_footage_rotation_diag_*.json",
    )
    bot_max_audio_mb: int = _int_env("BOT_MAX_AUDIO_MB", 5)
    bot_max_video_mb: int = _int_env("BOT_MAX_VIDEO_MB", 49)
    tg_video_send_timeout_s: float = _float_env("TG_VIDEO_SEND_TIMEOUT_S", 120.0)
    tg_video_send_retries: int = _int_env("TG_VIDEO_SEND_RETRIES", 2)
    tg_video_send_backoff_base_s: float = _float_env("TG_VIDEO_SEND_BACKOFF_BASE_S", 2.0)
    tg_video_compress_enabled: bool = _bool_env("TG_VIDEO_COMPRESS_ENABLED", True)
    tg_maintenance_mode: bool = _maintenance_mode_env(_bool_env("TG_MAINTENANCE_MODE", False))
    tg_maintenance_message: str = _maintenance_message_env(
        _env("TG_MAINTENANCE_MESSAGE", "Мы на техработах. Скоро вернемся.")
    )
    tg_maintenance_state_key: str = _env("TG_MAINTENANCE_STATE_KEY", "blast:tg:public:maintenance_mode")
    # When True, paying clients (credits_db.has_paid) bypass maintenance and keep generating.
    # Set False for a full stop where nobody — clients included — may generate.
    tg_maintenance_allow_paid_clients: bool = _bool_env("TG_MAINTENANCE_ALLOW_PAID_CLIENTS", True)
    system_maintenance_bypass_usernames: tuple[str, ...] = _username_allowlist_env(
        "SYSTEM_MAINTENANCE_BYPASS_USERNAMES"
    ) or (
        "@nikitaimpulse",
        "@vilebody",
        "@impulsemanage",
    )
    system_maintenance_bypass_token: str = _env("SYSTEM_MAINTENANCE_BYPASS_TOKEN", "")

    redis_host: str = _env("REDIS_HOST", "localhost")
    redis_port: int = _int_env("REDIS_PORT", 6379)
    redis_username: str = _env("REDIS_USERNAME", "")
    redis_password: str = _env("REDIS_PASSWORD", "")
    redis_db: int = _int_env("REDIS_DB", 0)

    tg_state_prefix: str = _env("TG_STATE_PREFIX", "blast:tg:public:chat_state")

    ffmpeg_bin: str = _env("FFMPEG_BIN", "ffmpeg")

    s3_endpoint_url: str = _env("S3_ENDPOINT_URL", "")
    s3_access_key_id: str = _env("S3_ACCESS_KEY_ID", "")
    s3_secret_access_key: str = _env("S3_SECRET_ACCESS_KEY", "")
    s3_region: str = _env("S3_REGION", "ru-1")

    s3_bucket_raw_audio: str = _env("S3_BUCKET_RAW_AUDIO", "")
    s3_bucket_output_video: str = _env("S3_BUCKET_OUTPUT_VIDEO", "")
    s3_bucket_asset_storage: str = _env("S3_BUCKET_ASSET_STORAGE", "")
    s3_raw_audio_prefix: str = _env("S3_RAW_AUDIO_PREFIX", "raw_audio")
    s3_presign_expires_s: int = _int_env("S3_PRESIGN_EXPIRES_S", 86400)
    tg_send_project_archive: bool = _bool_env("TG_SEND_PROJECT_ARCHIVE", False)
    artifacts_allowlist: tuple[str, ...] = _username_allowlist_env("ARTIFACTS_ALLOWLIST")
    subscription_channel: str = _env("SUBSCRIPTION_CHANNEL", "@impulsemarketing")
    manager_chat_id: int = _int_env("MANAGER_CHAT_ID", 0)
    survey_url: str = _env("SURVEY_URL", "https://forms.yandex.ru/u/69c52ce695add5c0264676e3")
    alert_telegram_bot_token: str = _env("ALERT_TELEGRAM_BOT_TOKEN", "")
    alert_telegram_chat_id: str = _env("ALERT_TELEGRAM_CHAT_ID", "")

    # Credits & admin panel
    credits_db_url: str = _active_credits_db_url_env()
    # Kept only as an explicit source for one-time migration script.
    credits_db_path: str = _env("CREDITS_DB_PATH", "/app/work/credits.db")
    admin_panel_port: int = _int_env("ADMIN_PANEL_PORT", 8080)
    admin_panel_password: str = _env("ADMIN_PANEL_PASSWORD", "changeme")
    # Redis namespace shared with tg_bot_botapi for the season phase tumbler.
    season_redis_prefix: str = _env("SEASON_REDIS_PREFIX", "blast:season")
    admin_panel_enable_donor_restart: bool = _bool_env("ADMIN_PANEL_ENABLE_DONOR_RESTART", False)
    dozzle_base_url: str = _env("DOZZLE_BASE_URL", "")
    initial_credits: int = _int_env("INITIAL_CREDITS", 2)
    jobstore_prefix: str = _env("JOBSTORE_PREFIX", "blast")
    windows_render_url: str = _env("WINDOWS_RENDER_URL", "")
    windows_donor_host: str = _env("WINDOWS_DONOR_HOST", "")
    windows_donor_url: str = _env("WINDOWS_DONOR_URL", "http://85.239.48.31:8000")
    windows_donor_user: str = _env("WINDOWS_DONOR_USER", "Administrator")
    windows_donor_password: str = _env("WINDOWS_DONOR_PASSWORD", "")
    windows_donor_canary_audio_s3_url: str = _env("WINDOWS_DONOR_CANARY_AUDIO_S3_URL", "")
    windows_donor_canary_mode: str = _env("WINDOWS_DONOR_CANARY_MODE", "with_gemini")
    windows_donor_llm_worker_type: str = _env("WINDOWS_DONOR_LLM_WORKER_TYPE", "vertex_sdk_mix")
    windows_donor_start_afterfx: bool = _bool_env("WINDOWS_DONOR_START_AFTERFX", True)
    windows_donor_kill_afterfx_first: bool = _bool_env("WINDOWS_DONOR_KILL_AFTERFX_FIRST", True)
    windows_donor_skip_restart: bool = _bool_env("WINDOWS_DONOR_SKIP_RESTART", False)
    windows_donor_health_timeout_s: int = _int_env("WINDOWS_DONOR_HEALTH_TIMEOUT_S", 180)
    windows_donor_health_poll_s: int = _int_env("WINDOWS_DONOR_HEALTH_POLL_S", 2)
    windows_donor_canary_timeout_s: int = _int_env("WINDOWS_DONOR_CANARY_TIMEOUT_S", 1800)
    windows_donor_canary_poll_s: float = _float_env("WINDOWS_DONOR_CANARY_POLL_S", 5.0)

    # T-Bank payments
    tbank_terminal_key: str = _env("TBANK_TERMINAL_KEY", "")
    tbank_password: str = _env("TBANK_PASSWORD", "")
    tbank_notify_url: str = _env("TBANK_NOTIFY_URL", "")
    offer_url: str = _env("OFFER_URL", "")

    # Finance bot webhook (income tracking)
    finance_bot_url: str = _env("FINANCE_BOT_URL", "http://finance-bot:8080")

    # Timeweb render node lifecycle (admin panel create/delete)
    twc_token: str = _env("TWC_TOKEN", "")
    twc_render_source_server_id: int = _int_env("TWC_RENDER_SOURCE_SERVER_ID", 0)
    twc_render_firewall_group_id: str = _env("TWC_RENDER_FIREWALL_GROUP_ID", "")
    twc_render_name_prefix: str = _env("TWC_RENDER_NAME_PREFIX", "blast-render-node")
    twc_render_wait_on_timeout_s: int = _int_env("TWC_RENDER_WAIT_ON_TIMEOUT_S", 1800)
    twc_render_wait_api_timeout_s: int = _int_env("TWC_RENDER_WAIT_API_TIMEOUT_S", 900)

    @property
    def tmp_dir(self) -> Path:
        p = Path(self.bot_tmp_dir).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p.resolve()

    def __post_init__(self) -> None:
        if self.tg_test_bypass_subscription and self.tg_bot_api_env != TELEGRAM_API_ENV_TEST:
            raise RuntimeError("TG_TEST_BYPASS_SUBSCRIPTION=1 is allowed only when TG_BOT_API_ENV=test")
        if self.tg_bot_api_env == TELEGRAM_API_ENV_TEST:
            if not str(self.tg_test_bot_token or "").strip():
                raise RuntimeError("TG_TEST_BOT_TOKEN is required when TG_BOT_API_ENV=test")
            if not str(self.tg_test_bot_username or "").strip():
                raise RuntimeError("TG_TEST_BOT_USERNAME is required when TG_BOT_API_ENV=test")
            if self.tg_delivery_mode == "webhook" and not str(self.tg_webhook_url or "").strip():
                raise RuntimeError("TG_WEBHOOK_URL is required for TG_BOT_API_ENV=test with webhook delivery")
            if not str(self.credits_db_url or "").strip():
                raise RuntimeError("TG_TEST_CREDITS_DB_URL is required when TG_BOT_API_ENV=test")


SETTINGS = Settings()
