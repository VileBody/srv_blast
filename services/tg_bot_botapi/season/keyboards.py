"""Inline keyboards for the season flow.

All callback_data strings are namespaced under `season:` so the dispatcher
in app.py can route them via a single startswith() check.
"""
from __future__ import annotations

from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


CB_INTRO_NEXT = "season:intro:next"
CB_CONSENT_ALL = "season:consent:all"
CB_CONSENT_FINALS = "season:consent:finals"

CB_MENU_GENERATION = "season:menu:generation"
CB_MENU_PRICING = "season:menu:pricing"
CB_MENU_EXAMPLES = "season:menu:examples"
CB_MENU_ABOUT = "season:menu:about"
CB_MENU_INVITE = "season:menu:invite"
CB_MENU_HISTORY = "season:menu:history"
CB_MENU_BACK = "season:menu:back"

CB_WAITLIST_JOIN = "season:waitlist:join"
CB_WAITLIST_SKIP = "season:waitlist:skip"

CB_NOTIF_ALL = "season:notif:all"
CB_NOTIF_FINALS = "season:notif:finals"
CB_NOTIF_OFF = "season:notif:off"


def intro_next_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Дальше →", callback_data=CB_INTRO_NEXT),
    ]])


def consent_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Да, всё", callback_data=CB_CONSENT_ALL)],
        [InlineKeyboardButton(text="✨ Только финалы", callback_data=CB_CONSENT_FINALS)],
    ])


def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Генерация", callback_data=CB_MENU_GENERATION)],
        [InlineKeyboardButton(text="📦 Тарифы", callback_data=CB_MENU_PRICING)],
        [InlineKeyboardButton(text="🎯 Примеры", callback_data=CB_MENU_EXAMPLES)],
        [InlineKeyboardButton(text="📚 О сезоне", callback_data=CB_MENU_ABOUT)],
        [InlineKeyboardButton(text="👥 Пригласить друга", callback_data=CB_MENU_INVITE)],
        [InlineKeyboardButton(text="📜 История генераций", callback_data=CB_MENU_HISTORY)],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад в меню", callback_data=CB_MENU_BACK),
    ]])


def waitlist_kb(*, already_in: bool) -> InlineKeyboardMarkup:
    join_label = "✓ В waitlist'е" if already_in else "Да, в waitlist"
    rows = [
        [
            InlineKeyboardButton(text=join_label, callback_data=CB_WAITLIST_JOIN),
            InlineKeyboardButton(text="Нет", callback_data=CB_WAITLIST_SKIP),
        ],
        [InlineKeyboardButton(text="👥 Пригласить — попасть раньше",
                              callback_data=CB_MENU_INVITE)],
        [InlineKeyboardButton(text="← Назад в меню", callback_data=CB_MENU_BACK)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_kb(referral_link: str) -> InlineKeyboardMarkup:
    """Share buttons that prefill the message via Telegram/WhatsApp.

    Telegram's share URL takes the link + optional text; WhatsApp uses wa.me.
    """
    share_text = quote(
        f"Тестим формулу залетевшего ролика в Blast. Залетай: {referral_link}",
        safe="",
    )
    tg_url = f"https://t.me/share/url?url={quote(referral_link, safe='')}&text={share_text}"
    wa_url = f"https://wa.me/?text={share_text}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Поделиться в TG", url=tg_url)],
        [InlineKeyboardButton(text="📨 Поделиться в WhatsApp", url=wa_url)],
        [InlineKeyboardButton(text="← Назад в меню", callback_data=CB_MENU_BACK)],
    ])


def notifications_kb(*, current: str) -> InlineKeyboardMarkup:
    """Notification settings buttons; mark the current choice with a check."""
    def label(option: str, text: str) -> str:
        return f"✓ {text}" if current == option else text

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label("all", "Получать всё"),
                              callback_data=CB_NOTIF_ALL)],
        [InlineKeyboardButton(text=label("finals_only", "Только финалы"),
                              callback_data=CB_NOTIF_FINALS)],
        [InlineKeyboardButton(text=label("off", "Отписаться"),
                              callback_data=CB_NOTIF_OFF)],
        [InlineKeyboardButton(text="← Назад в меню", callback_data=CB_MENU_BACK)],
    ])


def build_referral_link(bot_username: str, chat_id: int) -> str:
    """Construct the t.me deep-link with `start=ref_<chat_id>` payload."""
    username = (bot_username or "").lstrip("@")
    if not username:
        # Fallback: telegram-resolvable canonical bot link is impossible without
        # a username, so we return a placeholder that humans can replace.
        return f"https://t.me/?start=ref_{chat_id}"
    return f"https://t.me/{username}?start=ref_{chat_id}"
