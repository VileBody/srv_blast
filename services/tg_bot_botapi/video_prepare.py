"""Тонкий ре-экспорт общей нормализации видео (см. mlcore/media/video_normalize).

Модуль оставлен на месте, чтобы не переписывать импорты бота и его тесты. Сама
логика переехала в mlcore, потому что тот же транскод нужен оркестратору для
вырезок с YouTube: два независимых ffmpeg-профиля разъехались бы по параметрам,
и AE на ноде открыл бы одно и не открыл другое.
"""
from __future__ import annotations

from mlcore.media.video_normalize import (  # noqa: F401
    F6_MAX_VIDEO_SEC,
    F6_MIN_VIDEO_SEC,
    VideoPrepareResult,
    normalize_video_for_ae,
    probe_video,
)

__all__ = [
    "F6_MAX_VIDEO_SEC",
    "F6_MIN_VIDEO_SEC",
    "VideoPrepareResult",
    "normalize_video_for_ae",
    "probe_video",
]
