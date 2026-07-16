"""Three-message warm-up chain for the existing public-bot audience.

The chain advances only from inline button callbacks, which makes each funnel
transition measurable and avoids timer-based follow-ups.
"""

from __future__ import annotations

from typing import Final

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CAMPAIGN: Final = "warmup_july_2026"
CALLBACK_PREFIX: Final = "warmup:"

MESSAGE_1: Final = """Бласт больше не может существовать в прежнем виде.

Привет, это Никита, основатель Бласта.

Последние месяцы мы разбирали всю обратную связь о продукте — и позитивную, и особенно неприятную. Вы говорили о синхронизации текста, субтитрах, эффектах и качестве исходников. Мы услышали вас и практически полностью пересобрали генерацию.

Но главным ограничением оставалась экономика продукта. При старой технологии мы не могли дать достаточно роликов по адекватной цене.

Сейчас мы нашли решение.

Оно позволяет увеличить пакет с 15 до 100 роликов, а в будущем — открыть бесплатный тестовый режим: безлимитную генерацию контента под один выбранный трек.

Так каждый сможет бесплатно проверить решение на своей музыке и увидеть, как оно работает на всём пути — от создания роликов до публикации и анализа результатов.

Если Бласт всё ещё актуален для тебя — нажми кнопку. Расскажу, что именно мы изменили."""

MESSAGE_2: Final = """Самая частая претензия к Бласту была простой: роликов недостаточно, чтобы регулярно вести соцсети.

Для кого-то 15 видео хватало. Но многие прямо говорили: пользоваться сервисом имеет смысл, только если он даёт 100 роликов и больше.

Раньше это казалось невозможным. Генерация слишком сильно зависела от дорогих моделей, а увеличение пакета означало бы увеличение цены.

За последние месяцы мы пересобрали ключевые этапы системы и убрали модели из тех мест, где они не приносили реальной пользы. В результате генерация стала дешевле, стабильнее и предсказуемее — а мы получили больше контроля над исходниками, текстом и монтажом.

Поэтому теперь можем дать не просто больше видео, а достаточно контента для системного ведения аккаунта.

Но 100 роликов — только первая часть изменений."""

MESSAGE_3: Final = """Мы хотим, чтобы Бласт был не просто генератором роликов, а системой для развития артиста в соцсетях.

Поэтому мы обновляем условия подписки:

100 роликов в месяц за 1 990 ₽.

Это предложение действует до запуска сайта. На сайте появятся массовая генерация, автопостинг и аналитика контента: система будет видеть результаты опубликованных роликов и помогать улучшать следующие.

Там же появится отдельный бесплатный тестовый режим. В нём можно будет безлимитно создавать контент под один выбранный трек и проверить весь путь — от генерации до публикации и анализа результатов.

Лирикс-ролики — только начало. Мы строим технологическую основу для новых музыкальных форматов и персонального визуального стиля каждого артиста.

Это следующий этап Бласта, и нам хочется пройти его вместе с тобой. Оставайся с нами — дальше будет больше форматов, больше свободы для экспериментов и больше возможностей дать своей музыке визуальную жизнь.

Посмотреть актуальные тарифы — /packages
Запустить генерацию — /sendtrack"""


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


def callback_progress(current_stage: int, requested_stage: int) -> tuple[bool, int]:
    """Return ``(should_send, repair_stage)`` for an inline transition.

    Receiving a valid stage-N callback proves that Telegram delivered stage
    N-1, even if the DB write after that delivery was interrupted.  In that
    case the handler repairs progress before sending the next message.
    """
    if requested_stage not in (2, 3):
        raise ValueError("warmup callback stage must be 2 or 3")
    current = max(0, int(current_stage))
    if current >= requested_stage:
        return False, current
    return True, max(current, requested_stage - 1)
