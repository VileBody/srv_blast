from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import boto3


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def s3_enabled() -> bool:
    # Если ключи есть — считаем, что S3 включен
    return bool(_env("S3_ACCESS_KEY_ID") and _env("S3_SECRET_ACCESS_KEY") and _env("S3_ENDPOINT_URL"))


def _s3_client():
    """
    Timeweb Cloud S3 / любой S3-compatible.
    """
    endpoint_url = _env("S3_ENDPOINT_URL")
    region = _env("S3_REGION", "ru-1")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        region_name=region or None,
        aws_access_key_id=_env("S3_ACCESS_KEY_ID") or None,
        aws_secret_access_key=_env("S3_SECRET_ACCESS_KEY") or None,
    )


def parse_s3_url(url: str) -> Tuple[str, str]:
    """
    s3://bucket/key -> (bucket, key)
    """
    u = (url or "").strip()
    if not u.startswith("s3://"):
        raise ValueError(f"Not an s3 url: {url!r}")
    rest = u[len("s3://") :]
    if "/" not in rest:
        raise ValueError(f"Invalid s3 url (missing key): {url!r}")
    bucket, key = rest.split("/", 1)
    bucket = bucket.strip()
    key = key.strip()
    if not bucket or not key:
        raise ValueError(f"Invalid s3 url: {url!r}")
    return bucket, key


def download_file_from_s3(*, bucket: str, key: str, dest: Path) -> None:
    """
    Надёжный вариант: качаем напрямую через SDK, без presign и HTTP.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    c = _s3_client()
    c.download_file(bucket, key, str(dest))


def generate_presigned_url(*, bucket: str, key: str, expires_in: int = 3600) -> str:
    """
    Presign полезен для отдачи наружу (например, результат рендера клиенту).
    Для скачивания на Win-ноде НЕ нужен, если качаем через SDK.
    """
    c = _s3_client()
    return c.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=int(expires_in),
    )


def upload_file_to_s3(*, bucket: str, key: str, path: Path, content_type: Optional[str] = None) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    extra = {}
    if content_type:
        extra["ContentType"] = content_type

    c = _s3_client()
    c.upload_file(str(p), bucket, key, ExtraArgs=extra or None)
