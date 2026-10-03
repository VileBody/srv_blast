"""Ссылка «на сайт» из публичного бота: подписи, вопрос в Telegram и ссылка «новая в боте».

Привязка Telegram к уже открытому на сайте аккаунту (вход через Google) раньше шла по
одному клику на сайте: подсунь человеку свою ссылку — и твой Telegram приклеен к его
аккаунту. Теперь сайт называет, КАКОЙ Telegram будет привязан, а саму привязку
подтверждает владелец этого Telegram кнопкой в боте (колбэк — services/tg_bot_public/app.py
`_handle_web_link_decision`, состояние — общая таблица `web_link_requests`).
"""
from __future__ import annotations

from typing import Any

from services.tg_bot_public import marketing_texts as mt

from . import funnel, telegram_bot

# Сколько живёт запрос на подтверждение в Telegram: человек как раз держит сайт открытым.
LINK_REQUEST_TTL_S = 10 * 60
# Ключ сессии: запрос привязки принадлежит браузеру, который его завёл.
SESSION_KEY = "handoff_link_request"

LINK_CONFIRM_BUTTON = "Привязать"
LINK_REJECT_BUTTON = "Это не я"


def bot_relogin_url() -> str:
    """Кнопка «Получить новую ссылку в боте»: бот по /start site_login сразу шлёт свежую."""
    return f"https://t.me/{funnel.PUBLIC_BOT_USERNAME}?start={mt.WEB_SITE_LOGIN_START}"


def telegram_label(profile: dict[str, Any] | None) -> dict[str, str]:
    """@username и имя Telegram из ссылки — их показываем в вопросах на сайте."""
    profile = profile or {}
    username = str(profile.get("username") or "").strip().lstrip("@")
    name = " ".join(
        part for part in (str(profile.get("name") or "").strip(), str(profile.get("surname") or "").strip()) if part
    )
    return {"username": f"@{username}" if username else "", "name": name}


def mask_email(email: str) -> str:
    """lena.beats@gmail.com → le***@gmail.com: владелец узнает, постороннему не раскрываем."""
    email = str(email or "").strip()
    local, sep, domain = email.partition("@")
    if not sep or not local or not domain:
        return ""
    return f"{local[:2]}***@{domain}"


def account_label(user: dict[str, Any]) -> str:
    """Чем назвать аккаунт сайта в вопросе в Telegram (уходит владельцу Telegram)."""
    email = str(user.get("email") or user.get("googleEmail") or "")
    return mask_email(email) or str(user.get("name") or "").strip() or "без имени"


def confirm_text(user: dict[str, Any]) -> str:
    return (
        f"Привязать этот Telegram к аккаунту {account_label(user)} на сайте Blast?\n\n"
        "Жми «Привязать», только если сам сейчас входишь на сайт. Если нет — «Это не я»: "
        "ничего не привяжется, доступа к твоему Telegram никто не получит."
    )


def confirm_markup(request_id: str) -> dict[str, Any]:
    prefix = mt.WEB_LINK_CALLBACK_PREFIX
    return {"inline_keyboard": [[
        {"text": LINK_CONFIRM_BUTTON, "callback_data": f"{prefix}y:{request_id}"},
        {"text": LINK_REJECT_BUTTON, "callback_data": f"{prefix}n:{request_id}"},
    ]]}


def send_confirmation(tg_id: int, request_id: str, user: dict[str, Any]) -> telegram_bot.SendResult:
    """Вопрос в тот самый чат, чей Telegram привязываем, — от публичного бота (его колбэк)."""
    return telegram_bot.deliver(int(tg_id), confirm_text(user), confirm_markup(request_id), via="public")
