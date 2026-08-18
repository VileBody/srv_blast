#!/usr/bin/env python3
"""Заливка fx-ассетов (глитч-клипы, рамки, звуки, лого) в S3.

Раскладка на S3 = то, что ждёт код:

    s3://$FX_ASSETS_S3_BUCKET/$FX_ASSETS_S3_PREFIX/
      video/glitch/*.mp4     <- клип-пул blackwhite (manifest.clip_pools)
      frames/*.png           <- рамки (mlcore/hooks/frames/catalog.py, ТОЧНЫЕ имена)
      sounds/<pool>/*        <- звуки F3
      logo/*.png             <- лого-штамп

Локальная папка передаётся как есть: её дерево 1:1 ложится под префикс.

    python scripts/upload_fx_assets_to_s3.py --src "C:/.../fx_upload" --dry-run
    python scripts/upload_fx_assets_to_s3.py --src "C:/.../fx_upload"

Идемпотентно: объект с совпадающим размером пропускается (--force перезаливает).
Креды и бакет читаются из .env репозитория (как у upload_pins2_1to1_to_s3.py)
или из окружения: FX_ASSETS_S3_BUCKET, FX_ASSETS_S3_PREFIX, S3_ENDPOINT_URL,
S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY.

После заливки сверяет, что каждый залитый объект реально лежит в бакете
(--no-verify отключает): рамки адресуются фиксированными ключами, и промах =
молча отрендеренный ролик без рамки.

Запускать там, где есть креды S3, — то есть на сервере (в репо лежит .env) или
внутри контейнера с S3-переменными. Репозиторные модули скрипт НЕ импортирует,
поэтому работает и на чекауте до мёржа этой ветки.
"""
from __future__ import annotations

import argparse
import mimetypes
import os

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Только для чтения .env — репозиторные модули скрипт намеренно не импортирует,
# чтобы запускаться на сервере со старым чекаутом (до мёржа ветки).
REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
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


def require_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(
            f"Missing env: {name}. Заполни .env в корне репо или экспортируй переменную."
        )
    return v


def make_client():
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore

    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(signature_version="s3v4", proxies={}),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def head_size(s3: Any, bucket: str, key: str) -> Optional[int]:
    try:
        return int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except Exception:
        return None


def collect(src: Path) -> List[Tuple[Path, str]]:
    """(локальный файл, относительный ключ) для всего дерева src."""
    out: List[Tuple[Path, str]] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        out.append((p, rel))
    return out


def verify_uploaded(s3: Any, bucket: str, planned: List[Tuple[str, int]]) -> List[str]:
    """Сверить, что каждый запланированный ключ реально лежит в бакете.

    Проверяем именно залитое, а не список из mlcore-каталога: скрипт должен
    запускаться и на сервере со старым чекаутом (до мёржа ветки), где модуля
    `mlcore.hooks.frames` ещё нет. Заодно нет и дрейфа «константа в скрипте vs
    каталог» — источник истины один, это сами файлы.
    """
    return [key for key, size in planned if head_size(s3, bucket, key) != size]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="локальная папка (её дерево = дерево под префиксом)")
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    ap.add_argument("--force", action="store_true", help="перезалить даже совпадающие по размеру")
    ap.add_argument("--no-verify", action="store_true", help="не сверять залитое в конце")
    args = ap.parse_args()

    load_env_file(REPO_ROOT / ".env")

    src = Path(args.src).expanduser().resolve()
    if not src.is_dir():
        raise SystemExit(f"--src не папка: {src}")

    bucket = require_env("FX_ASSETS_S3_BUCKET")
    prefix = (os.environ.get("FX_ASSETS_S3_PREFIX") or "").strip().strip("/")

    files = collect(src)
    if not files:
        raise SystemExit(f"в {src} нет файлов")

    s3 = make_client() if not args.dry_run else None
    total = uploaded = skipped = 0
    planned: List[Tuple[str, int]] = []
    for path, rel in files:
        key = (prefix + "/" + rel).strip("/")
        size = path.stat().st_size
        total += size
        planned.append((key, size))
        if args.dry_run:
            print(f"PLAN  {size/1048576:7.1f}MB  s3://{bucket}/{key}")
            continue
        if not args.force and head_size(s3, bucket, key) == size:
            print(f"SKIP  (уже есть)     s3://{bucket}/{key}")
            skipped += 1
            continue
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": ctype})
        print(f"PUT   {size/1048576:7.1f}MB  s3://{bucket}/{key}")
        uploaded += 1

    print(
        f"\n{'план' if args.dry_run else 'готово'}: файлов={len(files)} "
        f"объём={total/1048576:.1f}MB uploaded={uploaded} skipped={skipped}"
    )

    if args.dry_run or args.no_verify:
        return 0

    # Рамки адресуются фиксированными ключами, и промах = молча рендерим без
    # рамки, поэтому сверяем факт наличия каждого залитого объекта.
    missing = verify_uploaded(s3, bucket, planned)
    if missing:
        print("\nВНИМАНИЕ: эти ключи не подтвердились в бакете:")
        for k in missing:
            print(f"  s3://{bucket}/{k}")
        print("Рамки из frames/ будут выбираться в боте, но в ролик не попадут.")
        return 1
    print(f"verify: все {len(planned)} объектов на месте")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
