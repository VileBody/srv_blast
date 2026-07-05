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
            "🔊 *Звук* — ты загружаешь свой звук: разгон, риз, голос и он играет "
            "в первые секунды, до дропа.\n"
            "На дропе срабатывает вспышка-молния, а сразу после — резкий "
            "визуальный переход."
        ),
        "video": "",
    },
    "object": {
        "text": (
            "🟦 *Объект* — на склейке до дропа в такт влетает фигура.\n"
            "Добавляет ритм и «дорогую» динамику началу ролика."
        ),
        "video": "",
    },
    "effect": {
        "text": (
            "✨ *Эффект* — визуальные FX: хук на дропе, переход между клипами и "
            "цветовой грейд.\n"
            "Самый гибкий вариант — можно скомбинировать под настроение трека."
        ),
        "video": "",
    },
    "motion": {
        "text": (
            "👆 *Движение* — интерактивный engagement-байт.\n"
            "Подсказка «что делать» с помощью движения руки или головы в такт "
            "музыке."
        ),
        "video": "",
    },
    "thought": {
        "text": (
            "💭 *Мысль* — короткая голосовая ИИ-вставка перед дропом.\n"
            "Звучит как «мысль» артиста — добавляет смысл и личный акцент."
        ),
        "video": "",
    },
}

# Stable display order for the category picker.
HOOK_CATEGORY_ORDER: tuple[str, ...] = ("sound", "object", "effect", "motion", "thought")


def hook_intro(key: str) -> Optional[HookIntro]:
    """Return the intro entry for a hook key, or None if unknown."""
    return HOOK_INTROS.get(str(key or "").strip())
