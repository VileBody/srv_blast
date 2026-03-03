#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import uuid


ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv_fallback(env_file: Path) -> None:
    if not env_file.exists() or not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


def load_env() -> Optional[Path]:
    raw = (os.environ.get("ENV_PATH") or "").strip()
    env_path = Path(raw).expanduser() if raw else (ROOT / ".env")
    if not env_path.is_absolute():
        env_path = (ROOT / env_path).resolve()
    if not env_path.exists():
        return None
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        _load_dotenv_fallback(env_path)
    return env_path


def _require_non_empty(value: str, label: str) -> str:
    v = str(value or "").strip()
    if not v:
        raise RuntimeError(f"Missing required value: {label}")
    return v


def _build_s3_client(*, endpoint_url: str, access_key: str, secret_key: str, region: str):
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "boto3 is required.\n"
            "Install: pip install boto3\n"
            f"Import error: {e!r}"
        ) from e

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def _extract_s3_error_code(exc: Exception) -> str:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "").strip()
    return code


def _is_not_found_error(exc: Exception) -> bool:
    code = _extract_s3_error_code(exc)
    return code in {"404", "NoSuchBucket", "NotFound", "NoSuchKey"}


def _is_forbidden_error(exc: Exception) -> bool:
    code = _extract_s3_error_code(exc)
    return code in {"403", "AccessDenied", "Forbidden"}


def _head_bucket_exists(s3: Any, *, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except Exception as e:
        if _is_not_found_error(e):
            return False
        raise


def _ensure_destination_bucket(
    s3: Any,
    *,
    bucket: str,
    region: str,
    create_if_missing: bool,
) -> None:
    exists = _head_bucket_exists(s3, bucket=bucket)
    if exists:
        return
    if not create_if_missing:
        raise RuntimeError(
            f"Destination bucket does not exist: {bucket!r}. "
            "Re-run with --create-dst-bucket to create it."
        )

    create_kwargs: Dict[str, Any] = {"Bucket": bucket}
    if region and region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**create_kwargs)


def _iter_s3_objects(s3: Any, *, bucket: str, prefix: str) -> Iterable[Dict[str, Any]]:
    paginator = s3.get_paginator("list_objects_v2")
    kwargs: Dict[str, Any] = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            key = str(obj.get("Key") or "")
            size = int(obj.get("Size") or 0)
            if not key:
                raise RuntimeError("S3 list returned object with empty Key")
            if key.endswith("/"):
                if size == 0:
                    continue
                raise RuntimeError(f"S3 object key ends with '/' but has non-zero size: {key}")
            yield {"key": key, "size": size}


def _build_destination_key(
    *,
    source_key: str,
    source_prefix: str,
    destination_prefix: str,
    drop_source_prefix: bool,
) -> str:
    local_key = source_key
    if drop_source_prefix and source_prefix:
        if not source_key.startswith(source_prefix):
            raise RuntimeError(
                "Key does not start with source prefix while --drop-src-prefix-in-dst is enabled: "
                f"source_key={source_key!r}, source_prefix={source_prefix!r}"
            )
        local_key = source_key[len(source_prefix) :].lstrip("/")
        if not local_key:
            raise RuntimeError(
                "Destination key became empty after dropping source prefix. "
                f"source_key={source_key!r}, source_prefix={source_prefix!r}"
            )

    if destination_prefix:
        out = f"{destination_prefix.rstrip('/')}/{local_key.lstrip('/')}"
    else:
        out = local_key
    out = out.lstrip("/")
    if not out:
        raise RuntimeError(f"Computed empty destination key for source_key={source_key!r}")
    return out


def _destination_exists(s3: Any, *, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        if _is_not_found_error(e):
            return False
        raise


def _copy_object_via_tmp_file(
    *,
    source_s3: Any,
    source_bucket: str,
    source_key: str,
    source_size: int,
    destination_s3: Any,
    destination_bucket: str,
    destination_key: str,
    tmp_dir: Path,
) -> None:
    source_meta = source_s3.head_object(Bucket=source_bucket, Key=source_key)

    extra_args: Dict[str, Any] = {}
    for field in (
        "CacheControl",
        "ContentDisposition",
        "ContentEncoding",
        "ContentLanguage",
        "ContentType",
        "Expires",
    ):
        value = source_meta.get(field)
        if value is not None:
            extra_args[field] = value
    metadata = source_meta.get("Metadata")
    if isinstance(metadata, dict) and metadata:
        extra_args["Metadata"] = metadata

    suffix = Path(destination_key).suffix
    tmp_path = tmp_dir / f"s3_to_minio_{uuid.uuid4().hex}{suffix}"
    try:
        source_s3.download_file(source_bucket, source_key, str(tmp_path))
        if tmp_path.stat().st_size != source_size:
            raise RuntimeError(
                f"downloaded temp file size mismatch for key={source_key!r}: "
                f"expected={source_size}, got={tmp_path.stat().st_size}"
            )
        if extra_args:
            destination_s3.upload_file(str(tmp_path), destination_bucket, destination_key, ExtraArgs=extra_args)
        else:
            destination_s3.upload_file(str(tmp_path), destination_bucket, destination_key)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(n)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f}{unit}"
        value /= 1024.0
    return f"{n}B"


def main() -> int:
    env_path = load_env()

    ap = argparse.ArgumentParser(
        "s3_to_minio_once.py",
        description="One-shot mirror from source S3 bucket to MinIO bucket.",
    )
    ap.add_argument("--src-endpoint-url", default=os.environ.get("S3_ENDPOINT_URL", ""))
    ap.add_argument("--src-access-key-id", default=os.environ.get("S3_ACCESS_KEY_ID", ""))
    ap.add_argument("--src-secret-access-key", default=os.environ.get("S3_SECRET_ACCESS_KEY", ""))
    ap.add_argument("--src-region", default=os.environ.get("S3_REGION", ""))
    ap.add_argument("--src-bucket", default=os.environ.get("S3_BUCKET_ASSET_STORAGE", ""))
    ap.add_argument("--src-prefix", default="")

    ap.add_argument("--dst-endpoint-url", default=os.environ.get("MINIO_ENDPOINT_URL", ""))
    ap.add_argument("--dst-access-key-id", default=os.environ.get("MINIO_ROOT_USER", ""))
    ap.add_argument("--dst-secret-access-key", default=os.environ.get("MINIO_ROOT_PASSWORD", ""))
    ap.add_argument("--dst-region", default=os.environ.get("MINIO_REGION", ""))
    ap.add_argument("--dst-bucket", default=os.environ.get("MINIO_BUCKET_ASSET_STORAGE", ""))
    ap.add_argument("--dst-prefix", default="")
    ap.add_argument("--drop-src-prefix-in-dst", action="store_true")
    ap.add_argument("--create-dst-bucket", action="store_true")
    ap.add_argument("--tmp-dir", default="/tmp", help="Temporary directory for downloaded source objects")

    ap.add_argument("--max-files", type=int, default=0, help="Limit number of files (0 = all)")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.max_files < 0:
        raise RuntimeError("--max-files must be >= 0")

    src_endpoint = _require_non_empty(args.src_endpoint_url, "--src-endpoint-url or S3_ENDPOINT_URL")
    src_access = _require_non_empty(args.src_access_key_id, "--src-access-key-id or S3_ACCESS_KEY_ID")
    src_secret = _require_non_empty(args.src_secret_access_key, "--src-secret-access-key or S3_SECRET_ACCESS_KEY")
    src_region = _require_non_empty(args.src_region, "--src-region or S3_REGION")
    src_bucket = _require_non_empty(args.src_bucket, "--src-bucket or S3_BUCKET_ASSET_STORAGE")

    dst_endpoint = _require_non_empty(args.dst_endpoint_url, "--dst-endpoint-url or MINIO_ENDPOINT_URL")
    dst_access = _require_non_empty(args.dst_access_key_id, "--dst-access-key-id or MINIO_ROOT_USER")
    dst_secret = _require_non_empty(args.dst_secret_access_key, "--dst-secret-access-key or MINIO_ROOT_PASSWORD")
    dst_region = _require_non_empty(args.dst_region, "--dst-region or MINIO_REGION")
    dst_bucket = _require_non_empty(args.dst_bucket, "--dst-bucket or MINIO_BUCKET_ASSET_STORAGE")

    src_prefix = str(args.src_prefix or "").lstrip("/")
    dst_prefix = str(args.dst_prefix or "").lstrip("/")
    tmp_dir = Path(str(args.tmp_dir)).expanduser().resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if env_path:
        print(f"[info] Loaded env from: {env_path}")
    print(f"[info] Source: endpoint={src_endpoint}, bucket={src_bucket}, prefix={src_prefix or '(root)'}")
    print(f"[info] Destination: endpoint={dst_endpoint}, bucket={dst_bucket}, prefix={dst_prefix or '(root)'}")
    print(f"[info] Temp dir: {tmp_dir}")
    print(f"[info] Dry run: {bool(args.dry_run)}")
    print(f"[info] Skip existing: {bool(args.skip_existing)}")
    print(f"[info] Drop src prefix in dst: {bool(args.drop_src_prefix_in_dst)}")

    source_s3 = _build_s3_client(
        endpoint_url=src_endpoint,
        access_key=src_access,
        secret_key=src_secret,
        region=src_region,
    )
    destination_s3 = _build_s3_client(
        endpoint_url=dst_endpoint,
        access_key=dst_access,
        secret_key=dst_secret,
        region=dst_region,
    )

    try:
        src_bucket_exists = _head_bucket_exists(source_s3, bucket=src_bucket)
    except Exception as e:
        if _is_forbidden_error(e):
            src_bucket_exists = True
            print(f"[warn] Source head_bucket forbidden for {src_bucket!r}; continue with list/read checks.")
        else:
            raise
    if not src_bucket_exists:
        raise RuntimeError(f"Source bucket does not exist: {src_bucket!r}")
    _ensure_destination_bucket(
        destination_s3,
        bucket=dst_bucket,
        region=dst_region,
        create_if_missing=bool(args.create_dst_bucket),
    )

    objects = list(_iter_s3_objects(source_s3, bucket=src_bucket, prefix=src_prefix))
    if args.max_files > 0:
        objects = objects[: args.max_files]

    total_bytes = sum(int(x["size"]) for x in objects)
    print(f"[info] Objects to process: {len(objects)} ({_human_bytes(total_bytes)})")
    if not objects:
        print("[done] Nothing to mirror.")
        return 0

    copied = 0
    skipped = 0

    for idx, obj in enumerate(objects, start=1):
        source_key = str(obj["key"])
        source_size = int(obj["size"])
        destination_key = _build_destination_key(
            source_key=source_key,
            source_prefix=src_prefix,
            destination_prefix=dst_prefix,
            drop_source_prefix=bool(args.drop_src_prefix_in_dst),
        )

        if args.skip_existing and _destination_exists(destination_s3, bucket=dst_bucket, key=destination_key):
            skipped += 1
            print(f"[{idx}/{len(objects)}] skip existing: {destination_key}")
            continue

        print(
            f"[{idx}/{len(objects)}] mirror: "
            f"s3://{src_bucket}/{source_key} -> s3://{dst_bucket}/{destination_key} "
            f"({_human_bytes(source_size)})"
        )
        if args.dry_run:
            continue

        _copy_object_via_tmp_file(
            source_s3=source_s3,
            source_bucket=src_bucket,
            source_key=source_key,
            source_size=source_size,
            destination_s3=destination_s3,
            destination_bucket=dst_bucket,
            destination_key=destination_key,
            tmp_dir=tmp_dir,
        )
        copied += 1

    print(f"[done] Completed. copied={copied}, skipped={skipped}, total={len(objects)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[abort] Interrupted by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        raise SystemExit(1)
