from __future__ import annotations

import os
from dataclasses import dataclass


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _required_int_env(name: str, *, min_value: int = 1, max_value: int | None = None) -> int:
    raw = _required_env(name)
    try:
        value = int(raw)
    except ValueError as e:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from e

    if value < min_value:
        raise RuntimeError(f"{name} must be >= {min_value}, got {value}")
    if max_value is not None and value > max_value:
        raise RuntimeError(f"{name} must be <= {max_value}, got {value}")
    return value


@dataclass(frozen=True)
class AssetUISettings:
    s3_bucket_assets: str
    port: int
    upload_max_mb: int
    trash_prefix: str
    presign_ttl_s: int


def load_settings() -> AssetUISettings:
    trash_prefix = _required_env("ASSET_UI_TRASH_PREFIX").strip("/")
    if not trash_prefix:
        raise RuntimeError("ASSET_UI_TRASH_PREFIX must not be empty after trimming '/'")

    return AssetUISettings(
        s3_bucket_assets=_required_env("S3_BUCKET_ASSET_STORAGE"),
        port=_required_int_env("ASSET_UI_PORT", min_value=1, max_value=65535),
        upload_max_mb=_required_int_env("ASSET_UI_UPLOAD_MAX_MB", min_value=1),
        trash_prefix=trash_prefix,
        presign_ttl_s=_required_int_env("ASSET_UI_PRESIGN_TTL_S", min_value=1),
    )

