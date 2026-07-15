"""Three-message warm-up chain for the existing public-bot audience.

The chain advances only from inline button callbacks, which makes each funnel
transition measurable and avoids timer-based follow-ups.
"""

from __future__ import annotations

from typing import Final

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CAMPAIGN: Final = "warmup_july_2026"
CALLBACK_PREFIX: Final = "warmup:"

MESSAGE_1: Final = """Проект больше не может существовать в текущем состоянии...

Привет, это Никита, основатель Бласта. Мы внимательно собрали обратную связь и за последние месяцы переработали генерацию, субтитры, эффекты и подбор исходников.

Главное: мы нашли способ снизить стоимость генерации и готовим новый формат подписки — 100 роликов в месяц вместо 15, а также будущий безлимит для одного трека.

Если Бласт всё ещё актуален для тебя — расскажу, как это будет работать."""

MESSAGE_2: Final = """Самый частый вопрос в вашей обратной связи — количество роликов в продукте.

Мы перебрали почти все узлы генерации, убрали модели там, где они не давали пользы, и получили больше контроля над исходниками, таймингами склеек и текстом.

За счёт этого мы сможем кратно увеличить число роликов в пакетах, чтобы вы могли регулярно вести аккаунты и показывать людям свои треки.

Но это ещё не всё — следующий шаг про то, во что выльются эти изменения."""

MESSAGE_3: Final = """Во что выльются все нововведения?

Мы вводим подписку на Бласт: 100 роликов за 2 000 ₽ в месяц. Это ограниченное предложение до запуска сайта; затем появятся массовые батчи, автопостинг, аналитика и система эволюции контента.

Актуальные тарифы — /packages, генерация видео — /sendtrack."""


def keyboard_for_next(stage: int, *, is_test: bool) -> InlineKeyboardMarkup | None:
    mode = "test" if is_test else "prod"
    if stage == 1:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Как получить безлимит", callback_data=f"{CALLBACK_PREFIX}{mode}:2"),
        ]])
    if stage == 2:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Что это меняет для меня", callback_data=f"{CALLBACK_PREFIX}{mode}:3"),
        ]])
    return None


def message_for_stage(stage: int) -> str:
    return {1: MESSAGE_1, 2: MESSAGE_2, 3: MESSAGE_3}[stage]
