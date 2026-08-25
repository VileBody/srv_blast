"""Вырезка отрезка по ссылке (YouTube) через yt-dlp.

Почему именно так:

* **Качаем только нужный отрезок** (`--download-sections`), а не весь ролик —
  прогрев это 5–15 секунд, тянуть ради них часовое интервью незачем.
* **`--force-keyframes-at-cuts` обязателен.** Без него yt-dlp режет по ближайшему
  ключевому кадру ДО запрошенной точки, и файл начинается на произвольные 0–10с раньше,
  причём узнать эту дельту неоткуда. Для хука, который обязан лечь встык к дропу,
  это неприемлемо. Флаг заставляет перекодировать края — да, промежуточный файл
  теряет в качестве, но следующим шагом мы всё равно гоним нормализацию под AE,
  так что лишний прогон только один.
* **PO-токены.** С 2025 YouTube требует proof-of-origin для большинства клиентов
  и отдаёт SABR-форматы; без токена приходит 403 или пустой список форматов.
  Токены генерирует сторонний сайдкар (bgutil-ytdlp-pot-provider), адрес которого
  передаётся сюда через env — сам yt-dlp его не умеет.
* **Прокси.** Датацентровый IP ловит бот-детект почти сразу, поэтому
  ``YTDLP_PROXY`` — не опция, а условие работы в проде.

Модуль НЕ решает, можно ли вообще качать: это делает вызывающая сторона по флагу
``EXTERNAL_VIDEO_SOURCE_ENABLED``.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# Поддерживаем только YouTube: у каждого источника свои правила и свой способ
# сломаться, и «поддержать всё, что умеет yt-dlp» = обещание, которое мы не
# сможем сдержать.
_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}

# Больше — и прогрев начнёт заметно удлинять рендер (окно клипа едет назад на всю
# длину вырезки). Совпадает с потолком нормализации.
MAX_SECTION_SEC: float = 15.0
MIN_SECTION_SEC: float = 1.0

DEFAULT_TIMEOUT_S: float = 300.0


class ExternalFetchError(RuntimeError):
    """Не смогли достать отрезок (сеть, формат, приватное видео и т.п.)."""


class ExternalFetchBlocked(ExternalFetchError):
    """YouTube опознал нас как бота / потребовал PO-токен.

    Отдельный класс, потому что реакция другая: это не «ссылка плохая», а
    «инфраструктура сдулась» — нужен прокси/токен-провайдер, и это повод для
    алерта, а не для совета юзеру поправить ссылку.
    """


@dataclass(frozen=True)
class ExternalClipRequest:
    url: str
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return float(self.end_sec) - float(self.start_sec)


def external_source_enabled() -> bool:
    return (os.environ.get("EXTERNAL_VIDEO_SOURCE_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def is_supported_url(url: str) -> bool:
    """True для ссылок на YouTube-видео (watch / youtu.be / shorts / embed)."""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return (parsed.netloc or "").lower() in _YOUTUBE_HOSTS


def video_id(url: str) -> str:
    """Идентификатор ролика — для логов, кеш-ключей и имён файлов. "" если не наш."""
    parsed = urlparse(str(url or "").strip())
    host = (parsed.netloc or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return ""
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.lstrip("/").split("/")[0]
    path = parsed.path or ""
    for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
        if path.startswith(prefix):
            return path[len(prefix):].split("/")[0]
    return (parse_qs(parsed.query).get("v") or [""])[0]


def validate_request(req: ExternalClipRequest) -> None:
    """Проверки, которые дешевле сделать до сети."""
    if not is_supported_url(req.url):
        raise ExternalFetchError("поддерживаются только ссылки на YouTube")
    if not video_id(req.url):
        raise ExternalFetchError("в ссылке не нашёлся id ролика")
    if req.start_sec < 0:
        raise ExternalFetchError("начало отрезка не может быть отрицательным")
    if req.duration_sec < MIN_SECTION_SEC:
        raise ExternalFetchError(
            f"отрезок слишком короткий ({req.duration_sec:.1f}с), "
            f"нужно ≥{MIN_SECTION_SEC:.0f}с"
        )
    if req.duration_sec > MAX_SECTION_SEC:
        raise ExternalFetchError(
            f"отрезок слишком длинный ({req.duration_sec:.1f}с), "
            f"максимум {MAX_SECTION_SEC:.0f}с"
        )


def build_command(req: ExternalClipRequest, *, out_path: Path) -> list[str]:
    """Полная командная строка yt-dlp. Вынесена отдельно — её удобно проверить
    тестом, не выходя в сеть."""
    ytdlp_bin = (os.environ.get("YTDLP_BIN") or "yt-dlp").strip() or "yt-dlp"
    cmd = [
        ytdlp_bin,
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        # Кадр вертикальный, апскейл выше 1080 смысла не имеет, а вес и время
        # скачивания растут.
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--download-sections", f"*{float(req.start_sec):.3f}-{float(req.end_sec):.3f}",
        # Без этого начало отрезка уезжает к предыдущему ключевому кадру.
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", str(out_path),
    ]

    proxy = (os.environ.get("YTDLP_PROXY") or "").strip()
    if proxy:
        cmd += ["--proxy", proxy]

    cookies = (os.environ.get("YTDLP_COOKIES_FILE") or "").strip()
    if cookies:
        cmd += ["--cookies", cookies]

    # Сайдкар-генератор PO-токенов (bgutil). Плагин сам ходит на этот адрес;
    # без него YouTube отдаёт 403 / пустой список форматов.
    pot = (os.environ.get("YTDLP_POT_BASE_URL") or "").strip()
    if pot:
        cmd += ["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot}"]

    extra = (os.environ.get("YTDLP_EXTRA_ARGS") or "").strip()
    if extra:
        cmd += extra.split()

    cmd += ["--", str(req.url)]
    return cmd


# Подписи ошибок, по которым видно, что нас опознали как бота, а не что ссылка
# кривая. Держим списком: формулировки YouTube меняет, а реакция одна.
_BLOCKED_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "po token",
    "proof of origin",
    "http error 403",
    "unable to download api page",
    "requested format is not available",
    "this content isn",  # "isn't available" — типичный ответ на бан по IP
)


def _classify(stderr: str) -> ExternalFetchError:
    low = (stderr or "").lower()
    for marker in _BLOCKED_MARKERS:
        if marker in low:
            return ExternalFetchBlocked(
                "YouTube не отдал видео (бот-детект / PO-токен). "
                "Нужны рабочие прокси и токен-провайдер."
            )
    tail = (stderr or "").strip()[-500:]
    return ExternalFetchError(f"yt-dlp не смог скачать отрезок: {tail}")


def fetch_section(
    req: ExternalClipRequest,
    *,
    work_dir: Path,
    timeout_s: float | None = None,
    runner=subprocess.run,
) -> Path:
    """Скачать [start, end] и вернуть путь к файлу. Нормализацию под AE делает
    вызывающая сторона (``mlcore.media.video_normalize``)."""
    validate_request(req)

    work_dir = Path(work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / f"yt_{video_id(req.url) or 'clip'}.mp4"

    cmd = build_command(req, out_path=out_path)
    if timeout_s is None:
        try:
            timeout_s = float((os.environ.get("YTDLP_TIMEOUT_S") or "").strip() or DEFAULT_TIMEOUT_S)
        except ValueError:
            timeout_s = DEFAULT_TIMEOUT_S

    logger.info(
        "external_video fetch id=%s window=%.2f..%.2f proxy=%s pot=%s",
        video_id(req.url), req.start_sec, req.end_sec,
        bool((os.environ.get("YTDLP_PROXY") or "").strip()),
        bool((os.environ.get("YTDLP_POT_BASE_URL") or "").strip()),
    )

    try:
        proc = runner(cmd, capture_output=True, timeout=timeout_s)
    except FileNotFoundError as e:
        raise ExternalFetchError(
            f"yt-dlp не найден ({cmd[0]!r}) — поставь его в образ оркестратора "
            "или задай YTDLP_BIN"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ExternalFetchError(f"yt-dlp не уложился в {timeout_s:.0f}с") from e

    if int(getattr(proc, "returncode", 1)) != 0:
        stderr = (getattr(proc, "stderr", b"") or b"")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise _classify(stderr)

    # yt-dlp мог склеить в другой контейнер, несмотря на merge-output-format.
    if not out_path.exists():
        siblings = sorted(work_dir.glob(f"{out_path.stem}.*"))
        if not siblings:
            raise ExternalFetchError("yt-dlp отработал, но файла нет")
        out_path = siblings[0]
    if out_path.stat().st_size <= 0:
        raise ExternalFetchError("yt-dlp отдал пустой файл")

    logger.info("external_video fetched path=%s bytes=%d", out_path.name, out_path.stat().st_size)
    return out_path
