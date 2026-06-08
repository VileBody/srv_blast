# mlcore/hooks/f5_cognition/prompts/stage1.py
"""
Промты Stage 1 (text mode).

В v1.3:
  - device приходит из бота, всегда задан. Stage 1 НЕ выбирает устройство сам,
    только генерит tts_text + voice spec под уже выбранное.
  - 3–8 слов, expected_duration_ms в окне 2500–3500.
"""
from __future__ import annotations

from mlcore.hooks.f5_cognition.devices import device_block_for_prompt
from mlcore.hooks.f5_cognition.models import F5Device, F5Request


SYSTEM_PROMPT_TEMPLATE = """\
Ты — генератор когнитивных хуков для системы Blast. Твоя задача — создать
2–3.5-секундную голосовую вставку, которая встанет поверх первых секунд
фокусного отрывка музыкального трека и семантически провзаимодействует
с его текстом по заданному устройству.

═══════════════════════════════════════════════════════════════
УСТРОЙСТВО (выбрано пользователем — не меняй)
═══════════════════════════════════════════════════════════════

{device_block}

═══════════════════════════════════════════════════════════════
ПРИНЦИПЫ ГЕНЕРАЦИИ
═══════════════════════════════════════════════════════════════
1. Длина вставки — 3–8 слов, целевая длина 2500–3500 мс. Оптимум 4–6 слов.
2. Вставка работает в связке с треком — не отдельно, а именно как
   выбранное устройство (см. блок выше).
3. Voice persona подбирается уникально под каждый трек — на основе жанра,
   BPM, эмоции лирики, пола/возраста артиста. КАЖДЫЙ РАЗ НОВАЯ ПЕРСОНА:
   две генерации одного и того же трека должны давать разные голоса,
   одинаково подходящие под концепт.
4. Эмоцию голоса выбирай сам — контраст с треком и совпадение оба валидны.
5. Учти язык трека: ru → русская вставка, en → английская. Bilingual —
   язык первой строки.
6. Темп подбирай так, чтобы фраза комфортно влезла в 2.5–3.5с при выбранной
   эмоции. Шёпот = меньше слов, fast/staccato = можно больше.
"""


USER_PROMPT_TEMPLATE = """\
Лирика трека (контекст, первые 30 секунд):
\"\"\"
{lyrics}
\"\"\"
{focus_block}
Метаданные:
- BPM: {bpm}
- Тональность: {key}
- Жанр: {genre}
- Артист: {artist}
- Фокусный отрывок начинается на: {focal_start_ms} мс от начала трека
- Целевая длина TTS: 2500–3500 мс (3–8 слов)
{drop_hint}
Верни ОДИН JSON-объект (без преамбулы, без markdown-обёртки):

{{
  "tts_text": "текст для синтеза (3–8 слов, можно с многоточиями)",
  "voice_persona": "5–10 слов: пол, возраст, тембр, акцент",
  "voice_emotion": "одно из: hype / whisper / robotic / melancholic / hostile / playful / urgent / detached",
  "voice_pacing": "одно из: slow / normal / fast / staccato / rising / falling",
  "expected_duration_ms": 2500-3500,
  "rationale": "1–2 предложения: почему именно этот вариант сработает под устройство"
}}
"""


def build_system_prompt(device: F5Device) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        device_block=device_block_for_prompt(device),
    )


def _first_line(lyrics: str) -> str:
    for line in lyrics.splitlines():
        s = line.strip()
        if s:
            return s
    return lyrics.strip()[:80]


def _first_word(lyrics: str) -> str:
    line = _first_line(lyrics)
    parts = line.split()
    return parts[0] if parts else ""


def build_user_prompt(req: F5Request) -> str:
    meta = req.track_meta
    drop_hint = ""
    if req.drop_at_sec is not None and req.drop_at_sec < 3.0:
        max_ms = int((req.drop_at_sec - 0.15) * 1000)
        drop_hint = (
            f"- ВАЖНО: дроп на {req.drop_at_sec:.2f}с от начала фокуса. "
            f"TTS должен закончиться до {max_ms} мс.\n"
        )

    # Target line: prefer the post-drop line (resolved upstream from ASR
    # word-timings). The TTS must interact with THIS line — not the clip start.
    # Fallback to the first lyric line only when focus_line is absent.
    focus = (req.focus_line or "").strip()
    if focus:
        focus_block = (
            "\n══════════════════════════════════════════════════════════\n"
            "ЦЕЛЕВАЯ СТРОКА (звучит СРАЗУ ПОСЛЕ дропа — взаимодействуй ИМЕННО с ней,\n"
            "по выбранному устройству; начало клипа и первые строки — только фон):\n"
            f'"{focus}"\n'
            "══════════════════════════════════════════════════════════\n"
        )
    else:
        focus_block = (
            f'\nЦелевая строка (начало отрывка): "{_first_line(req.lyrics)}"\n'
            f'Первое слово: "{_first_word(req.lyrics)}"\n'
        )

    return USER_PROMPT_TEMPLATE.format(
        lyrics=req.lyrics.strip()[:2000],
        focus_block=focus_block,
        bpm=meta.bpm if meta.bpm is not None else "—",
        key=meta.key or "—",
        genre=meta.genre or "—",
        artist=meta.artist or "—",
        focal_start_ms=req.focal_start_ms,
        drop_hint=drop_hint,
    )
