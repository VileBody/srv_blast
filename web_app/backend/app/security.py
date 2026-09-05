"""CSRF, ограничение частоты и проверка загружаемых файлов.

Все три вещи закрывают дыры, которые видно снаружи:

- **CSRF.** Аутентификация — cookie-сессия, значит любой сторонний сайт мог отправить
  запрос от имени залогиненного пользователя. Схема double-submit: сервер кладёт токен
  в НЕ-HttpOnly cookie, фронт возвращает его заголовком `X-CSRF-Token`, сервер сверяет.
  Чужой домен cookie прочитать не может, поэтому и заголовок подделать не может.
- **Rate-limit.** Вход и загрузки — самые дорогие ручки: первая рассылает сообщения в
  Telegram, вторая пишет файлы на диск. Без ограничения их можно долбить бесконечно.
- **Проверка файлов.** `content_type` приходит от клиента и ничего не гарантирует —
  под видом mp3 можно было залить что угодно. Смотрим на первые байты.

В dev счётчики могут жить в памяти процесса. В production `REDIS_URL` обязателен:
недоступность общего счётчика является ошибкой, а не поводом незаметно ослабить лимиты.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from collections import deque
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

CSRF_COOKIE = "blast_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Ручки, которые вызываются НЕ из браузера пользователя и потому не могут прислать токен:
# возврат OAuth (переход с домена TikTok) и вебхук платёжки (сервер-сервер, своя подпись).
CSRF_EXEMPT_PREFIXES = ("/api/tiktok/callback", "/api/payments/webhook")


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default) == "1"


def csrf_enabled() -> bool:
    return _flag("BLAST_CSRF", "1")


# --------------------------------------------------------------------- CSRF

def issue_csrf_cookie(request: Request, response: Response) -> None:
    """Выдать токен, если его ещё нет. Cookie НЕ HttpOnly — фронт обязан её прочитать."""
    if not csrf_enabled() or request.cookies.get(CSRF_COOKIE):
        return
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        httponly=False,
        secure=cookie_secure(),
        samesite=cookie_samesite(),
        path="/",
        max_age=60 * 60 * 24 * 30,
    )


def _csrf_failure(request: Request) -> JSONResponse | None:
    if not csrf_enabled():
        return None
    path = request.url.path
    if request.method in SAFE_METHODS or not path.startswith("/api/"):
        return None
    if path.startswith(CSRF_EXEMPT_PREFIXES):
        return None
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    # Сравниваем БАЙТЫ: compare_digest на строках с не-ASCII бросает TypeError — заголовок
    # приходит от клиента, и кириллица в нём превращала бы проверку в 500.
    if cookie and header and secrets.compare_digest(cookie.encode(), header.encode()):
        return None
    # code — машинный маркер: фронт по нему обновляет токен и повторяет запрос,
    # а не выкидывает пользователя на логин (это НЕ протухшая сессия).
    return JSONResponse({"detail": "Проверка CSRF не пройдена", "code": "csrf_failed"}, status_code=403)


# --------------------------------------------------------------- rate-limit

class _Bucket:
    """Скользящее окно: помним отметки времени запросов и режем всё сверх лимита.

    Счётчики — в памяти ПРОЦЕССА. Пока инстанс один, этого достаточно; при нескольких
    воркерах каждый считает свой лимит, и суммарный оказывается кратно больше заявленного.
    Поэтому при заданном `REDIS_URL` счёт уезжает в общее хранилище (см. `_redis_hit`).
    """

    def __init__(self, limit: int, window_sec: int, name: str) -> None:
        self.limit = limit
        self.window = window_sec
        self.name = name
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int:
        """Вернуть 0, если можно; иначе — через сколько секунд повторить."""
        shared = _redis_hit(self.name, key, self.limit, self.window)
        if shared is not None:
            return shared
        now = time.monotonic()
        with self._lock:
            marks = self._hits.setdefault(key, deque())
            while marks and now - marks[0] > self.window:
                marks.popleft()
            if len(marks) >= self.limit:
                return max(1, int(self.window - (now - marks[0])) + 1)
            marks.append(now)
            if len(self._hits) > 10_000:  # защита от роста словаря на переборе IP
                self._hits = {k: v for k, v in self._hits.items() if v}
            return 0


# --- общий счётчик на Redis (нужен, когда воркеров больше одного) ------------
#
# Окно здесь фиксированное (ключ на каждый интервал), а не скользящее, как в памяти:
# INCR + EXPIRE — две команды без Lua и без гонок, а разница между окнами для защиты от
# перебора несущественна. Клиент создаётся лениво и один раз на процесс.
_redis_client: Any = None
_redis_broken = False
_redis_lock = threading.Lock()


def _redis() -> Any:
    """Клиент Redis или None в dev; production fails closed."""
    global _redis_client, _redis_broken
    if _redis_broken:
        return None
    if _redis_client is not None:
        return _redis_client
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        if os.getenv("MODE") == "prod":
            raise RuntimeError("security_rate_limit: REDIS_URL is required in production")
        _redis_broken = True
        return None
    with _redis_lock:
        if _redis_client is None:
            try:
                import redis  # локальный импорт: в одноинстансном режиме зависимость не нужна

                _redis_client = redis.Redis.from_url(url, socket_timeout=0.2, socket_connect_timeout=0.2)
                _redis_client.ping()
            except Exception as exc:  # noqa: BLE001
                if os.getenv("MODE") == "prod":
                    raise RuntimeError(f"security_rate_limit: Redis unavailable: {exc}") from exc
                _redis_broken = True
                return None
    return _redis_client


def _redis_hit(name: str, key: str, limit: int, window: int) -> int | None:
    """0 / сколько ждать — либо None, если общего счётчика нет и решает память.

    В dev падение Redis возвращает None и включает память. В production лимиты должны
    оставаться общими между процессами, поэтому сбой Redis является явной ошибкой.
    """
    client = _redis()
    if client is None:
        return None
    now = int(time.time())
    slot = now // window
    try:
        redis_key = f"blast:rl:{name}:{key}:{slot}"
        count = int(client.incr(redis_key))
        if count == 1:
            client.expire(redis_key, window)
        if count > limit:
            return max(1, window - (now % window))
        return 0
    except Exception as exc:  # noqa: BLE001
        if os.getenv("MODE") == "prod":
            raise RuntimeError(f"security_rate_limit: Redis operation failed: {exc}") from exc
        return None


def healthcheck() -> None:
    """Verify the shared limiter store before accepting production traffic."""
    client = _redis()
    if os.getenv("MODE") == "prod" and client is None:
        raise RuntimeError("security_rate_limit: Redis client is unavailable")
    if client is not None:
        client.ping()


def _limit_from_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_AUTH_BUCKET = _Bucket(_limit_from_env("BLAST_RATE_AUTH", 10), 60, "auth")
_UPLOAD_BUCKET = _Bucket(_limit_from_env("BLAST_RATE_UPLOAD", 20), 60, "upload")

# Ручки, где ограничение обязательно: вход шлёт сообщения в Telegram, загрузки пишут на диск
_AUTH_PATHS = ("/api/auth/",)
_UPLOAD_PATHS = (
    "/api/wizard/upload-track",
    "/api/wizard/upload-source",
    "/api/wizard/upload-hook-sound",
    "/api/wizard/upload-hook-video",
    "/api/wizard/upload-link",
    "/api/mobile-upload",
    "/api/profile/avatar",
    "/cover",
)


def rate_limit_enabled() -> bool:
    return _flag("BLAST_RATE_LIMIT", "1")


def _client_key(request: Request) -> str:
    # За обратным прокси реальный адрес приходит в X-Forwarded-For
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_failure(request: Request) -> JSONResponse | None:
    if not rate_limit_enabled():
        return None
    path = request.url.path
    if request.method in SAFE_METHODS:
        return None
    bucket: _Bucket | None = None
    if path.startswith(_AUTH_PATHS):
        bucket = _AUTH_BUCKET
    elif any(part in path for part in _UPLOAD_PATHS):
        bucket = _UPLOAD_BUCKET
    if bucket is None:
        return None
    retry_after = bucket.hit(f"{_client_key(request)}|{'auth' if bucket is _AUTH_BUCKET else 'upload'}")
    if not retry_after:
        return None
    return JSONResponse(
        {"detail": "Слишком много запросов, попробуй позже", "code": "rate_limited"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def guard(request: Request) -> JSONResponse | None:
    """Единая точка входа для middleware: CSRF, затем частота."""
    # Phone upload uses an explicit short-lived capability header, never cookies.
    if request.url.path == "/api/mobile-upload":
        return _rate_failure(request)
    return _csrf_failure(request) or _rate_failure(request)


# ------------------------------------------------------------------- cookies

def cookie_secure() -> bool:
    """В проде cookie обязаны быть Secure. Локально по http это выключается флагом."""
    return _flag("BLAST_COOKIE_SECURE", "0")


def cookie_samesite() -> str:
    """lax | strict | none.

    По умолчанию `lax`, и это осознанно: при `strict` браузер не пришлёт cookie на
    возврате с домена TikTok после OAuth, и подключение аккаунта перестанет работать.
    От межсайтовых запросов защищает CSRF-токен, а не SameSite.
    """
    value = (os.getenv("BLAST_COOKIE_SAMESITE") or "lax").strip().lower()
    return value if value in {"lax", "strict", "none"} else "lax"


# --------------------------------------------------------- проверка файлов

MB = 1024 * 1024

# Первые байты форматов, которые мы принимаем. Проверяем содержимое, а не заявленный тип.
_AUDIO_SIGNATURES: tuple[tuple[int, bytes], ...] = (
    (0, b"ID3"),        # MP3 с тегами
    (0, b"RIFF"),       # WAV (дальше сверяем WAVE)
    (0, b"OggS"),       # OGG / Opus
    (0, b"fLaC"),       # FLAC
    (4, b"ftyp"),       # M4A / MP4-аудио
)
_IMAGE_SIGNATURES: tuple[tuple[int, bytes], ...] = (
    (0, b"\x89PNG\r\n\x1a\n"),
    (0, b"\xff\xd8\xff"),        # JPEG
)


def _matches(content: bytes, signatures: tuple[tuple[int, bytes], ...]) -> bool:
    return any(content[offset:offset + len(sig)] == sig for offset, sig in signatures)


def _is_mp3_frame(content: bytes) -> bool:
    """MP3 без ID3 начинается с синка кадра 0xFFEx."""
    return len(content) > 1 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0


def check_audio(content: bytes, max_mb: int = 40) -> None:
    """Пропустить только настоящий аудиофайл разумного размера."""
    if not content:
        raise HTTPException(status_code=422, detail="Пустой файл")
    if len(content) > max_mb * MB:
        raise HTTPException(status_code=413, detail=f"Файл больше {max_mb} МБ")
    head = content[:16]
    ok = _matches(head, _AUDIO_SIGNATURES) or _is_mp3_frame(head)
    if ok and head[:4] == b"RIFF" and content[8:12] != b"WAVE":
        ok = False
    if not ok:
        raise HTTPException(status_code=422, detail="Нужен аудиофайл: MP3, WAV, M4A, OGG или FLAC")


def check_image(content: bytes, max_mb: int = 8) -> None:
    """Пропустить только PNG или JPEG: обложка и аватар показываются другим людям."""
    if not content:
        raise HTTPException(status_code=422, detail="Пустой файл")
    if len(content) > max_mb * MB:
        raise HTTPException(status_code=413, detail=f"Файл больше {max_mb} МБ")
    if not _matches(content[:16], _IMAGE_SIGNATURES):
        raise HTTPException(status_code=422, detail="Нужен файл PNG или JPEG")


def safe_extension(filename: str | None, allowed: set[str], fallback: str) -> str:
    """Расширение из имени файла — только из белого списка, иначе фолбэк.

    Имя приходит от клиента: без белого списка в него можно было положить `.php`,
    `..` или что угодно ещё и получить запись мимо папки загрузок.
    """
    from pathlib import Path

    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in allowed else fallback


def sanitize_filename(filename: str | None, fallback: str) -> str:
    """Оставить только имя файла без путей и управляющих символов."""
    from pathlib import Path

    name = Path((filename or "").replace("\\", "/")).name.strip()
    cleaned = "".join(char for char in name if char.isprintable() and char not in '<>:"|?*')
    return cleaned[:120] or fallback


# ------------------------------------------------------- гео-ограничения

def geo_country(request: Request) -> str:
    """Код страны запроса из заголовка обратного прокси (по умолчанию Cloudflare).

    Своей GeoIP-базы у нас нет и заводить её ради одной проверки незачем: страну
    проставляет прокси перед приложением. Если заголовка нет, страна неизвестна —
    и это честнее, чем угадывать по Accept-Language.
    """
    header = os.getenv("BLAST_GEO_HEADER", "CF-IPCountry")
    return (request.headers.get(header) or "").strip().upper()


def blocked_google_countries() -> set[str]:
    raw = os.getenv("BLAST_GOOGLE_BLOCKED_COUNTRIES", "RU")
    return {code.strip().upper() for code in raw.split(",") if code.strip()}


def google_allowed(request: Request) -> bool:
    """Можно ли этому запросу предлагать Google.

    Из России подключение Google не показываем: риск штрафов. Проверка мягкая по
    построению — когда страна неизвестна (нет прокси-заголовка), разрешаем. Это
    осознанно: без VPN сервис из России и так недоступен, а с VPN адрес будет не
    российским, так что это страховка, а не барьер.
    """
    country = geo_country(request)
    return not country or country not in blocked_google_countries()


def public_config() -> dict[str, Any]:
    """То, что не стыдно отдать наружу — для диагностики окружения."""
    return {
        "csrf": csrf_enabled(),
        "rateLimit": rate_limit_enabled(),
        "cookieSecure": cookie_secure(),
        "cookieSameSite": cookie_samesite(),
    }
