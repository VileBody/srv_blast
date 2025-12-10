from __future__ import annotations

import logging
import os
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

_S3_CLIENT = None


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

    # Никаких proxies для S3 — прямой выход
    boto_config = BotoConfig()

    session = boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    _S3_CLIENT = session.client("s3", endpoint_url=endpoint, config=boto_config)
    log.info("Initialized S3 client (endpoint=%s, region=%s)", endpoint, region)
    return _S3_CLIENT


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
