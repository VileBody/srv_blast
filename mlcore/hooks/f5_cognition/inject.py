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

# Макс. длина одной строки субтитра голоса (символов). Трек-строки рендерятся
# на 100% при длине ~до 20 символов; более длинную point-text строку AE ужимает
# width-fit'ом (наблюдалось: строка 31 символ → scale 85.8%, «другой размер» по
# сравнению с треком). Поэтому фразу голоса режем на трек-размерные строки и
# показываем их ПОСЛЕДОВАТЕЛЬНО по окну голоса (как трек: музыка непрерывна,
# строки сменяются) — каждая строка короткая → каждая на 100% → 1:1 как трек.
F5_SUBTITLE_MAX_CHARS = 20


def _split_tts_text(text: str, max_chars: int = F5_SUBTITLE_MAX_CHARS) -> list[str]:
    """Жадно пакует слова в строки длиной ≤ max_chars (по границам слов).

    Порядок слов сохраняется; одно слово длиннее max_chars кладётся в свою
    строку как есть (резать слово не будем — лучше один ужатый кадр, чем
    разорванное слово).
    """
    words = str(text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= int(max_chars):
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


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


def _template_renders_uppercase(template: dict[str, Any]) -> bool:
    """True, если клонируемый трек-субтитр визуально в ВЕРХНЕМ регистре.

    Капс у трека приходит из двух механизмов (часто обоих сразу):
      1) свойство text_base.allCaps=True (AE сам аплит верхний регистр);
      2) сам текст заранее зааплен в .upper() билдером (scenes_3rd /
         text_flow_renderer) — тогда allCaps может быть и False.

    Клон тянет (1) через text_base, но НЕ тянет (2): текст голоса приходит от
    Gemini в нормальном регистре. Поэтому решаем по визуальному факту шаблона —
    и если он капсовый, сами аплим .upper() к строкам голоса (как делает трек).
    """
    td = template.get("text_data") or {}
    base = td.get("text_base") or {}
    if base.get("allCaps"):
        return True
    txt = str(template.get("text") or "")
    alpha = [c for c in txt if c.isalpha()]
    return bool(alpha) and all(c.isupper() for c in alpha)


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


def _retime_keyframes_inplace(obj: Any, *, src_in: float, src_out: float,
                              dst_in: float, dst_out: float) -> None:
    """Recursively remap every keyframe time `t` from the source segment window
    [src_in, src_out] to the destination (voice) window [dst_in, dst_out].

    This is the KEY to "same subtitle type as the track": we clone a real track
    subtitle layer WITH its reveal animator/keyframes, then just slide+scale the
    keyframe times onto the voice window so the reveal plays correctly over the
    voice (instead of stale times → the old "reveals in 1s then vanishes" bug).
    """
    src_span = float(src_out) - float(src_in)
    dst_span = float(dst_out) - float(dst_in)
    if src_span <= 1e-9:
        return

    def remap(t: float) -> float:
        p = (float(t) - float(src_in)) / src_span
        return float(dst_in) + p * dst_span

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kfs = node.get("keyframes")
            if isinstance(kfs, list):
                for kf in kfs:
                    if isinstance(kf, dict) and "t" in kf:
                        try:
                            kf["t"] = remap(kf["t"])
                        except (TypeError, ValueError):
                            pass
            for k, v in node.items():
                if k != "keyframes":
                    walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(obj)


def inject_voice_subtitle(
    text_layers: list[dict[str, Any]],
    *,
    text: str,
    in_sec: float,
    out_sec: float,
    name_prefix: str,
    log_tag: str = "voice",
) -> list[dict[str, Any]]:
    """Кладёт произвольную голосовую фразу как трек-субтитр в окне [in_sec, out_sec].

    Переиспользуемое ядро (F5 «Мысль» и F1 «Звук» зовут его): клонирует
    существующий трек-субтитр КАК ЕСТЬ (стиль + аниматор + reveal = ровно тот же
    ТИП), режет фразу на трек-размерные строки (≤ F5_SUBTITLE_MAX_CHARS) и
    показывает ПОСЛЕДОВАТЕЛЬНО по окну — слой-на-строку, reveal ретаймится в
    под-окно. Каждая строка короткая → рендерится на 100% (без width-fit ужима)
    → 1:1 как трек по размеру/позиции. Капс наследуется от шаблона. Все
    пересекающие окно трек-субтитры вырезаются (никогда не наслаиваем).
    """
    template = _clone_text_layer_template(text_layers)
    if template is None:
        logger.warning(
            "%s.inject subtitle skipped: no text_layers to clone style from", log_tag
        )
        return list(text_layers)

    # Жёсткое требование: вырезаем ВСЕ трек-субтитры, пересекающие окно голоса.
    cleaned, removed = _remove_track_subtitles_in_window(
        text_layers, window_start_sec=in_sec, window_end_sec=out_sec,
    )
    logger.info(
        "%s.inject cleared %d track subtitle(s) in window [%.3f..%.3f]",
        log_tag, removed, in_sec, out_sec,
    )

    lines = _split_tts_text(text)
    if not lines:
        logger.warning("%s.inject subtitle skipped: empty text", log_tag)
        return cleaned

    max_z = max(
        (int(L.get("z_index", 0)) for L in text_layers if isinstance(L, dict)),
        default=1000,
    )

    # Source window = the cloned track segment's own window (keyframes are timed
    # to it). Retime them onto each line's sub-window so the same reveal plays.
    src_in = float(template.get("in_point", in_sec))
    src_out = float(template.get("out_point", src_in + max(0.0001, out_sec - in_sec)))

    window = max(0.0001, out_sec - in_sec)
    total_chars = sum(len(s) for s in lines) or 1

    # Match the track's case: jakson/impulse pre-uppercase the displayed text, so
    # a normal-case voice line under the same style looks out of place. If the
    # cloned template renders uppercase → uppercase the voice lines too.
    force_upper = _template_renders_uppercase(template)

    new_subs: list[dict[str, Any]] = []
    cursor = in_sec
    for idx, line in enumerate(lines):
        if force_upper:
            line = line.upper()
        # Длительность строки пропорциональна её длине; последняя добивает до конца
        # окна, чтобы не накопить погрешность округления.
        if idx == len(lines) - 1:
            seg_out = out_sec
        else:
            seg_out = cursor + window * (len(line) / total_chars)
        seg_in = cursor
        cursor = seg_out

        layer = copy.deepcopy(template)
        suffix = "" if len(lines) == 1 else f"_{idx + 1}"
        layer["name"] = f"{name_prefix}{suffix}"
        layer["text"] = line
        layer["in_point"] = float(seg_in)
        layer["out_point"] = float(seg_out)
        layer["z_index"] = max_z + 1 + idx

        # Retime reveal/animation keyframes (props + effects + text_data) onto
        # this line's sub-window.
        _retime_keyframes_inplace(layer.get("props"), src_in=src_in, src_out=src_out,
                                  dst_in=seg_in, dst_out=seg_out)
        _retime_keyframes_inplace(layer.get("effects"), src_in=src_in, src_out=src_out,
                                  dst_in=seg_in, dst_out=seg_out)

        td = layer.setdefault("text_data", {})
        _retime_keyframes_inplace(td, src_in=src_in, src_out=src_out,
                                  dst_in=seg_in, dst_out=seg_out)
        meta = td.setdefault("layer_meta", {})
        meta["startTime"] = float(seg_in)
        meta["enabled"] = True
        # Rebuild per-char styles ONLY if the template actually carries them.
        # scenes_3rd/flow layers keep char_styles_ungrouped=[] (styling comes
        # from base text_data + the text_animator) — rebuilding to index-only
        # entries there CLOBBERS the scene style and the voice subtitle renders
        # as a flat "blocks"-looking line. Empty template → leave empty.
        existing_cs = td.get("char_styles_ungrouped")
        if isinstance(existing_cs, list) and existing_cs:
            td["char_styles_ungrouped"] = _rebuild_char_styles(td, text_len=len(line))

        logger.info(
            "%s.inject subtitle line=%r in=%.3f out=%.3f z=%d",
            log_tag, line, seg_in, seg_out, layer["z_index"],
        )
        new_subs.append(layer)

    logger.info(
        "%s.inject subtitle (track-type) lines=%d window=[%.3f..%.3f] "
        "retimed_from=[%.3f..%.3f]",
        log_tag, len(new_subs), in_sec, out_sec, src_in, src_out,
    )
    return cleaned + new_subs


def inject_subtitle_layer(
    text_layers: list[dict[str, Any]],
    f5: F5Response,
    *,
    focal_start_ms: int,
) -> list[dict[str, Any]]:
    """F5-обёртка над inject_voice_subtitle: окно = [focal..focal+tts]."""
    in_sec = focal_start_ms / 1000.0
    out_sec = in_sec + f5.audio_duration_ms / 1000.0
    return inject_voice_subtitle(
        text_layers,
        text=f5.tts_text,
        in_sec=in_sec,
        out_sec=out_sec,
        name_prefix=f"f5_hook_subtitle_{f5.chosen_device.value}",
        log_tag="f5",
    )


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
