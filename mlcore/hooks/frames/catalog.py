"""Каталог рамок («Рамка» — шаг в боте, отдельный от выбора хука).

Рамка = PNG 1080×1920, чёрная маска с прозрачным окном, которая ложится
ПОВЕРХ всех слоёв компа (включая субтитры) на всю длину ролика. Это не хук:
шаг доступен на любом пути, чтобы юзер мог разнообразить кадр.

Доставка ассета — тот же media[]-транспорт, что у F3 (S3 → media[] → ноду):
  s3://<FX_ASSETS_S3_BUCKET>/<FX_ASSETS_S3_PREFIX>/frames/<file>

Нет env / нет файла на S3 → resolve_frame_asset вернёт None, и билдер
пропустит рамку (рендер идёт без неё, а не падает).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

LOGGER = logging.getLogger(__name__)

# id -> (S3-key относительно префикса, RU-подпись для кнопки бота)
FRAMES: Dict[str, Tuple[str, str]] = {
    "rounded": ("frames/exclude.png", "Скруглённое окно"),
    "soft_bars": ("frames/group_2172.png", "Мягкие шторки"),
    "letterbox": ("frames/group_2173.png", "Чёрные полосы"),
}

FRAME_IDS: Tuple[str, ...] = tuple(FRAMES.keys())
FRAME_LABELS_RU: Dict[str, str] = {k: v[1] for k, v in FRAMES.items()}

# Сентинел «без рамки» — бот кладёт его в state, наверх не уезжает.
FRAME_NONE = "none"


def _asset_root() -> Tuple[str, str]:
    bucket = (os.environ.get("FX_ASSETS_S3_BUCKET") or "").strip()
    prefix = (os.environ.get("FX_ASSETS_S3_PREFIX") or "").strip().strip("/")
    return bucket, prefix


def _make_s3_client():
    """Тот же способ подключения, что у f3/asset_picker (MinIO-endpoint, sigv4,
    S3 мимо зарубежного OUTBOUND-прокси)."""
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
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


@lru_cache(maxsize=32)
def _key_exists(bucket: str, key: str) -> bool:
    """Есть ли объект в бакете. Ошибку соединения считаем «нет».

    Проверка обязательна: рамка адресуется ФИКСИРОВАННЫМ ключом (в отличие от
    пулов, которые листятся, — там существование гарантировано листингом). Если
    ключа нет, а мы всё равно положим его в media[], нода не скачает файл и
    провалит ВЕСЬ рендер (`ae_sdk.run_job` → "prepare/download error"). Лучше
    отрендерить без рамки, чем отдать юзеру упавшую джобу.
    """
    try:
        _make_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:  # noqa: BLE001 — любой отказ = ассета нет
        LOGGER.warning("frame asset unavailable s3://%s/%s (%s)", bucket, key, e)
        return False


def reset_cache() -> None:
    """Сбросить кеш существования (тесты / до-заливка ассетов без рестарта)."""
    _key_exists.cache_clear()


def resolve_frame_asset(frame_id: str, *, verify: bool = True) -> Optional[Dict[str, str]]:
    """frame_id -> {"relpath", "url"} для media[] или None.

    None означает «рамку не кладём»: неизвестный id, сентинел none, не
    настроенный FX_ASSETS_S3_BUCKET или отсутствующий на S3 файл.
    """
    fid = (frame_id or "").strip().lower()
    if not fid or fid == FRAME_NONE or fid not in FRAMES:
        return None
    bucket, prefix = _asset_root()
    if not bucket:
        return None
    key = FRAMES[fid][0]
    full_key = (prefix + "/" + key).strip("/")
    if verify and not _key_exists(bucket, full_key):
        return None
    file_name = full_key.rsplit("/", 1)[-1]
    return {
        "relpath": f"media/img/{file_name}",
        "url": f"s3://{bucket}/{full_key}",
    }
