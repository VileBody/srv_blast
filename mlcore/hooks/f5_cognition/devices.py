# mlcore/hooks/f5_cognition/devices.py
"""
Каталог 5 устройств F5.

Каждое устройство — это «риторический приём» для TTS-вставки.
Используется:
  1. В промте Stage 1 (см. prompts/system.py) — LLM подставляет «как генерить».
  2. В UI бота — пользователь выбирает кнопку (название + пример).
  3. Для логов и метрик.

Описания специально написаны единообразно, чтобы их можно было автоматически
вставлять в промт и в UI без повторений.
"""
from __future__ import annotations

from dataclasses import dataclass

from mlcore.hooks.f5_cognition.models import F5Device


@dataclass(frozen=True)
class DeviceSpec:
    device: F5Device
    title_ru: str          # короткое имя для кнопки в боте
    concept: str           # 1 предложение для промта
    example_tts: str       # TTS-фраза в примере
    example_track: str     # ответ трека в примере
    when_to_use: str       # 1–2 предложения, для промта
    generation_logic: str  # как Stage 1 должен генерить, для промта


DEVICES: dict[F5Device, DeviceSpec] = {
    F5Device.PUNCHLINE: DeviceSpec(
        device=F5Device.PUNCHLINE,
        title_ru="Панчлайн",
        concept="TTS произносит setup-фразу, трек доставляет панч.",
        example_tts="Я не хотел этого делать, но…",
        example_track="…всё равно сделал",
        when_to_use=(
            "Первая строка трека — резкое признание, констатация или удар. "
            "Лирика провоцирует на «вот сейчас он скажет что-то жёсткое»."
        ),
        generation_logic=(
            "Анализируем первые 2 строки. Генерируем 3–8 слов setup-фразы, "
            "которая подводит к первой строке как к панчу."
        ),
    ),
    F5Device.MISSING_WORD: DeviceSpec(
        device=F5Device.MISSING_WORD,
        title_ru="Пропущенное слово",
        concept="TTS обрывается на паузе, первое слово трека закрывает пропуск.",
        example_tts="Меня зовут…",
        example_track="Sashok",
        when_to_use=(
            "Первое слово трека — имя, объект, эмоция или восклицание. "
            "Должно быть достаточно «весомым» чтобы закрыть фразу."
        ),
        generation_logic=(
            "Генерируем фразу 3–7 слов, которая лексически требует именно "
            "этого слова в конце. TTS заканчивается многоточием."
        ),
    ),
    F5Device.LYRIC_ECHO: DeviceSpec(
        device=F5Device.LYRIC_ECHO,
        title_ru="Эхо",
        concept="TTS заранее произносит ключевую фразу, трек её повторяет.",
        example_tts="тревога, тревога…",
        example_track="тревога, тревога, тревога…",
        when_to_use=(
            "В треке есть запоминающаяся короткая фраза-крючок (2–5 слов), "
            "которая повторяется. Самое универсальное устройство."
        ),
        generation_logic=(
            "Находим катчевую фразу в первых 10с трека. Генерируем TTS, "
            "который её произносит. Эмоция и темп — на усмотрение LLM."
        ),
    ),
    F5Device.QUESTION_TO_TRACK: DeviceSpec(
        device=F5Device.QUESTION_TO_TRACK,
        title_ru="Вопрос к треку",
        concept="TTS задаёт вопрос, первая строка трека буквально отвечает.",
        example_tts="А что если он не вернётся?",
        example_track="Он вернётся",
        when_to_use=(
            "Первая строка трека — утверждение, констатация, прямой ответ, "
            "который можно «развернуть» в вопрос."
        ),
        generation_logic=(
            "Конструируем вопрос (3–8 слов) к первой строке. Вопрос должен "
            "звучать эмоционально нагруженным (риторический, не справочный)."
        ),
    ),
    F5Device.INVERSE_LYRIC: DeviceSpec(
        device=F5Device.INVERSE_LYRIC,
        title_ru="Инверсия",
        concept="TTS произносит противоположное по смыслу.",
        example_tts="Мне больше никто не нужен",
        example_track="Я не могу без тебя",
        when_to_use=(
            "Первая строка имеет ясное смысловое ядро, которое можно "
            "инвертировать. Хорошо для эмоциональных пиковых строк."
        ),
        generation_logic=(
            "Генерируем семантическую инверсию (3–7 слов). Контраст должен "
            "быть очевиден за 0.5с после дропа."
        ),
    ),
}


def get_device(device: F5Device) -> DeviceSpec:
    """Возвращает спецификацию устройства. Бросает KeyError при неизвестном."""
    return DEVICES[device]


def device_block_for_prompt(device: F5Device) -> str:
    """Готовый текстовый блок для подстановки в Stage 1 system prompt."""
    spec = DEVICES[device]
    return (
        f"Устройство: {spec.device.value} — {spec.title_ru}\n"
        f"Концепция: {spec.concept}\n"
        f"Пример: TTS «{spec.example_tts}» → Трек «{spec.example_track}»\n"
        f"Когда подходит: {spec.when_to_use}\n"
        f"Как генерировать: {spec.generation_logic}"
    )
