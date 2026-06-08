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

# TTS-аудио и его субтитр обязаны стартовать в один и тот же кадр — иначе в
# отрендеренном проекте появляется рассинхрон голоса и подписи. Поэтому смещение
# = 0: и audio_layer, и subtitle_layer берут ровно focal_start_ms.
F5_TTS_OFFSET_MS = 0

# Имена композиций (подсмотрено в render.jsx)
DEFAULT_AUDIO_COMP = "Comp 1"
DEFAULT_TEXT_COMP = "Текст"

# Envelope для TTS-голоса — короткие фейды, играет на полной громкости.
# (Чтобы голос не тонул в вокале трека, приглушается САМ ТРЕК — см.
# inject_track_duck / F5_TRACK_DUCK_* ниже.)
F5_AUDIO_ENVELOPE = {
    "fade_in_s": 0.05,
    "fade_out_s": 0.10,
    "min_db": -48.0,
}

# Ducking трека под F5-голос: трек приглушается до F5_TRACK_DUCK_FROM_PCT в
# момент старта голоса и плавно возвращается к F5_TRACK_DUCK_TO_PCT к дропу.
F5_TRACK_DUCK_FROM_PCT = 25.0
F5_TRACK_DUCK_TO_PCT = 100.0


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


def _rebuild_char_styles(template_td: dict[str, Any], *, text_len: int) -> list[dict[str, Any]]:
    """
    Перестраивает char_styles_ungrouped под длину нового текста.

    AE рендерит видимую строку из layer["text"]; char_styles_ungrouped несёт
    только по-символьные стили ({"i", "font", "fontSize", ...}) и обязан совпадать
    по длине с текстом, иначе хвост символов отрендерится без стиля. Берём стиль
    первого символа шаблона как базовый для всех индексов нового текста.
    """
    base_style: dict[str, Any] = {}
    existing = template_td.get("char_styles_ungrouped")
    if isinstance(existing, list) and existing and isinstance(existing[0], dict):
        base_style = {k: v for k, v in existing[0].items() if k != "i"}
    return [{"i": i, **base_style} for i in range(max(0, int(text_len)))]


# Max words per TTS subtitle chunk. The TTS phrase (3–8 words) is split into
# sequential chunks shown across the voice window — like default subtitles
# (one layer per segment), instead of one layer dumping the whole phrase.
F5_SUBTITLE_MAX_WORDS_PER_CHUNK = 3


def _split_tts_text(text: str, *, max_words: int = F5_SUBTITLE_MAX_WORDS_PER_CHUNK) -> list[str]:
    words = [w for w in str(text or "").split() if w]
    if not words:
        return []
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def _strip_time_animated(d: Any) -> dict[str, Any]:
    """Drop keyframed (time-animated) entries from a props/effects dict.

    A cloned template segment carries its OWN reveal keyframes (e.g.
    props["reveal"] = ADBE Text Percent Start with keyframes at the template's
    times). Left in place after re-timing, they misfire — the text "reveals" in
    ~1s at the wrong moment then vanishes. We keep only static entries so each
    chunk just shows cleanly for its slice.
    """
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if not (isinstance(v, dict) and "keyframes" in v)}


def inject_subtitle_layer(
    text_layers: list[dict[str, Any]],
    f5: F5Response,
    *,
    focal_start_ms: int,
) -> list[dict[str, Any]]:
    """
    Клонирует первый существующий text-слой как шаблон стиля и раскладывает
    TTS-фразу на ПОСЛЕДОВАТЕЛЬНЫЕ чанки (как дефолтные субтитры — слой на
    сегмент), равномерно по окну голоса [focal..focal+tts]. У каждого чанка
    снимаются stale reveal-кейфреймы и отключается text-аниматор, чтобы текст
    показывался ровно в свой слайс, а не «раскрывался за секунду» весь сразу.
    """
    template = _clone_text_layer_template(text_layers)
    if template is None:
        logger.warning(
            "f5.inject subtitle_layer skipped: no text_layers to clone style from"
        )
        return list(text_layers)

    in_sec = focal_start_ms / 1000.0
    out_sec = in_sec + f5.audio_duration_ms / 1000.0

    # Жёсткое требование: вырезаем ВСЕ трек-субтитры, пересекающие окно TTS.
    cleaned, removed = _remove_track_subtitles_in_window(
        text_layers, window_start_sec=in_sec, window_end_sec=out_sec,
    )
    logger.info(
        "f5.inject cleared %d track subtitle(s) in TTS window [%.3f..%.3f]",
        removed, in_sec, out_sec,
    )

    chunks = _split_tts_text(f5.tts_text)
    if not chunks:
        logger.warning("f5.inject subtitle skipped: empty tts_text")
        return cleaned

    max_z = max(
        (int(L.get("z_index", 0)) for L in text_layers if isinstance(L, dict)),
        default=1000,
    )

    n = len(chunks)
    span = max(0.0001, out_sec - in_sec)
    slice_dur = span / n

    new_layers: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        seg_in = in_sec + i * slice_dur
        seg_out = out_sec if i == n - 1 else in_sec + (i + 1) * slice_dur

        layer = copy.deepcopy(template)
        layer["name"] = f"f5_hook_subtitle_{f5.chosen_device.value}_{i + 1}"
        layer["text"] = chunk
        layer["in_point"] = float(seg_in)
        layer["out_point"] = float(seg_out)
        layer["z_index"] = max_z + 1 + i
        # Strip the cloned segment's stale reveal keyframes (props/effects) so the
        # chunk doesn't inherit a misfiring per-character reveal.
        layer["props"] = _strip_time_animated(layer.get("props"))
        layer["effects"] = _strip_time_animated(layer.get("effects"))

        td = layer.setdefault("text_data", {})
        meta = td.setdefault("layer_meta", {})
        meta["startTime"] = float(seg_in)
        meta["enabled"] = True
        # Disable the reveal animator: each chunk shows fully for its slice.
        td.pop("text_animator", None)
        td["no_text_animator"] = True
        td["char_styles_ungrouped"] = _rebuild_char_styles(td, text_len=len(chunk))

        logger.info(
            "f5.inject subtitle chunk %d/%d text=%r in=%.3f out=%.3f z=%d",
            i + 1, n, chunk, seg_in, seg_out, layer["z_index"],
        )
        new_layers.append(layer)

    return cleaned + new_layers


# ─────────────────────────────────────────────────────────────────────────────
# Track ducking — приглушаем ТРЕК под голос, возвращаем громкость к дропу
# ─────────────────────────────────────────────────────────────────────────────

def _is_track_audio_layer(layer: dict[str, Any]) -> bool:
    """True для основного трек-аудио слоя (audioEnabled, не наш F5/F1 хук)."""
    if not isinstance(layer, dict) or layer.get("type") != "footage":
        return False
    name = str(layer.get("name") or "")
    if name.startswith("f5_hook") or name.startswith("f1_hook"):
        return False
    meta = ((layer.get("text_data") or {}).get("layer_meta") or {})
    return bool(meta.get("audioEnabled"))


def inject_track_duck(
    footage_layers: list[dict[str, Any]],
    *,
    duck_from_sec: float,
    duck_to_sec: float,
    from_pct: float = F5_TRACK_DUCK_FROM_PCT,
    to_pct: float = F5_TRACK_DUCK_TO_PCT,
) -> list[dict[str, Any]]:
    """
    Приглушает основной ТРЕК под F5-голос: на duck_from_sec громкость падает до
    from_pct%, затем линейно растёт до to_pct% к duck_to_sec (= дропу), дальше
    100%. Реализуется через duck_* поля в audio_envelope трек-слоя (выражение на
    ADBE Audio Levels в шаблоне). Pure (исходный список не мутируется).
    """
    if not (duck_to_sec > duck_from_sec):
        logger.info(
            "f5.duck skipped: non-positive window [%.3f..%.3f]",
            duck_from_sec, duck_to_sec,
        )
        return footage_layers

    out: list[dict[str, Any]] = []
    ducked = 0
    for layer in footage_layers:
        if _is_track_audio_layer(layer):
            L = copy.deepcopy(layer)
            td = L.setdefault("text_data", {})
            env = dict(td.get("audio_envelope") or {})
            env["duck_from_s"] = float(duck_from_sec)
            env["duck_to_s"] = float(duck_to_sec)
            env["duck_from_pct"] = float(from_pct)
            env["duck_to_pct"] = float(to_pct)
            td["audio_envelope"] = env
            out.append(L)
            ducked += 1
            logger.info(
                "f5.duck track=%s window=[%.3f..%.3f] %.0f%%->%.0f%%",
                L.get("name"), duck_from_sec, duck_to_sec, from_pct, to_pct,
            )
        else:
            out.append(layer)
    if ducked == 0:
        logger.info("f5.duck: no track audio layer found — track left at full volume")
    return out


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
    drop_rel_sec: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Главная точка входа для project_builder.

    f5 is None → возвращает входные списки без изменений (job без F5-хука).
    drop_rel_sec (comp-relative секунды дропа) → если задан и > старта голоса,
    приглушаем трек под голос с возвратом громкости к дропу.
    """
    if f5 is None:
        return footage_layers, text_layers

    # Duck the TRACK under the voice FIRST (before adding the voice layer, so the
    # duck only touches the real track audio, never our own f5 voice layer).
    voice_in_sec = focal_start_ms / 1000.0
    if drop_rel_sec is not None and float(drop_rel_sec) > voice_in_sec:
        footage_layers = inject_track_duck(
            footage_layers,
            duck_from_sec=voice_in_sec,
            duck_to_sec=float(drop_rel_sec),
        )

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
