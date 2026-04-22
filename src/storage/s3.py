from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

_S3_CLIENT = None


class S3ObjectNotFoundError(RuntimeError):
    """Raised when object lookup/move targets a missing S3 key."""


def _is_not_found_error(exc: ClientError) -> bool:
    code = str((exc.response or {}).get("Error", {}).get("Code", "")).strip()
    return code in {"404", "NoSuchKey", "NotFound"}


def get_s3_client():
    """
    Ленивая инициализация S3-клиента под Timeweb (или любой S3-совместимый сервис).

    Берём настройки из env:
      - S3_ENDPOINT_URL
      - S3_ACCESS_KEY_ID
      - S3_SECRET_ACCESS_KEY
      - S3_REGION (опционально)

    ВАЖНО: никакого прокси здесь не используем, чтобы не ломать socks5.
    """
    global _S3_CLIENT
    if _S3_CLIENT is not None:
        return _S3_CLIENT

    endpoint = os.getenv("S3_ENDPOINT_URL")
    access_key = os.getenv("S3_ACCESS_KEY_ID")
    secret_key = os.getenv("S3_SECRET_ACCESS_KEY")
    region = os.getenv("S3_REGION") or None

    if not endpoint or not access_key or not secret_key:
        raise RuntimeError(
            "S3 is not configured: set S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY"
        )

    # Никаких proxies для S3 — прямой выход.
    # read_timeout=3600s: крупные бакет-операции (например, ZIP-стрим экспорта
    # всех ассетов в asset-ui или заливка многогигабайтного файла) могут между
    # чанками ответа ждать дольше дефолтных 60с, если S3 подтормаживает;
    # ставим верхнюю границу 1ч, чтобы не обрывать длинные операции.
    boto_config = BotoConfig(
        connect_timeout=30,
        read_timeout=3600,
        retries={"max_attempts": 3, "mode": "standard"},
    )

    session = boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    _S3_CLIENT = session.client("s3", endpoint_url=endpoint, config=boto_config)
    log.info("Initialized S3 client (endpoint=%s, region=%s)", endpoint, region)
    return _S3_CLIENT


def list_s3_objects(
    bucket: str,
    *,
    prefix: str = "",
    continuation_token: str | None = None,
    max_keys: int = 200,
    delimiter: str = "/",
) -> dict[str, Any]:
    """
    List objects/prefixes under s3://bucket/prefix with pagination.
    """
    if max_keys < 1 or max_keys > 1000:
        raise RuntimeError(f"max_keys must be in [1,1000], got {max_keys}")

    s3 = get_s3_client()
    kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Prefix": prefix,
        "MaxKeys": max_keys,
        "Delimiter": delimiter,
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token

    try:
        resp = s3.list_objects_v2(**kwargs)
    except (BotoCoreError, ClientError):
        log.exception("Failed to list objects bucket=%s prefix=%s", bucket, prefix)
        raise

    objects: list[dict[str, Any]] = []
    for item in resp.get("Contents") or []:
        key = str(item.get("Key") or "")
        if not key:
            continue
        last_modified = item.get("LastModified")
        objects.append(
            {
                "key": key,
                "size": int(item.get("Size") or 0),
                "etag": str(item.get("ETag") or "").strip('"'),
                "last_modified": (
                    last_modified.isoformat() if hasattr(last_modified, "isoformat") else None
                ),
            }
        )

    prefixes = [
        str(p.get("Prefix") or "")
        for p in (resp.get("CommonPrefixes") or [])
        if str(p.get("Prefix") or "")
    ]

    return {
        "objects": objects,
        "prefixes": prefixes,
        "next_continuation_token": str(resp.get("NextContinuationToken") or "") or None,
        "is_truncated": bool(resp.get("IsTruncated")),
    }


def head_s3_object(bucket: str, key: str) -> dict[str, Any]:
    """
    Head object metadata for s3://bucket/key.
    """
    s3 = get_s3_client()
    try:
        resp = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if _is_not_found_error(e):
            raise S3ObjectNotFoundError(f"Object not found: s3://{bucket}/{key}") from e
        raise
    except BotoCoreError:
        log.exception("Failed to head object s3://%s/%s", bucket, key)
        raise

    last_modified = resp.get("LastModified")
    return {
        "content_type": str(resp.get("ContentType") or "").strip() or None,
        "content_length": int(resp.get("ContentLength") or 0),
        "etag": str(resp.get("ETag") or "").strip('"') or None,
        "last_modified": (
            last_modified.isoformat() if hasattr(last_modified, "isoformat") else None
        ),
    }


def download_from_s3(bucket: str, key: str, dest: Path) -> Path:
    """
    Скачать объект s3://bucket/key в локальный файл dest.
    """
    s3 = get_s3_client()
    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    log.info("Downloading from s3://%s/%s -> %s", bucket, key, dest)
    try:
        s3.download_file(bucket, key, str(dest))
    except (BotoCoreError, ClientError) as e:
        log.error(
            "Failed to download s3://%s/%s: %s",
            bucket,
            key,
            e,
        )
        raise

    return dest


def upload_bytes_to_s3(bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
    """
    Залить bytes в S3 по ключу s3://bucket/key.
    """
    s3 = get_s3_client()
    extra = {}
    if content_type:
        extra["ContentType"] = content_type

    log.info("Uploading bytes to s3://%s/%s (len=%d)", bucket, key, len(data))
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=data, **extra)
    except (BotoCoreError, ClientError) as e:
        log.error(
            "Failed to upload to s3://%s/%s: %s",
            bucket,
            key,
            e,
        )
        raise


def upload_file_to_s3(bucket: str, key: str, path: Path, content_type: str | None = None) -> None:
    """
    Залить локальный файл в S3 по ключу s3://bucket/key.
    """
    s3 = get_s3_client()
    extra = {}
    if content_type:
        extra["ContentType"] = content_type

    log.info("Uploading file %s to s3://%s/%s", path, bucket, key)
    try:
        s3.upload_file(str(path), bucket, key, ExtraArgs=extra)
    except (BotoCoreError, ClientError) as e:
        log.error(
            "Failed to upload file to s3://%s/%s: %s",
            bucket,
            key,
            e,
        )
        raise


def generate_presigned_url(bucket: str, key: str, expires_in: int = 3600) -> str:
    """
    Сгенерировать presigned URL для скачивания объекта s3://bucket/key.
    """
    s3 = get_s3_client()
    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as e:
        log.error(
            "Failed to generate presigned URL for s3://%s/%s: %s",
            bucket,
            key,
            e,
        )
        raise
    log.info("Generated presigned URL for s3://%s/%s", bucket, key)
    return url


def soft_delete_s3_object(bucket: str, key: str, *, trash_prefix: str) -> str:
    """
    Soft-delete object by moving it to trash prefix:
    s3://bucket/<trash_prefix>/<YYYY-MM-DD>/<original-key>.
    """
    clean_key = str(key or "").strip().lstrip("/")
    if not clean_key:
        raise RuntimeError("soft_delete_s3_object requires non-empty key")

    clean_trash_prefix = str(trash_prefix or "").strip().strip("/")
    if not clean_trash_prefix:
        raise RuntimeError("soft_delete_s3_object requires non-empty trash_prefix")

    date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trash_key = f"{clean_trash_prefix}/{date_part}/{clean_key}"

    s3 = get_s3_client()
    try:
        s3.copy_object(
            Bucket=bucket,
            Key=trash_key,
            CopySource={"Bucket": bucket, "Key": clean_key},
            MetadataDirective="COPY",
        )
    except ClientError as e:
        if _is_not_found_error(e):
            raise S3ObjectNotFoundError(f"Object not found: s3://{bucket}/{clean_key}") from e
        raise
    except BotoCoreError:
        log.exception("Failed to copy object to trash s3://%s/%s", bucket, clean_key)
        raise

    try:
        s3.delete_object(Bucket=bucket, Key=clean_key)
    except (BotoCoreError, ClientError):
        log.exception("Failed to delete original after trash copy s3://%s/%s", bucket, clean_key)
        raise

    log.info("Soft-deleted s3://%s/%s -> s3://%s/%s", bucket, clean_key, bucket, trash_key)
    return trash_key
