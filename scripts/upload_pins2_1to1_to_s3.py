#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


def require_env(name: str) -> str:
    v = (os.environ.get(name) or '').strip()
    if not v:
        raise RuntimeError(f'Missing env: {name}')
    return v


def list_existing_keys(s3: Any, bucket: str, prefix: str) -> set[str]:
    out: set[str] = set()
    token: str | None = None
    while True:
        kwargs: Dict[str, Any] = {
            'Bucket': bucket,
            'Prefix': prefix.rstrip('/') + '/',
            'MaxKeys': 1000,
        }
        if token:
            kwargs['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kwargs)
        for it in resp.get('Contents', []) or []:
            key = str(it.get('Key') or '').strip()
            if key and not key.endswith('/'):
                out.add(key)
        if not resp.get('IsTruncated'):
            break
        token = str(resp.get('NextContinuationToken') or '').strip() or None
        if token is None:
            break
    return out


def main() -> int:
    repo = Path('.').resolve()
    load_env_file(repo / '.env')

    endpoint = require_env('S3_ENDPOINT_URL')
    access_key = require_env('S3_ACCESS_KEY_ID')
    secret_key = require_env('S3_SECRET_ACCESS_KEY')
    region = (os.environ.get('S3_REGION') or 'ru-1').strip() or 'ru-1'

    inventory_path = repo / 'pins2' / 'footage_inventory_1to1.json'
    merged_dir = repo / 'pins2' / 'merged_1to1'
    report_path = repo / 'pins2' / 's3_upload_1to1_report.json'

    inv = json.loads(inventory_path.read_text(encoding='utf-8'))
    assets = inv.get('assets') or []
    if not isinstance(assets, list) or not assets:
        raise RuntimeError(f'Invalid assets in {inventory_path}')

    bucket = str((inv.get('assets')[0] or {}).get('s3_bucket') or os.environ.get('S3_BUCKET_ASSET_STORAGE') or '').strip()
    if not bucket:
        bucket = require_env('S3_BUCKET_ASSET_STORAGE')

    prefix = str(inv.get('s3_prefix') or '').strip()
    if not prefix:
        prefix = 'pinterest_collection/pins2_1to1_20260323'

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version='s3v4', retries={'max_attempts': 8, 'mode': 'standard'}),
    )

    print(f'[s3] bucket={bucket} prefix={prefix}')
    existing = list_existing_keys(s3=s3, bucket=bucket, prefix=prefix)
    print(f'[s3] existing_under_prefix={len(existing)}')

    tasks: List[Tuple[Path, str]] = []
    missing_local: List[str] = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        file_name = str(a.get('file_name') or '').strip()
        key = str(a.get('s3_key') or '').strip()
        if not file_name or not key:
            continue
        src = merged_dir / file_name
        if not src.exists():
            missing_local.append(file_name)
            continue
        if key in existing:
            continue
        tasks.append((src, key))

    if missing_local:
        raise RuntimeError(f'Missing local files in merged_1to1: count={len(missing_local)} sample={missing_local[:20]}')

    print(f'[s3] total_assets={len(assets)} to_upload={len(tasks)} skip_existing={len(assets)-len(tasks)}')

    uploaded = 0
    failed = 0
    uploaded_bytes = 0
    errors: List[Dict[str, str]] = []

    def _upload_one(item: Tuple[Path, str]) -> Tuple[bool, int, str, str | None]:
        src, key = item
        ctype, _ = mimetypes.guess_type(src.name)
        extra = {'ContentType': ctype or 'video/mp4'}
        try:
            s3.upload_file(str(src), bucket, key, ExtraArgs=extra)
            return True, int(src.stat().st_size), key, None
        except Exception as e:
            return False, 0, key, repr(e)

    max_workers = 16
    print(f'[s3] max_workers={max_workers}')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_upload_one, t) for t in tasks]
        total = len(futs)
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            ok, sz, key, err = fut.result()
            if ok:
                uploaded += 1
                uploaded_bytes += sz
            else:
                failed += 1
                errors.append({'key': key, 'error': err or 'unknown'})
            if done % 25 == 0 or done == total:
                print(f'[s3] progress {done}/{total} uploaded={uploaded} failed={failed}')

    report = {
        'bucket': bucket,
        'prefix': prefix,
        'assets_total': len(assets),
        'existing_under_prefix_before': len(existing),
        'attempted_uploads': len(tasks),
        'uploaded_ok': uploaded,
        'failed': failed,
        'uploaded_bytes': uploaded_bytes,
        'errors_sample': errors[:50],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[s3] report={report_path}')
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if failed > 0:
        raise RuntimeError(f'Upload failed for {failed} objects')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
