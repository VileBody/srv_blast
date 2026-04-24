from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

TELEGRAM_API_ENV_PROD = "prod"
TELEGRAM_API_ENV_TEST = "test"
TELEGRAM_API_ENVS = {TELEGRAM_API_ENV_PROD, TELEGRAM_API_ENV_TEST}


def normalize_telegram_api_env(value: str | None, *, name: str = "TG_BOT_API_ENV") -> str:
    env = str(value or TELEGRAM_API_ENV_PROD).strip().lower()
    if env not in TELEGRAM_API_ENVS:
        allowed = ", ".join(sorted(TELEGRAM_API_ENVS))
        raise RuntimeError(f"{name} must be one of: {allowed} (got {env!r})")
    return env


def require_telegram_token(token: str | None, *, name: str) -> str:
    value = str(token or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for Telegram API access")
    return value


@dataclass(frozen=True)
class TelegramApi:
    environment: str

    @classmethod
    def from_env(cls, value: str | None, *, name: str = "TG_BOT_API_ENV") -> "TelegramApi":
        return cls(environment=normalize_telegram_api_env(value, name=name))

    @property
    def is_test(self) -> bool:
        return self.environment == TELEGRAM_API_ENV_TEST

    def method_url(self, *, token: str, method: str) -> str:
        bot_token = require_telegram_token(token, name="Telegram bot token")
        method_name = str(method or "").strip().lstrip("/")
        if not method_name:
            raise RuntimeError("Telegram API method is empty")
        if self.is_test:
            return f"https://api.telegram.org/bot{bot_token}/test/{method_name}"
        return f"https://api.telegram.org/bot{bot_token}/{method_name}"

    def file_url(self, *, token: str, path: str | Path) -> str:
        bot_token = require_telegram_token(token, name="Telegram bot token")
        file_path = str(path or "").strip().lstrip("/")
        if not file_path:
            raise RuntimeError("Telegram file path is empty")
        encoded_path = quote(file_path, safe="/")
        if self.is_test:
            return f"https://api.telegram.org/file/bot{bot_token}/test/{encoded_path}"
        return f"https://api.telegram.org/file/bot{bot_token}/{encoded_path}"


def make_telegram_api(value: str | None, *, name: str = "TG_BOT_API_ENV") -> TelegramApi:
    return TelegramApi.from_env(value, name=name)


def build_aiogram_session(*, api_env: str, proxy_url: str = ""):
    """Create an aiogram session pinned to prod or Telegram test Bot API.

    The import stays lazy so non-bot utilities can use URL helpers without
    requiring aiogram in lightweight test environments.
    """

    normalized = normalize_telegram_api_env(api_env)
    try:
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer
    except Exception as exc:  # pragma: no cover - depends on runtime package install
        raise RuntimeError("aiogram TelegramAPIServer support is required") from exc

    if normalized == TELEGRAM_API_ENV_TEST:
        server = getattr(TelegramAPIServer, "TEST", None) or TelegramAPIServer(
            base="https://api.telegram.org/bot{token}/test/{method}",
            file="https://api.telegram.org/file/bot{token}/test/{path}",
        )
    else:
        server = TelegramAPIServer.PRODUCTION
    kwargs = {"api": server}
    proxy = str(proxy_url or "").strip()
    if proxy:
        kwargs["proxy"] = proxy
    return AiohttpSession(**kwargs)
