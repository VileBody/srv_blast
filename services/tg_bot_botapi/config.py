from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class Settings:
    tg_bot_token: str = _env("TG_BOT_TOKEN", "")
    tg_file_proxy_url: str = _env("TG_FILE_PROXY_URL", "")
    orchestrator_public_url: str = _env("ORCHESTRATOR_PUBLIC_URL", "http://orchestrator-api:8000")

    bot_poll_interval_s: float = _float_env("BOT_POLL_INTERVAL_S", 5.0)
    bot_status_update_interval_s: float = _float_env("BOT_STATUS_UPDATE_INTERVAL_S", 20.0)
    bot_tmp_dir: str = _env("BOT_TMP_DIR", "/app/work/tg_tmp")
    bot_max_audio_mb: int = _int_env("BOT_MAX_AUDIO_MB", 5)

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

    @property
    def tmp_dir(self) -> Path:
        p = Path(self.bot_tmp_dir).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p.resolve()


SETTINGS = Settings()
