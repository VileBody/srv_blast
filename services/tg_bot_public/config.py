from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


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


@dataclass(frozen=True)
class Settings:
    tg_bot_token: str = _env("TG_BOT_TOKEN", "")
    tg_bot_username: str = _env("TG_BOT_USERNAME", "blast808bot")
    tg_file_proxy_url: str = _env("TG_FILE_PROXY_URL", "")
    orchestrator_public_url: str = _env("ORCHESTRATOR_PUBLIC_URL", "http://orchestrator-api:8000")

    bot_poll_interval_s: float = _float_env("BOT_POLL_INTERVAL_S", 5.0)
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
        "stage2_subtitles.json,stage2_subtitles_*.json,gemini_raw_stage2_subtitles_*.json,stage2_footage.json,stage2_footage_*.json",
    )
    bot_max_audio_mb: int = _int_env("BOT_MAX_AUDIO_MB", 5)

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
    s3_raw_audio_prefix: str = _env("S3_RAW_AUDIO_PREFIX", "raw_audio")
    s3_presign_expires_s: int = _int_env("S3_PRESIGN_EXPIRES_S", 86400)
    tg_send_project_archive: bool = _bool_env("TG_SEND_PROJECT_ARCHIVE", False)
    artifacts_allowlist: tuple[str, ...] = _username_allowlist_env("ARTIFACTS_ALLOWLIST")
    subscription_channel: str = _env("SUBSCRIPTION_CHANNEL", "@impulsemarketing")
    manager_chat_id: int = _int_env("MANAGER_CHAT_ID", 0)
    survey_url: str = _env("SURVEY_URL", "https://forms.yandex.ru/u/69c52ce695add5c0264676e3")

    # Credits & admin panel
    credits_db_url: str = _credits_db_url_env()
    # Kept only as an explicit source for one-time migration script.
    credits_db_path: str = _env("CREDITS_DB_PATH", "/app/work/credits.db")
    admin_panel_port: int = _int_env("ADMIN_PANEL_PORT", 8080)
    admin_panel_password: str = _env("ADMIN_PANEL_PASSWORD", "changeme")
    initial_credits: int = _int_env("INITIAL_CREDITS", 2)
    jobstore_prefix: str = _env("JOBSTORE_PREFIX", "blast")
    windows_render_url: str = _env("WINDOWS_RENDER_URL", "")

    # T-Bank payments
    tbank_terminal_key: str = _env("TBANK_TERMINAL_KEY", "")
    tbank_password: str = _env("TBANK_PASSWORD", "")
    tbank_notify_url: str = _env("TBANK_NOTIFY_URL", "")
    offer_url: str = _env("OFFER_URL", "")

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


SETTINGS = Settings()
