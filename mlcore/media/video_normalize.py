"""F6 «Прогрев видео»: нормализация видео под AE (общий модуль).

Живёт в mlcore, потому что нужен обоим концам: боты нормализуют то, что прислал
юзер, а оркестратор — то, что вытащил с YouTube. Раньше лежал в
``services/tg_bot_*/video_prepare.py``; те модули остались тонкими ре-экспортами.

Зачем нормализовать, а не слать как есть: After Effects на рендер-ноде не
откроет то, что чаще всего приезжает из Telegram и с YouTube — VP9/AV1/webm,
HEVC с айфона, экзотические профили. Один прогон через ffmpeg в H.264 + AAC
(yuv420p) убирает целый класс «джоба упала на ноде» и заодно даёт нам точные
размеры/длительность, по которым build-сторона запекает cover-скейл.

Размеры снимаются ПОСЛЕ транскода: ffmpeg применяет rotation-метадату сам, так
что вертикальное видео с телефона отдаёт уже повёрнутые width/height — то, что
реально увидит AE.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


# Границы прогрева. Верхняя — не только про вес: окно клипа сдвигается назад на
# длину вырезки, то есть каждая секунда прогрева удлиняет рендер.
F6_MIN_VIDEO_SEC: float = 1.0
F6_MAX_VIDEO_SEC: float = 15.0


@dataclass(frozen=True)
class VideoPrepareResult:
    source_path: Path
    output_path: Path
    width: int
    height: int
    duration_sec: float
    size_bytes: int
    trimmed: bool
    has_audio: bool


def _safe_name(name: str) -> str:
    out = []
    for ch in str(name or ""):
        out.append(ch if (ch.isalnum() or ch in {"-", "_", "."}) else "_")
    return "".join(out).strip("_") or "video"


def probe_video(*, ffprobe_bin: str, path: Path) -> tuple[int, int, float, bool]:
    """(width, height, duration_sec, has_audio) по первому видео-потоку.

    has_audio решает, глушить ли трек: под немой вырезкой (gif/animation из
    Telegram) приглушённый трек дал бы просто тишину вместо прогрева.
    """
    cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "stream=index,codec_type,width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError as e:
        # Отдельная ветка: без ffprobe не снять размеры, а без них не запечь
        # cover-скейл. Сообщение должно сразу говорить деплою, чего не хватает.
        raise RuntimeError(
            f"ffprobe не найден ({ffprobe_bin!r}) — поставь ffmpeg в образ бота "
            "или задай FFPROBE_BIN"
        ) from e
    if proc.returncode != 0:
        err_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"ffprobe failed rc={proc.returncode} stderr_tail={err_tail}")
    try:
        data = json.loads((proc.stdout or b"{}").decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe returned non-JSON: {e}") from e

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("в файле нет видео-дорожки")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"ffprobe вернул некорректный размер: {width}x{height}")

    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0.0:
        raise RuntimeError("ffprobe не смог определить длительность")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return width, height, duration, has_audio


def normalize_video_for_ae(
    *,
    src: Path,
    work_dir: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    max_duration_sec: float = F6_MAX_VIDEO_SEC,
    min_duration_sec: float = F6_MIN_VIDEO_SEC,
) -> VideoPrepareResult:
    """Перекодировать вырезку в H.264+AAC mp4 и вернуть её фактические параметры.

    Длиннее max_duration_sec → режем по начало (прогрев не должен растягивать
    рендер); короче min_duration_sec → ошибка, из такого куска хука не выйдет.
    """
    src = src.expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"video source missing: {src}")

    # Длительность исходника нужна до транскода — по ней решаем, режем ли.
    _w_in, _h_in, src_duration, _a_in = probe_video(ffprobe_bin=ffprobe_bin, path=src)
    if src_duration < float(min_duration_sec):
        raise RuntimeError(
            f"видео слишком короткое ({src_duration:.1f}с), нужно ≥{min_duration_sec:.0f}с"
        )
    trimmed = src_duration > float(max_duration_sec)

    work_dir = work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / f"{_safe_name(src.stem)}_ae.mp4"

    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(src),
        "-t", f"{float(max_duration_sec):.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"ffmpeg failed rc={proc.returncode} stderr_tail={err_tail}")
    if not out_path.exists() or out_path.stat().st_size <= 0:
        raise RuntimeError("ffmpeg произвёл пустой файл")

    width, height, duration, has_audio = probe_video(ffprobe_bin=ffprobe_bin, path=out_path)
    return VideoPrepareResult(
        source_path=src,
        output_path=out_path,
        width=width,
        height=height,
        duration_sec=duration,
        size_bytes=int(out_path.stat().st_size),
        trimmed=trimmed,
        has_audio=has_audio,
    )
