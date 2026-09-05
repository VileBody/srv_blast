"""Телеграм-бот верификации (passwordless-вход).

Активен ТОЛЬКО если в `backend/.env` задан `TELEGRAM_BOT_TOKEN`. Без токена — no-op,
а верификацию в деве двигает мок-фолбэк в `/api/auth/tg-verify`.

Клиент на stdlib `urllib` (в окружении нет сети под pip); если задан `TELEGRAM_PROXY_URL`,
запросы к Bot API идут через него — из контейнера прод-инфраструктуры Telegram напрямую
недоступен. Long-polling `getUpdates` в фоне:
на `/start <token>` подтверждаем токен в auth_store и шлём пользователю ответ.
Токен и username берём из окружения — сами значения кладёт владелец в .env, в коде их нет.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from . import auth_store, tiktok_config

tiktok_config.load_env()  # подтянуть backend/.env в окружение

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "blast808bot").strip().lstrip("@")

_started = False
_start_lock = threading.Lock()


def configured() -> bool:
    return bool(BOT_TOKEN)


def healthcheck() -> None:
    if not configured():
        raise RuntimeError("telegram_auth: TELEGRAM_BOT_TOKEN is not configured")
    result = _api("getMe", {})
    if not result.get("ok"):
        raise RuntimeError("telegram_auth: getMe returned an unsuccessful response")


def deep_link(token: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start={token}"


# Прокси ТОЛЬКО для api.telegram.org. Из контейнера прямой запрос падает с
# `[Errno 101] Network is unreachable`, хотя с хоста проходит: в этой инфраструктуре
# контейнеры ходят в Telegram через прокси на гейтвее (то же самое стоит у всех
# tg-bot-*). Отдельная переменная, а не общий http_proxy/https_proxy, намеренно:
# общий прокси увёл бы туда же выгрузки в S3 и вызовы оркестратора, которым он не
# нужен и вреден. Пусто — ходим напрямую (дев, локальный запуск).
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "").strip()

_opener: urllib.request.OpenerDirector | None = None


def _http() -> urllib.request.OpenerDirector:
    """Опенер для Bot API: с прокси, если он задан, и всегда мимо системного."""
    global _opener
    if _opener is None:
        proxies = {"http": TELEGRAM_PROXY_URL, "https": TELEGRAM_PROXY_URL} if TELEGRAM_PROXY_URL else {}
        _opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return _opener


def _api(method: str, params: dict) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with _http().open(urllib.request.Request(url, data=data), timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send(chat_id: object, text: str, markup: dict | None = None) -> None:
    try:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if markup:
            params["reply_markup"] = json.dumps(markup)
        _api("sendMessage", params)
    except Exception:
        pass


# Сколько поштучных сообщений «Ролик N готов» отправляем, прежде чем перейти на сводку.
# Батч бывает и на 50 роликов — 50 сообщений подряд читаются как спам и приводят к mute бота.
NOTIFY_LIMIT = 5


def _batch_button(app_url: str, project_id: str) -> dict:
    """Кнопка-диплинк на страницу батча: возвращает человека ровно туда, где лежат ролики."""
    return {"inline_keyboard": [[{"text": "Открыть батч", "url": f"{app_url}/app/projects/{project_id}"}]]}


def notify_video_ready(chat_id: object, index: int, total: int, project_id: str, app_url: str) -> None:
    """«Ролик N готов» — по мере рендера, но не больше NOTIFY_LIMIT сообщений на батч."""
    if not configured() or not chat_id or index > NOTIFY_LIMIT:
        return
    _send(chat_id, f"Ролик {index} из {total} готов", _batch_button(app_url, project_id))


def notify_batch_done(chat_id: object, total: int, project_id: str, app_url: str) -> None:
    """Итоговая сводка по батчу — приходит всегда, даже если поштучные были обрезаны."""
    if not configured() or not chat_id:
        return
    _send(chat_id, f"Батч готов: {total} роликов. Можно выкладывать.", _batch_button(app_url, project_id))


def app_url() -> str:
    """Адрес фронта — тот же источник, что у OAuth-возврата."""
    return os.getenv("APP_URL", "http://localhost:5173").rstrip("/")


def _back_button(label: str = "Вернуться на сайт") -> dict:
    """Кнопка возврата в приложение.

    Без неё сообщение «подтверждено» было тупиком: человек уходил в Telegram и должен был
    сам вспомнить про вкладку с сайтом. Это один из главных мест отвала на входе.
    """
    return {"inline_keyboard": [[{"text": label, "url": f"{app_url()}/app"}]]}


def _poll_loop() -> None:
    # webhook и getUpdates взаимоисключающие: если где-то стоял webhook — снимаем, иначе
    # getUpdates молча не получает апдейты (частая причина «верификация не срабатывает»).
    #
    # drop_pending_updates=true — принципиально: с накопленной очередью бот на старте
    # разгребал СТАРЫЕ `/start` с уже протухшими токенами, слал на каждый «ссылка устарела»
    # и только потом доходил до свежего нажатия. Снаружи это и выглядело как «нажми ещё раз».
    try:
        _api("deleteWebhook", {"drop_pending_updates": "true"})
    except Exception:
        pass
    offset = 0
    while True:
        try:
            # allowed_updates: нас интересуют только сообщения, остальное не тянем
            res = _api("getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": json.dumps(["message"])})
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = (msg.get("chat") or {}).get("id")
                if not text.startswith("/start"):
                    continue
                parts = text.split(maxsplit=1)
                token = parts[1].strip() if len(parts) > 1 else ""
                # профиль из Telegram — им заполняем аккаунт при входе без email
                sender = msg.get("from") or {}
                profile = {
                    "name": (sender.get("first_name") or "").strip(),
                    "surname": (sender.get("last_name") or "").strip(),
                    "username": (sender.get("username") or "").strip(),
                }
                _handle_start(chat_id, token, profile)
        except Exception:
            time.sleep(3)  # сеть моргнула — не спамим, ждём и продолжаем


def _handle_start(chat_id: object, token: str, profile: dict[str, Any]) -> None:
    """Ответ на `/start`. Три исхода, и у каждого понятный текст.

    Голый `/start` без токена — нормальная ситуация: так бот открывается из поиска или
    истории, а не по ссылке с сайта. Раньше на это отвечали «ссылка устарела или неверна»,
    и человек жал START снова и снова, пытаясь получить подтверждение.
    """
    if not token:
        _send(chat_id, "Это бот входа в Blast. Открой сайт и нажми «Войти через Telegram» — "
                       "я подтвержу аккаунт автоматически.", _back_button("Открыть Blast"))
        return
    # allow_create=False: по кнопке «Войти» аккаунт не заводим (см. auth_store.confirm_token)
    result = auth_store.confirm_token(token, chat_id, profile, allow_create=False)
    if result == "ok":
        _send(chat_id, "✅ Аккаунт Blast подтверждён. Возвращайтесь на сайт.", _back_button())
        return
    if result == "no_account":
        _send(chat_id, "Аккаунта Blast с этим Telegram ещё нет. Нажми «Зарегистрироваться» — "
                       "это минута, имя спросим один раз.",
              {"inline_keyboard": [[{"text": "Зарегистрироваться", "url": f"{app_url()}/register"}]]})
        return
    _send(chat_id, "Ссылка устарела — запроси новую на сайте.", _back_button("Открыть Blast"))


def ensure_started() -> None:
    """Идемпотентно поднять фоновый long-polling (только если задан токен)."""
    global _started
    if not configured():
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_poll_loop, name="tg-verify-bot", daemon=True).start()
