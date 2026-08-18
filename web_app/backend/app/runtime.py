"""Explicit runtime contract for the web application.

The web UI can run against the local product mock during development or the
production S3/orchestrator/billing integrations.  Selecting the backend is an
operator decision: production must never silently fall back to mock data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"web_runtime_config: {name} is required")
    return value


def _flag(name: str, default: str = "0") -> bool:
    value = str(os.getenv(name, default)).strip()
    if value not in {"0", "1"}:
        raise RuntimeError(f"web_runtime_config: {name} must be 0 or 1")
    return value == "1"


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    backend: str
    app_url: str
    session_secret: str
    cors_origins: tuple[str, ...]
    dev_tools: bool
    require_auth: bool

    @property
    def production(self) -> bool:
        return self.mode == "prod"

    @classmethod
    def load(cls) -> "RuntimeSettings":
        mode = _required("MODE").lower()
        if mode not in {"dev", "prod"}:
            raise RuntimeError("web_runtime_config: MODE must be dev or prod")

        backend = _required("BLAST_BACKEND_MODE").lower()
        if backend not in {"mock", "production"}:
            raise RuntimeError(
                "web_runtime_config: BLAST_BACKEND_MODE must be mock or production"
            )
        if mode == "prod" and backend != "production":
            raise RuntimeError(
                "web_runtime_config: MODE=prod requires BLAST_BACKEND_MODE=production"
            )

        app_url = _required("APP_URL").rstrip("/")
        parsed = urlparse(app_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("web_runtime_config: APP_URL must be an absolute HTTP(S) URL")

        origins = tuple(
            origin.strip().rstrip("/")
            for origin in _required("BLAST_CORS_ORIGINS").split(",")
            if origin.strip()
        )
        if not origins:
            raise RuntimeError("web_runtime_config: BLAST_CORS_ORIGINS is empty")

        settings = cls(
            mode=mode,
            backend=backend,
            app_url=app_url,
            session_secret=_required("BLAST_SESSION_SECRET"),
            cors_origins=origins,
            dev_tools=_flag("BLAST_DEV_TOOLS"),
            require_auth=_flag("BLAST_REQUIRE_AUTH", "1"),
        )
        settings._validate_security()
        return settings

    def _validate_security(self) -> None:
        if not self.production:
            return
        if len(self.session_secret) < 32:
            raise RuntimeError(
                "web_runtime_config: BLAST_SESSION_SECRET must contain at least 32 characters"
            )
        if self.dev_tools:
            raise RuntimeError("web_runtime_config: BLAST_DEV_TOOLS must be 0 in prod")
        if not self.require_auth:
            raise RuntimeError("web_runtime_config: BLAST_REQUIRE_AUTH must be 1 in prod")
        if not _flag("BLAST_COOKIE_SECURE"):
            raise RuntimeError("web_runtime_config: BLAST_COOKIE_SECURE must be 1 in prod")
        if not _flag("BLAST_CSRF", "1"):
            raise RuntimeError("web_runtime_config: BLAST_CSRF must be 1 in prod")
        if not _flag("BLAST_RATE_LIMIT", "1"):
            raise RuntimeError("web_runtime_config: BLAST_RATE_LIMIT must be 1 in prod")
        if not _required("DATABASE_URL").lower().startswith(("postgres://", "postgresql://")):
            raise RuntimeError("web_runtime_config: prod DATABASE_URL must be PostgreSQL")
        _required("REDIS_URL")
        for name in (
            "CREDITS_DB_URL",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_BOT_USERNAME",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REDIRECT_URI",
            "TIKTOK_CLIENT_KEY",
            "TIKTOK_CLIENT_SECRET",
            "TIKTOK_REDIRECT_URI",
            "TIKTOK_TOKEN_KEY",
            "TIKTOK_UPLOAD_SOURCE",
        ):
            _required(name)
        if _required("TIKTOK_UPLOAD_SOURCE").upper() != "FILE_UPLOAD":
            raise RuntimeError(
                "web_runtime_config: production requires TIKTOK_UPLOAD_SOURCE=FILE_UPLOAD "
                "until a Blast-owned media domain is verified by TikTok"
            )
        for origin in self.cors_origins:
            if not origin.startswith("https://"):
                raise RuntimeError(
                    "web_runtime_config: prod CORS origins must use HTTPS"
                )
        expected_callbacks = {
            "GOOGLE_REDIRECT_URI": f"{self.app_url}/api/auth/google/callback",
            "TIKTOK_REDIRECT_URI": f"{self.app_url}/api/tiktok/callback",
        }
        for name, expected in expected_callbacks.items():
            if _required(name) != expected:
                raise RuntimeError(
                    f"web_runtime_config: {name} must be {expected!r}"
                )


SETTINGS = RuntimeSettings.load()
