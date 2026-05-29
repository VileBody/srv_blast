# mlcore/hooks/f5_cognition/inject.py
"""
F5 → AE JSON injection.

Перехватываем готовые footage_layers / text_layers (после Gemini Stage 2 +
project_builder.build_*) до сборки финального payload и добавляем:

  1. Audio-слой с TTS-файлом (см. inject_audio_layer)
  2. Text-слой с TTS-фразой в окне focal_start..focal_start+tts_duration
     (см. inject_subtitle_layer) — клонирует существующий стиль субтитров
  3. (заглушка) Volume keyframes / static gain для ducking основного трека
     (inject_track_duck — сейчас no-op, см. чат)

Схема ключей подсмотрена из реального render.jsx:
  C:/Users/User/Desktop/055060bb7d064bf98ca45517600bb84f/app/render.jsx
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from mlcore.hooks.f5_cognition.models import F5Response

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Константы инжекции
# ─────────────────────────────────────────────────────────────────────────────

# Подсмотрено в render.jsx: трек-аудио "реф" имеет z_index=2, видеоклипы 100+.
# TTS ставим между — слышен поверх трека, не конфликтует с видео.
F5_AUDIO_Z_INDEX = 5

# Лёгкая задержка TTS от старта focal-окна (мс). Совпадает с TTS_OFFSET_MS в mixer.
F5_TTS_OFFSET_MS = 100

# Имена композиций (подсмотрено в render.jsx)
DEFAULT_AUDIO_COMP = "Comp 1"
DEFAULT_TEXT_COMP = "Текст"

# Envelope для TTS
F5_AUDIO_ENVELOPE = {
    "fade_in_s": 0.05,
    "fade_out_s": 0.10,
    "min_db": -48.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Audio-слой
# ─────────────────────────────────────────────────────────────────────────────

def inject_audio_layer(
    footage_layers: list[dict[str, Any]],
    f5: F5Response,
    *,
    focal_start_ms: int,
    tts_remote_url: str | None = None,
    tts_local_path: str | None = None,
    target_comp_name: str = DEFAULT_AUDIO_COMP,
) -> list[dict[str, Any]]:
    """
    Добавляет в footage_layers ещё один элемент — TTS audio.
    Схема скопирована с реального "реф" audio-слоя в render.jsx.

    Args:
        footage_layers: текущий список слоёв.
        f5: ответ F5 pipeline (audio_duration_ms).
        focal_start_ms: где на ленте трека начинается focal — туда же ляжет TTS.
        tts_remote_url: S3 URL загруженного .wav. Если задан — file_path=""+remote_url.
        tts_local_path: локальный путь .wav (для тестов без S3).
        target_comp_name: куда положить (обычно "Comp 1").

    Returns:
        Новый список (исходный не мутируется).
    """
    if not tts_remote_url and not tts_local_path:
        # дефолт: берём из самого F5Response
        tts_local_path = f5.audio_path

    audio_path = Path(tts_local_path) if tts_local_path else Path(f5.audio_path)
    file_name = audio_path.name

    in_point_sec = (focal_start_ms + F5_TTS_OFFSET_MS) / 1000.0
    out_point_sec = in_point_sec + f5.audio_duration_ms / 1000.0

    source_footage: dict[str, Any] = {"file_name": file_name}
    if tts_remote_url:
        source_footage["file_path"] = ""
        source_footage["remote_url"] = tts_remote_url
    else:
        source_footage["file_path"] = str(audio_path)

    new_layer: dict[str, Any] = {
        "name": f"f5_hook_{f5.chosen_device.value}",
        "type": "footage",
        "in_point": float(in_point_sec),
        "out_point": float(out_point_sec),
        "z_index": F5_AUDIO_Z_INDEX,
        "text": "",
        "adjustment_layer": False,
        "comp_id": None,
        "comp_name": None,
        "source_rect": {},
        "props": {},
        "effects": {},
        "style_instructions": [],
        "text_data": {
            "layer_meta": {
                "comp_name_target": target_comp_name,
                "startTime": float(in_point_sec),
                "enabled": True,
                "audioEnabled": True,
                "motionBlur": False,
                "collapseTransformation": False,
                "blendingModeCode": "5212",
            },
            "source_footage": source_footage,
            "audio_envelope": dict(F5_AUDIO_ENVELOPE),
        },
    }

    logger.info(
        "f5.inject audio_layer name=%s in=%.3f out=%.3f remote=%s",
        new_layer["name"], in_point_sec, out_point_sec, bool(tts_remote_url),
    )

    return list(footage_layers) + [new_layer]


# ─────────────────────────────────────────────────────────────────────────────
# Subtitle-слой
# ─────────────────────────────────────────────────────────────────────────────

# В text_layer text дублируется в нескольких местах внутри text_data
# (text_base / char_styles_ungrouped / box_text). Чтобы не ломать стиль —
# клонируем существующий шаблон и переопределяем поля, где встречается текст.
_TEXT_DATA_TEXT_KEYS = ("text_base", "char_styles_ungrouped", "box_text")


# Запас по краям окна TTS (сек): чуть шире, чтобы гарантированно вырезать
# субтитры, которые краешком заходят под TTS. Жёсткое требование: НИКОГДА
# не наслаивать субтитры трека и TTS.
F5_SUBTITLE_CLEAR_MARGIN_SEC = 0.15


def _remove_track_subtitles_in_window(
    text_layers: list[dict[str, Any]],
    *,
    window_start_sec: float,
    window_end_sec: float,
    margin_sec: float = F5_SUBTITLE_CLEAR_MARGIN_SEC,
) -> tuple[list[dict[str, Any]], int]:
    """
    Полностью УДАЛЯЕТ из text_layers любой слой, чей [in_point, out_point]
    хоть как-то пересекается с окном TTS (с запасом margin_sec по краям).

    Не enabled=false, а физическое удаление — чтобы ни один трек-субтитр
    не отрендерился под/поверх TTS-субтитра ни при каких обстоятельствах.

    Полный скан списка (не только соседних слоёв).

    Returns:
        (отфильтрованный список, сколько удалили).
    """
    lo = window_start_sec - margin_sec
    hi = window_end_sec + margin_sec

    kept: list[dict[str, Any]] = []
    removed = 0
    for layer in text_layers:
        if not isinstance(layer, dict) or layer.get("type") != "text":
            kept.append(layer)
            continue

        try:
            in_p = float(layer.get("in_point", 0.0))
            out_p = float(layer.get("out_point", 0.0))
        except (TypeError, ValueError):
            # не смогли распарсить тайминги — оставляем (не рискуем)
            kept.append(layer)
            continue

        # Пересечение интервалов [in_p, out_p] и [lo, hi]
        overlaps = (in_p < hi) and (out_p > lo)
        if overlaps:
            removed += 1
            logger.info(
                "f5.inject removing track subtitle in=%.3f out=%.3f text=%r "
                "(overlaps TTS window [%.3f..%.3f])",
                in_p, out_p, str(layer.get("text", ""))[:30], lo, hi,
            )
            continue
        kept.append(layer)

    return kept, removed


def _clone_text_layer_template(
    text_layers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Берём первый существующий text-слой как шаблон стиля.
    Если text_layers пуст — возвращаем None (нет с чего клонировать,
    придётся падать или собирать минимальный слой).
    """
    for layer in text_layers:
        if isinstance(layer, dict) and layer.get("type") == "text":
            return copy.deepcopy(layer)
    return None


def _override_text_recursive(obj: Any, new_text: str) -> None:
    """
    Рекурсивно ищет поля text/string/value/sourceText/raw_text и заменяет
    на new_text. Грубо, но безопасно для наших целей (один новый слой).

    TODO: после первого реального теста — заменить на точечное обновление
    конкретных ключей text_base/char_styles_ungrouped/box_text.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"text", "sourceText", "raw_text", "source_text"} and isinstance(v, str):
                obj[k] = new_text
            else:
                _override_text_recursive(v, new_text)
    elif isinstance(obj, list):
        for item in obj:
            _override_text_recursive(item, new_text)


def inject_subtitle_layer(
    text_layers: list[dict[str, Any]],
    f5: F5Response,
    *,
    focal_start_ms: int,
) -> list[dict[str, Any]]:
    """
    Клонирует первый существующий text-слой как шаблон стиля,
    подставляет TTS-фразу, выставляет тайминги.

    z_index — на 1 выше максимального существующего, чтобы перебить
    лирику в окне focal..focal+tts.
    """
    # 1) Клонируем шаблон стиля ДО удаления (среди удаляемых может быть
    #    единственный text-слой, с которого берём стиль).
    template = _clone_text_layer_template(text_layers)
    if template is None:
        logger.warning(
            "f5.inject subtitle_layer skipped: no text_layers to clone style from"
        )
        return list(text_layers)

    in_sec = focal_start_ms / 1000.0
    out_sec = in_sec + f5.audio_duration_ms / 1000.0

    # 2) Жёсткое требование: вырезаем ВСЕ трек-субтитры, пересекающие окно TTS.
    cleaned, removed = _remove_track_subtitles_in_window(
        text_layers, window_start_sec=in_sec, window_end_sec=out_sec,
    )
    logger.info(
        "f5.inject cleared %d track subtitle(s) in TTS window [%.3f..%.3f]",
        removed, in_sec, out_sec,
    )

    # z_index — следующий ВЫШЕ существующих (в проекте z_index=1000 — самый
    # верхний субтитр; добавляем 1001+). Считаем по исходному списку, чтобы
    # номер был стабилен независимо от удалений.
    max_z = max(
        (int(L.get("z_index", 0)) for L in text_layers if isinstance(L, dict)),
        default=1000,
    )

    template["name"] = f"f5_hook_subtitle_{f5.chosen_device.value}"
    template["text"] = f5.tts_text
    template["in_point"] = float(in_sec)
    template["out_point"] = float(out_sec)
    template["z_index"] = max_z + 1

    # Внутри text_data тоже обновляем text где встречается + startTime.
    td = template.setdefault("text_data", {})
    meta = td.setdefault("layer_meta", {})
    meta["startTime"] = float(in_sec)
    meta["enabled"] = True

    for key in _TEXT_DATA_TEXT_KEYS:
        if key in td:
            _override_text_recursive(td[key], f5.tts_text)

    logger.info(
        "f5.inject subtitle_layer text=%r in=%.3f out=%.3f z=%d",
        f5.tts_text, in_sec, out_sec, template["z_index"],
    )

    # Возвращаем ОЧИЩЕННЫЙ список (без пересекающихся трек-субтитров) + наш слой.
    return cleaned + [template]


# ─────────────────────────────────────────────────────────────────────────────
# Track ducking — пока no-op (согласовано в чате 2026-05-28)
# ─────────────────────────────────────────────────────────────────────────────

def inject_track_duck(
    footage_layers: list[dict[str, Any]],
    *,
    focal_start_ms: int,
    duck_window_ms: int,
    duck_db: float = -3.0,
) -> list[dict[str, Any]]:
    """
    Сейчас no-op. Если на ручной прослушке голос будет тонуть — реализуем
    через audio_envelope или volume keyframes на трек-аудио слое.
    """
    logger.debug(
        "f5.inject track_duck disabled (no-op), focal_start_ms=%d window=%d duck_db=%.1f",
        focal_start_ms, duck_window_ms, duck_db,
    )
    return footage_layers


# ─────────────────────────────────────────────────────────────────────────────
# Единая точка входа
# ─────────────────────────────────────────────────────────────────────────────

def apply_f5(
    *,
    footage_layers: list[dict[str, Any]],
    text_layers: list[dict[str, Any]],
    f5: F5Response | None,
    focal_start_ms: int,
    tts_remote_url: str | None = None,
    tts_local_path: str | None = None,
    target_comp_name: str = DEFAULT_AUDIO_COMP,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Главная точка входа для project_builder.

    f5 is None → возвращает входные списки без изменений (job без F5-хука).
    """
    if f5 is None:
        return footage_layers, text_layers

    new_footage = inject_audio_layer(
        footage_layers, f5,
        focal_start_ms=focal_start_ms,
        tts_remote_url=tts_remote_url,
        tts_local_path=tts_local_path,
        target_comp_name=target_comp_name,
    )
    new_text = inject_subtitle_layer(
        text_layers, f5, focal_start_ms=focal_start_ms,
    )
    return new_footage, new_text
