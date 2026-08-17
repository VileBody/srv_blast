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

import os
from typing import Dict, Optional, Tuple

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


def resolve_frame_asset(frame_id: str) -> Optional[Dict[str, str]]:
    """frame_id -> {"relpath", "url"} для media[] или None.

    None означает «рамку не кладём»: неизвестный id, сентинел none или не
    настроенный FX_ASSETS_S3_BUCKET.
    """
    fid = (frame_id or "").strip().lower()
    if not fid or fid == FRAME_NONE or fid not in FRAMES:
        return None
    bucket, prefix = _asset_root()
    if not bucket:
        return None
    key = FRAMES[fid][0]
    full_key = (prefix + "/" + key).strip("/")
    file_name = full_key.rsplit("/", 1)[-1]
    return {
        "relpath": f"media/img/{file_name}",
        "url": f"s3://{bucket}/{full_key}",
    }
