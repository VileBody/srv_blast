"""Inspect and normalize uploaded bytes locally; never fetch user supplied URLs."""
from __future__ import annotations
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_ACCOUNT_BYTES = 2 * 1024 * 1024 * 1024
MAX_ACCOUNT_FILES = 50

# Рендер всё равно собирает 1080x1920, поэтому исходник больше рамки 1920x1080 (по длинной /
# короткой стороне) только тормозит. Раньше 4K перекодировалось в 4K пресетом `fast` в 2 потока:
# 20 с вертикального 4K шли ~100 с, а всё, что длиннее, упиралось в таймаут ffmpeg (120 с) или
# в proxy_read_timeout nginx (300 с) — загрузка с ПК «висела на 100%» и падала.
MAX_LONG_SIDE = 1920
MAX_SHORT_SIDE = 1080
# Ориентация берётся после автоповорота (ffmpeg применяет rotation до фильтров), поэтому
# рамка корректна и для телефонного 1920x1080+rotate=90.
_FIT_FILTER = (
    f"scale=w='if(gte(iw,ih),min({MAX_LONG_SIDE},iw),min({MAX_SHORT_SIDE},iw))'"
    f":h='if(gte(iw,ih),min({MAX_SHORT_SIDE},ih),min({MAX_LONG_SIDE},ih))'"
    ":force_original_aspect_ratio=decrease:force_divisible_by=2"
)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name) or default))
    except ValueError:
        return default


# Запрос синхронный: перекодирование должно уложиться раньше proxy_read_timeout (300 с),
# иначе юзер получает безликий 504 вместо внятной ошибки, а файл тихо доезжает позже.
TRANSCODE_TIMEOUT_S = _env_int("BLAST_UPLOAD_TRANSCODE_TIMEOUT", 240)
FFMPEG_THREADS = _env_int("BLAST_UPLOAD_FFMPEG_THREADS", 2)


def _run(args: list[str], timeout: int = 120) -> bytes:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("Для загрузки медиа на сервере нужны ffmpeg и ffprobe") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Обработка файла заняла слишком много времени. Загрузите ролик покороче "
                         "или в меньшем разрешении (достаточно 1080p).") from exc
    if proc.returncode:
        raise ValueError("Не удалось прочитать медиафайл. Загрузите исправный MP4 или аудиофайл.")
    return proc.stdout


def _display_size(video: dict[str, Any]) -> tuple[int, int]:
    """Ширина/высота ТАК, КАК ролик видит зритель.

    Телефоны пишут вертикальное видео кадром 1920x1080 плюс поворот в метаданных
    (side_data_list.rotation или tags.rotate). Без учёта поворота проверка формата
    считала такой файл 16:9 и отклоняла честный вертикальный исходник.
    """
    width, height = int(video.get('width') or 0), int(video.get('height') or 0)
    rotation = 0.0
    for side in video.get('side_data_list') or []:
        if side.get('rotation') is not None:
            try:
                rotation = float(side['rotation'])
            except (TypeError, ValueError):
                rotation = 0.0
    if not rotation:
        try:
            rotation = float((video.get('tags') or {}).get('rotate') or 0)
        except (TypeError, ValueError):
            rotation = 0.0
    if round(abs(rotation)) % 180 == 90:
        width, height = height, width
    return width, height


def probe(path: Path) -> dict[str, Any]:
    raw = json.loads(_run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-show_streams', '-show_format', '-of', 'json', str(path)], 30))
    video = next((s for s in raw['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    audio = any(s.get('codec_type') == 'audio' for s in raw['streams'])
    duration = float(raw.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration < 0.5 or duration > 600:
        raise ValueError("Длительность файла должна быть от 0,5 до 600 секунд")
    width, height = _display_size(video) if video else (0, 0)
    return {'width': width, 'height': height, 'duration': duration, 'hasAudio': audio}


def normalize(src: Path, workdir: Path, *, video: bool, expected_format: str | None = None) -> tuple[Path, dict[str, Any]]:
    """Проверить и перекодировать файл `src`; результат пишется в `workdir`.

    Работаем с путями, а не с bytes: 200 МБ исходника + результат в памяти на каждую
    параллельную загрузку раздували единственный воркер uvicorn.
    """
    size = src.stat().st_size if src.exists() else 0
    if not size or size > MAX_SOURCE_BYTES:
        raise ValueError("Файл должен быть непустым и не больше 200 МБ")
    meta = probe(src)
    if video and (min(meta['width'], meta['height']) < 360 or max(meta['width'], meta['height']) > 4096):
        raise ValueError("Разрешение видео: минимум 360 пикселей по короткой стороне, максимум 4096 по длинной")
    if not video and not meta['hasAudio']:
        raise ValueError("В файле нет звуковой дорожки")
    if expected_format:
        ratios = {'9:16': 9/16, '16:9': 16/9, '4:3': 4/3, '1:1': 1.0}
        if expected_format not in ratios or not meta['height']:
            raise ValueError("Неизвестный формат видео")
        if abs(meta['width']/meta['height']/ratios[expected_format]-1) > 0.02:
            raise ValueError(f"Этот батч имеет формат {expected_format}. Загрузите видео того же формата.")
        if meta['duration'] < 1.0:
            raise ValueError("Исходник должен длиться минимум 1 секунду")
    dst = workdir/('normalized.mp4' if video else 'normalized.wav')
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(src)]
    if video:
        args += ['-map', '0:v:0', '-map', '0:a:0?', '-vf', _FIT_FILTER,
                 '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                 '-c:a', 'aac', '-movflags', '+faststart', '-threads', str(FFMPEG_THREADS)]
    else:
        args += ['-vn', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le']
    _run(args + ['-y', str(dst)], TRANSCODE_TIMEOUT_S)
    out_size = dst.stat().st_size
    if out_size > MAX_SOURCE_BYTES:
        raise ValueError("Обработанный файл превышает 200 МБ")
    meta = probe(dst)
    if expected_format: meta['format'] = expected_format
    meta['bytes'] = out_size
    return dst, meta
