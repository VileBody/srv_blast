"""Hook intros — shared text descriptions (and, later, example videos) for the
5 hook categories. Imported by both tg_bot_botapi and tg_bot_public so the copy
never drifts.

Each entry: {"text": <markdown description>, "video": <tg file_id or "">}.
Now we ship text only; once example clips are montaged, drop their Telegram
file_id into "video" and the bots automatically switch from a text message to a
video+caption — no flow/handler change (same pattern as _SUBTITLES_EXAMPLE_VIDEO).
"""
from __future__ import annotations

from typing import Optional, TypedDict


class HookIntro(TypedDict):
    text: str
    video: str


# Keys = hook category ids used across the pipeline (hook_category / BTN map).
HOOK_INTROS: dict[str, HookIntro] = {
    "sound": {
        "text": (
            "🔊 *Звук* — ты загружаешь свой звук (разгон, риза, голос) и он "
            "играет в первые секунды, ДО дропа. На дропе срабатывает вспышка-"
            "молния, а сразу после — резкий визуальный переход. Музыка трека на "
            "это время автоматически приглушается, чтобы твой звук было слышно. "
            "Можно приложить текст — он покажется субтитром поверх."
        ),
        "video": "",
    },
    "object": {
        "text": (
            "🟦 *Объект* — на склейках до дропа в кадр в такт влетает выбранная "
            "фигура (ромб, квадрат, звезда или эллипс). На дропе — вспышка-"
            "молния, а после — рандомные резкие переходы между клипами. Добавляет "
            "ритм и «дорогую» динамику началу ролика."
        ),
        "video": "",
    },
    "effect": {
        "text": (
            "✨ *Эффект* — набор визуальных FX, который ты собираешь в 3 шага: "
            "хук на дропе (молния / затвор / слоу-шаттер), переход между клипами "
            "и цветовой грейд. Самый гибкий вариант — можно скомбинировать под "
            "настроение трека."
        ),
        "video": "",
    },
    "motion": {
        "text": (
            "👆 *Движение* — вовлекающая подсказка в такт музыки: рука или голова "
            "морфит в кадре (свайпни / тапни / зумни / задержи палец / качай "
            "головой) и на дропе бьёт вспышка. Цепляет внимание зрителя и "
            "провоцирует досмотр — engagement-байт первых секунд."
        ),
        "video": "",
    },
    "thought": {
        "text": (
            "💭 *Мысль* — короткая голосовая вставка (ИИ-голос) поверх строчки "
            "сразу после дропа, с субтитром в стиле трека. Звучит как «мысль» "
            "артиста — добавляет смысл и личный акцент. Музыка приглушается под "
            "голос и возвращается к дропу."
        ),
        "video": "",
    },
}

# Stable display order for the category picker.
HOOK_CATEGORY_ORDER: tuple[str, ...] = ("sound", "object", "effect", "motion", "thought")


def hook_intro(key: str) -> Optional[HookIntro]:
    """Return the intro entry for a hook key, or None if unknown."""
    return HOOK_INTROS.get(str(key or "").strip())
