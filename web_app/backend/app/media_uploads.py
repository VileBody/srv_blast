"""Inspect and normalize uploaded bytes locally; never fetch user supplied URLs."""
from __future__ import annotations
import json
import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_ACCOUNT_BYTES = 2 * 1024 * 1024 * 1024
MAX_ACCOUNT_FILES = 50


def _run(args: list[str], timeout: int = 120) -> bytes:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("Для загрузки медиа на сервере нужны ffmpeg и ffprobe") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Обработка файла превысила допустимое время") from exc
    if proc.returncode:
        raise ValueError("Не удалось прочитать медиафайл. Загрузите исправный MP4 или аудиофайл.")
    return proc.stdout


def probe(path: Path) -> dict[str, Any]:
    raw = json.loads(_run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-show_streams', '-show_format', '-of', 'json', str(path)], 30))
    video = next((s for s in raw['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    audio = any(s.get('codec_type') == 'audio' for s in raw['streams'])
    duration = float(raw.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration < 0.5 or duration > 600:
        raise ValueError("Длительность файла должна быть от 0,5 до 600 секунд")
    return {'width': int(video['width']) if video else 0, 'height': int(video['height']) if video else 0,
            'duration': duration, 'hasAudio': audio}


def normalize(content: bytes, *, video: bool, expected_format: str | None = None) -> tuple[bytes, dict[str, Any]]:
    if not content or len(content) > MAX_SOURCE_BYTES:
        raise ValueError("Файл должен быть непустым и не больше 200 МБ")
    with TemporaryDirectory(prefix='blast-upload-') as folder:
        src = Path(folder)/'input'; src.write_bytes(content)
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
        dst = Path(folder)/('normalized.mp4' if video else 'normalized.wav')
        args = ['ffmpeg', '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(src)]
        if video:
            args += ['-map', '0:v:0', '-map', '0:a:0?', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                     '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                     '-c:a', 'aac', '-movflags', '+faststart', '-threads', '2']
        else:
            args += ['-vn', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le']
        _run(args + ['-y', str(dst)])
        normalized = dst.read_bytes()
        if len(normalized) > MAX_SOURCE_BYTES:
            raise ValueError("Обработанный файл превышает 200 МБ")
        meta = probe(dst)
        if expected_format: meta['format'] = expected_format
        meta['bytes'] = len(normalized)
        return normalized, meta
