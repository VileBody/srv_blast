#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _require_env(key: str) -> str:
    v = _env(key, "")
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _load_dotenv_fallback(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


def _load_env() -> None:
    env_path = Path(_env("ENV_PATH", str(ROOT / ".env"))).expanduser()
    if not env_path.is_absolute():
        env_path = (ROOT / env_path).resolve()
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        _load_dotenv_fallback(env_path)


def _make_s3_client():
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "boto3 is required.\n"
            "Install: pip install boto3\n"
            f"Import error: {e!r}"
        ) from e

    endpoint = _require_env("S3_ENDPOINT_URL")
    access_key = _require_env("S3_ACCESS_KEY_ID")
    secret_key = _require_env("S3_SECRET_ACCESS_KEY")
    region = _env("S3_REGION", "ru-1") or "ru-1"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def _read_index(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    assets = obj.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError(f"Index missing assets[]: {path}")
    return obj


def _iter_s3_keys(s3: Any, *, bucket: str, prefix: str) -> set[str]:
    out: set[str] = set()
    cont: str | None = None
    while True:
        kwargs: Dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix.rstrip('/')}/", "MaxKeys": 1000}
        if cont:
            kwargs["ContinuationToken"] = cont
        resp = s3.list_objects_v2(**kwargs)
        for it in resp.get("Contents", []) or []:
            key = str(it.get("Key") or "").strip()
            if key and not key.endswith("/"):
                out.add(key)
        if not resp.get("IsTruncated"):
            break
        cont = str(resp.get("NextContinuationToken") or "").strip() or None
    return out


def _as_pos_float(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if x > 0 else None


def _require_asset_str(asset: Dict[str, Any], key: str) -> str:
    s = str(asset.get(key) or "").strip()
    if not s:
        raise RuntimeError(f"Asset row missing required field: {key}")
    return s


def main() -> int:
    _load_env()

    mode = _env("MODE", "")
    if mode and mode != "prod":
        raise RuntimeError(f"build_selected_index.py expects MODE=prod, got {mode!r}")

    source_index_path = Path(
        _env("STATIC_ASSETS_SOURCE_INDEX_JSON", str(ROOT / "data" / "static_assets_index.json"))
    ).resolve()
    selected_out_path = Path(
        _env("STATIC_ASSETS_SELECTED_OUT", str(ROOT / "data" / "footage_inventory_selected.json"))
    ).resolve()

    source_index = _read_index(source_index_path)
    source_assets = source_index.get("assets") or []

    bucket = _require_env("S3_BUCKET_ASSET_STORAGE")
    prefix = _require_env("S3_ASSET_PREFIX").strip().strip("/")

    s3 = _make_s3_client()
    existing_keys = _iter_s3_keys(s3, bucket=bucket, prefix=prefix)

    invalid_rows = 0
    selected_assets: List[Dict[str, Any]] = []
    missing_assets: List[Dict[str, str]] = []

    for row in source_assets:
        if not isinstance(row, dict):
            invalid_rows += 1
            continue
        try:
            file_name = _require_asset_str(row, "file_name")
            genre = _require_asset_str(row, "genre").strip("/")
            tag = _require_asset_str(row, "tag").strip("/")
            src_w = int(row.get("src_w") or 0)
            src_h = int(row.get("src_h") or 0)
            duration_sec = _as_pos_float(row.get("duration_sec"))
            if src_w <= 0 or src_h <= 0 or duration_sec is None:
                raise RuntimeError("invalid dimensions or duration")
        except Exception:
            invalid_rows += 1
            continue

        key = f"{prefix}/{genre}/{tag}/{file_name}"
        if key in existing_keys:
            # Keep original row shape; normalize required fields to cleaned values.
            out_row = dict(row)
            out_row["file_name"] = file_name
            out_row["genre"] = genre
            out_row["tag"] = tag
            out_row["src_w"] = src_w
            out_row["src_h"] = src_h
            out_row["duration_sec"] = float(duration_sec)
            selected_assets.append(out_row)
        else:
            missing_assets.append(
                {"file_name": file_name, "genre": genre, "tag": tag, "expected_key": key}
            )

    # Deterministic ordering for reproducible selected file.
    selected_assets.sort(key=lambda x: str(x.get("file_name") or ""))
    missing_assets.sort(key=lambda x: str(x.get("file_name") or ""))

    if not selected_assets:
        raise RuntimeError(
            "Selected index generation produced zero assets. "
            f"source_index={source_index_path} bucket={bucket} prefix={prefix}"
        )

    out_obj: Dict[str, Any] = {
        "version": "selected.v1",
        "source_static_assets_index": str(source_index_path),
        "s3_bucket": bucket,
        "s3_prefix": prefix,
        "warnings": {
            "source_rows": int(len(source_assets)),
            "selected_rows": int(len(selected_assets)),
            "missing_rows": int(len(missing_assets)),
            "invalid_rows": int(invalid_rows),
        },
        "assets": selected_assets,
    }
    if missing_assets:
        out_obj["warnings"]["missing_sample"] = missing_assets[:20]

    selected_out_path.parent.mkdir(parents=True, exist_ok=True)
    selected_out_path.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[selected] source={source_index_path}")
    print(f"[selected] out={selected_out_path}")
    print(f"[selected] rows source={len(source_assets)} selected={len(selected_assets)} missing={len(missing_assets)} invalid={invalid_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

