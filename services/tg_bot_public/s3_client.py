from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Tuple

import boto3
from botocore.config import Config

from .config import Settings


def parse_s3_url(url: str) -> Tuple[str, str]:
    u = (url or "").strip()
    if not u.startswith("s3://"):
        raise ValueError(f"not an s3 url: {url!r}")
    tail = u[5:]
    if "/" not in tail:
        raise ValueError(f"invalid s3 url (missing key): {url!r}")
    bucket, key = tail.split("/", 1)
    bucket = bucket.strip()
    key = key.strip()
    if not bucket or not key:
        raise ValueError(f"invalid s3 url: {url!r}")
    return bucket, key


def make_s3_url(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key.lstrip('/')}"


def _guess_content_type(path: Path) -> str:
    ct, _ = mimetypes.guess_type(str(path))
    return ct or "application/octet-stream"


class S3Client:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def validate_core(self) -> None:
        required = {
            "S3_ENDPOINT_URL": self._settings.s3_endpoint_url,
            "S3_ACCESS_KEY_ID": self._settings.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self._settings.s3_secret_access_key,
        }
        missing = [k for k, v in required.items() if not str(v or "").strip()]
        if missing:
            raise RuntimeError(f"missing required S3 env vars: {', '.join(missing)}")

    def upload_file(self, *, path: Path, bucket: str, key: str, content_type: str | None = None) -> str:
        if not bucket:
            raise RuntimeError("upload_file requires non-empty bucket")
        p = path.expanduser().resolve()
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"upload file not found: {p}")
        ct = content_type or _guess_content_type(p)
        self._client.upload_file(
            Filename=str(p),
            Bucket=bucket,
            Key=key,
            ExtraArgs={"ContentType": ct},
        )
        return make_s3_url(bucket, key)

    def download_file(self, *, bucket: str, key: str, dest: Path) -> Path:
        d = dest.expanduser().resolve()
        d.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(bucket, key, str(d))
        return d

    def download_s3_url(self, *, s3_url: str, dest: Path) -> Path:
        bucket, key = parse_s3_url(s3_url)
        return self.download_file(bucket=bucket, key=key, dest=dest)

    def generate_presigned_url(self, *, bucket: str, key: str, expires_s: int | None = None) -> str:
        ttl = int(expires_s or self._settings.s3_presign_expires_s)
        return str(
            self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=max(1, ttl),
            )
        )

    def generate_presigned_for_s3_url(self, *, s3_url: str, expires_s: int | None = None) -> str:
        bucket, key = parse_s3_url(s3_url)
        return self.generate_presigned_url(bucket=bucket, key=key, expires_s=expires_s)
