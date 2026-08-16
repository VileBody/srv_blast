"""Конфиг интеграции с TikTok — читается ТОЛЬКО из окружения.

client_secret не хранится в коде и не коммитится: он живёт в `backend/.env` (файл в .gitignore).
Заполнить: скопировать `.env.example` → `.env` и вписать значения из кабинета
developers.tiktok.com → приложение → Credentials.

Как это ложится на продукт (см. RENDER_JOB_SPEC / Трек C):
- Login Kit (OAuth) — подключение аккаунта. Пока аккаунт не подключён, у триала 5 роликов;
  подключил — ролики в рамках одного трека безлимитны (mock_store.video_limit).
- Content Posting API, scope `video.publish` — Direct Post из модалки предпоста (W52…W58).
- Display API, scope `video.list` — аналитика (W48/W60).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_env_loaded = False


def load_env(path: Path = _ENV_FILE) -> None:
    """Прочитать backend/.env в os.environ (один раз).

    Свой мини-парсер вместо python-dotenv: лишняя зависимость ради пяти строк не нужна,
    а ставить пакеты в этом окружении нечем (нет сети). Уже заданные переменные окружения
    не перетираем — прод задаёт их снаружи.
    """
    global _env_loaded
    if _env_loaded or not path.exists():
        _env_loaded = True
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
    _env_loaded = True


@dataclass(frozen=True)
class TiktokConfig:
    client_key: str
    client_secret: str
    redirect_uri: str
    scopes: str

    @property
    def configured(self) -> bool:
        """Готовы ли ключи. Без них включается мок-режим подключения аккаунта."""
        return bool(self.client_key and self.client_secret)


def load() -> TiktokConfig:
    load_env()
    return TiktokConfig(
        client_key=os.getenv("TIKTOK_CLIENT_KEY", ""),
        client_secret=os.getenv("TIKTOK_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:5173/app/profile/tiktok/callback"),
        scopes=os.getenv("TIKTOK_SCOPES", "user.info.basic,video.publish,video.list"),
    )
