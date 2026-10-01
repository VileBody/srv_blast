"""Шрифты субтитров для превью сайта — те же файлы, что ставятся на рендер-ноду.

Превью рисует субтитры ТОЛЬКО настоящим шрифтом каталога: подменять похожим нельзя
(у подмены другие ширины, и строки/фокус-слова разъезжаются с роликом). Поэтому сайт
сам раздаёт файлы шрифтов, а не надеется, что они установлены у человека.

Где лежат:
  прод — S3, `$FX_ASSETS_S3_BUCKET/$FX_ASSETS_S3_PREFIX/fonts/subtitles/<PostScript>.woff2`
         (заливка: scripts/upload_subtitle_fonts_to_s3.py; бакет — как у рамок);
  мок  — локальная папка `WEB_SUBTITLE_FONTS_DIR` (по умолчанию web_app/backend/.fonts),
         туда же складывает woff2 тот же скрипт с `--out-dir`.

Раздаём через свой бэк (`/api/wizard/subtitle-font/<ps>.woff2`), а не подписанной ссылкой
S3: @font-face с чужого домена требует CORS на бакете, а свой домен — нет.
Шрифт, которого нет в хранилище, в каталоге фронта без файла — превью явно пишет, что он
не загружен, и ничего не рисует (No Fallback).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from . import subtitle_text

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
FONT_EXT = ".woff2"
CONTENT_TYPE = "font/woff2"
_LIST_TTL_S = 300.0
_lock = threading.Lock()
# at=None — листинга ещё не было (не 0.0: monotonic() — время с загрузки машины, и на свежей
# машине «0» выглядел бы свежим кэшем)
_listing: dict[str, Any] = {"at": None, "sizes": {}}
_bytes: dict[str, tuple[int, bytes]] = {}


def required_fonts() -> list[str]:
    """Все шрифты, которыми превью может рисовать: выбираемые в каталоге (основной/акцент),
    стандартные шрифты стилей, зафиксированный шрифт brat с курсивами фокус-слова и шрифты
    тайтлов Kant."""
    eng = subtitle_text.engine()
    names: list[str] = []
    for ps, row in eng.load_catalog().items():
        if row.get("roles"):
            names.append(ps)
    names.extend(eng.STYLE_DEFAULT_FONTS.values())
    names.append(eng.BRAT_FONT)
    names.extend(font for font, _faux in eng.BRAT_FOCUS_STYLES.values() if font)
    names.extend(subtitle_text.kant_fonts())   # тайтлы Kant: свои шрифты шаблонов
    return sorted(set(names))


def _prefix() -> str:
    base = (os.environ.get("FX_ASSETS_S3_PREFIX") or "fx_assets/").strip().strip("/")
    return f"{base}/fonts/subtitles" if base else "fonts/subtitles"


def local_dir() -> Path:
    return Path(os.environ.get("WEB_SUBTITLE_FONTS_DIR") or (_BACKEND_ROOT / ".fonts"))


def _s3_target(asset_bucket: str) -> tuple[str, str]:
    bucket = (os.environ.get("FX_ASSETS_S3_BUCKET") or "").strip() or asset_bucket
    return bucket, _prefix()


def available(*, production: bool, s3: Any = None, asset_bucket: str = "") -> dict[str, int]:
    """PostScript-имя → размер файла для шрифтов, которые реально лежат в хранилище.
    Листинг S3 кэшируем на 5 минут: каталог шрифтов дёргают на каждом заходе в визард."""
    if not production:
        folder = local_dir()
        if not folder.is_dir():
            return {}
        return {p.stem: p.stat().st_size for p in folder.glob(f"*{FONT_EXT}") if p.is_file()}
    with _lock:
        if _listing["at"] is not None and time.monotonic() - _listing["at"] < _LIST_TTL_S:
            return dict(_listing["sizes"])
    bucket, prefix = _s3_target(asset_bucket)
    sizes: dict[str, int] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get("Contents") or []:
            name = str(obj["Key"]).rsplit("/", 1)[-1]
            if name.endswith(FONT_EXT):
                sizes[name[: -len(FONT_EXT)]] = int(obj["Size"])
    with _lock:
        _listing.update(at=time.monotonic(), sizes=sizes)
    return dict(sizes)


def font_files(*, production: bool, s3: Any = None, asset_bucket: str = "") -> dict[str, str]:
    """Для фронта: ps → URL файла. Версия в URL — размер файла: перезалили шрифт —
    браузер не держит старый из кэша (сам файл отдаём с долгим кэшем)."""
    have = available(production=production, s3=s3, asset_bucket=asset_bucket)
    return {ps: f"/api/wizard/subtitle-font/{ps}{FONT_EXT}?v={have[ps]}" for ps in required_fonts() if ps in have}


def read_font(ps: str, *, production: bool, s3: Any = None, asset_bucket: str = "") -> bytes:
    """Байты шрифта. Неизвестное имя (не из каталога) — KeyError: путь собираем только
    из своего списка, а не из запроса."""
    if ps not in required_fonts():
        raise KeyError(ps)
    if not production:
        path = local_dir() / f"{ps}{FONT_EXT}"
        if not path.is_file():
            raise FileNotFoundError(ps)
        return path.read_bytes()
    with _lock:
        cached = _bytes.get(ps)
    have = available(production=True, s3=s3, asset_bucket=asset_bucket)
    if ps not in have:
        raise FileNotFoundError(ps)
    if cached and cached[0] == have[ps]:
        return cached[1]
    bucket, prefix = _s3_target(asset_bucket)
    data = s3.get_object(Bucket=bucket, Key=f"{prefix}/{ps}{FONT_EXT}")["Body"].read()
    with _lock:
        _bytes[ps] = (len(data), data)
    return data


def reset_cache() -> None:
    with _lock:
        _listing.update(at=None, sizes={})
        _bytes.clear()

