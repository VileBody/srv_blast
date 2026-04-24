from __future__ import annotations

import os
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


def _telegram_api_env(name: str = "TG_BOT_API_ENV") -> str:
    return normalize_telegram_api_env(_env(name, "prod"), name=name)


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


@dataclass(frozen=True)
class Settings:
    tg_bot_api_env: str = _telegram_api_env()
    tg_test_bot_token: str = _env("TG_TEST_BOT_TOKEN", "")
    tg_bot_token: str = _active_tg_bot_token_env()
    tg_file_proxy_url: str = _env("TG_FILE_PROXY_URL", "")
    orchestrator_public_url: str = _env("ORCHESTRATOR_PUBLIC_URL", "http://orchestrator-api:8000")

    bot_poll_interval_s: float = _float_env("BOT_POLL_INTERVAL_S", 5.0)
    bot_status_update_interval_s: float = _float_env("BOT_STATUS_UPDATE_INTERVAL_S", 20.0)
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
    bot_enqueue_all_versions_async: bool = _bool_env("BOT_ENQUEUE_ALL_VERSIONS_ASYNC", True)
    tg_video_send_timeout_s: float = _float_env("TG_VIDEO_SEND_TIMEOUT_S", 120.0)
    tg_video_send_retries: int = _int_env("TG_VIDEO_SEND_RETRIES", 2)
    tg_video_send_backoff_base_s: float = _float_env("TG_VIDEO_SEND_BACKOFF_BASE_S", 2.0)
    tg_video_compress_enabled: bool = _bool_env("TG_VIDEO_COMPRESS_ENABLED", True)

    redis_host: str = _env("REDIS_HOST", "localhost")
    redis_port: int = _int_env("REDIS_PORT", 6379)
    redis_username: str = _env("REDIS_USERNAME", "")
    redis_password: str = _env("REDIS_PASSWORD", "")
    redis_db: int = _int_env("REDIS_DB", 0)

    tg_state_prefix: str = _env("TG_STATE_PREFIX", "blast:tg:chat_state")

    ffmpeg_bin: str = _env("FFMPEG_BIN", "ffmpeg")

    s3_endpoint_url: str = _env("S3_ENDPOINT_URL", "")
    s3_access_key_id: str = _env("S3_ACCESS_KEY_ID", "")
    s3_secret_access_key: str = _env("S3_SECRET_ACCESS_KEY", "")
    s3_region: str = _env("S3_REGION", "ru-1")

    s3_bucket_raw_audio: str = _env("S3_BUCKET_RAW_AUDIO", "")
    s3_bucket_output_video: str = _env("S3_BUCKET_OUTPUT_VIDEO", "")
    s3_raw_audio_prefix: str = _env("S3_RAW_AUDIO_PREFIX", "raw_audio")
    s3_presign_expires_s: int = _int_env("S3_PRESIGN_EXPIRES_S", 86400)
    tg_send_project_archive: bool = _bool_env("TG_SEND_PROJECT_ARCHIVE", False)
    artifacts_allowlist: tuple[str, ...] = _username_allowlist_env("ARTIFACTS_ALLOWLIST")

    # ------------------------------------------------------------------ #
    # PostgreSQL (credit system) — CREDITS_DB_URL or POSTGRES_* fallback
    # ------------------------------------------------------------------ #
    credits_db_url: str = _active_credits_db_url_env()

    # ------------------------------------------------------------------ #
    # Credit & access-gate settings
    # ------------------------------------------------------------------ #
    # When True, users must have credits to start generation.
    credits_required: bool = _bool_env("CREDITS_REQUIRED", False)
    # Credits spent per generation (batch of any size = 1 credit by default).
    credits_per_generation: int = _int_env("CREDITS_PER_GENERATION", 1)
    # Credits granted to the inviter when their referral completes a first generation.
    referral_bonus_credits: int = _int_env("REFERRAL_BONUS_CREDITS", 1)

    # ------------------------------------------------------------------ #
    # Recovery timeouts
    # ------------------------------------------------------------------ #
    # Hours before a stuck PROCESSING chat is automatically reset.
    bot_job_timeout_h: float = _float_env("BOT_JOB_TIMEOUT_H", 2.0)
    # Hours before a stuck WAITING_REFERRAL chat is automatically reset.
    bot_referral_timeout_h: float = _float_env("BOT_REFERRAL_TIMEOUT_H", 48.0)

    @property
    def tmp_dir(self) -> Path:
        p = Path(self.bot_tmp_dir).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p.resolve()

    def __post_init__(self) -> None:
        if self.tg_bot_api_env == TELEGRAM_API_ENV_TEST and not str(self.tg_test_bot_token or "").strip():
            raise RuntimeError("TG_TEST_BOT_TOKEN is required when TG_BOT_API_ENV=test")


SETTINGS = Settings()
