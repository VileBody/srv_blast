"""Рамки (PNG-маска 1080×1920 поверх всех слоёв ролика) — тот же каталог, что у бота.

Источник правды — `mlcore/hooks/frames/catalog.py` (бот и рендер): id уходит в оркестратор
как `frame_id`, рендер сам резолвит ассет. Здесь — копия ids/файлов для сайта (веб-бэк не
тянет mlcore), её совпадение с каталогом рендера держит тест `test_web_frames.py`.

Превью на проде — сам PNG из бакета ассетов (`<FX_ASSETS_S3_PREFIX>frames/<file>`, тот же
бакет, что `S3_BUCKET_ASSET_STORAGE`, — так его настраивает деплой), в моке — демо-SVG.
"""
from __future__ import annotations

# id -> (файл в fx_assets/frames/, подпись RU, подпись EN)
FRAMES: dict[str, tuple[str, str, str]] = {
    "rounded": ("exclude.png", "Скруглённое окно", "Rounded window"),
    "soft_bars": ("group_2172.png", "Мягкие шторки", "Soft bars"),
    "letterbox": ("group_2173.png", "Чёрные полосы", "Letterbox"),
}
FRAME_IDS: tuple[str, ...] = tuple(FRAMES)


def validate_frame(value: object) -> str | None:
    """id рамки из правок стола: None/"" — без рамки, неизвестный id — явная ошибка."""
    if value in (None, ""):
        return None
    frame = str(value)
    if frame not in FRAMES:
        raise ValueError(f"Неизвестная рамка «{frame}» — выбери её заново на столе")
    return frame
