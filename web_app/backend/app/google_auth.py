"""Вход через Google (OAuth 2.0, authorization code flow).

Второй способ входа рядом с Telegram: западная аудитория телеграмом почти не пользуется,
и для неё Google — то, что снимает барьер на первом экране.

Что важно про безопасность этой схемы:
- `client_secret` живёт только в окружении и уходит ТОЛЬКО на сервер Google (шаг обмена кода).
  В браузер он не попадает никогда, поэтому знания одного `code` для подделки входа мало.
- `state` — случайная строка, которую кладём в сессию перед редиректом и сверяем на возврате.
  Без неё чужой сайт мог бы подсунуть свой код и привязать наш сеанс к своему аккаунту.
- Скоупы минимальные: `openid email profile`. Они НЕ считаются чувствительными, поэтому
  ревью приложения в Google не требуется — только заполненный consent screen.

Зависимостей не добавляем: обмен кода — обычный HTTPS-запрос на stdlib `urllib`, как в
телеграм-боте. `id_token` приходит напрямую с сервера Google по TLS, поэтому подпись не
проверяем — источник и так доверенный (для code flow это допустимо по документации Google).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import tiktok_config  # ради load_env(): .env общий на все интеграции

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile"


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def configured(self) -> bool:
        """Без ключей кнопку входа не показываем вовсе — она бы вела в никуда."""
        return bool(self.client_id and self.client_secret)


def load() -> GoogleConfig:
    tiktok_config.load_env()
    return GoogleConfig(
        client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        # Должен совпадать с консолью Google буква в букву. Google требует https везде,
        # кроме http://localhost — до постоянного домена работает только локальный адрес.
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8001/api/auth/google/callback"),
    )


def authorize_url(state: str) -> str:
    cfg = load()
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # consent + offline не нужны: refresh-токен нам не требуется, мы не ходим
        # в Google после входа. Просим только идентификацию.
        "access_type": "online",
        "prompt": "select_account",
        "include_granted_scopes": "true",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class GoogleAuthError(RuntimeError):
    pass


def exchange_code(code: str) -> dict[str, Any]:
    """Обменять одноразовый код на токены. Запрос идёт сервер-серверу, с секретом."""
    cfg = load()
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": cfg.redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — наружу отдаём одну понятную ошибку
        raise GoogleAuthError(f"Не удалось обменять код Google: {exc}") from exc


def _decode_id_token(id_token: str) -> dict[str, Any]:
    """Достать полезную нагрузку JWT без проверки подписи.

    Токен получен ПРЯМО от Google по TLS в ответ на серверный запрос с секретом — это и есть
    гарантия подлинности. Проверка подписи нужна, когда токен приходит от клиента.
    """
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # base64url без паддинга
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GoogleAuthError("Google вернул нечитаемый id_token") from exc


def profile_from_tokens(tokens: dict[str, Any]) -> dict[str, Any]:
    """Почта и имя из id_token. Непроверенная почта — отказ.

    `email_verified: false` бывает у аккаунтов корпоративных доменов без подтверждения:
    пускать по такой почте нельзя — иначе чужой человек заведёт аккаунт на твою почту.
    """
    claims = _decode_id_token(tokens.get("id_token") or "")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("Google не вернул почту")
    if claims.get("email_verified") is False:
        raise GoogleAuthError("Почта Google не подтверждена")
    return {
        "email": email,
        "name": (claims.get("given_name") or "").strip(),
        "surname": (claims.get("family_name") or "").strip(),
        "avatarUrl": (claims.get("picture") or "").strip() or None,
        "googleSub": claims.get("sub"),
    }
