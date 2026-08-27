# -*- coding: utf-8 -*-
"""F6 «Видео»: врезка пользовательского видео в pre-drop окно.

Размещение (симметрично F1 «Звук»):

    in_point  = 0                                (с первого кадра композиции)
    out_point = drop_time                        (ровно до дропа)

и, если известна фактическая длительность вырезки, out_point дополнительно
клампится по ней — чтобы не оставлять «замороженный» хвост, если бот отдал окно
шире видео.

Слой лежит НАД футажом и грейд-аджастментами, но ПОД субтитр-прекомпом:
в этом проекте меньший z_index = выше в стеке AE (шаблон добавляет слои с
конца массива, а `layers.add` кладёт новый слой наверх). Раскладка:
текст=1, аудио=2, **видео F6=3**, оверлеи=5+, аджастменты=10+, футаж=100+.

Скейл «cover» считается ЧИСЛАМИ из размеров исходника (ffprobe на боте), а не
выражением: выражения в headless aerender ненадёжны — это правило репозитория,
оплаченное отладкой F4.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Выше футажа/аджастментов, ниже субтитров (меньше = выше, см. модульный докстринг).
F6_VIDEO_Z_INDEX = 3

# F6 закрывает всё pre-drop окно. Ненулевые отступы показывают основной футаж
# до/после пользовательской вырезки и воспринимаются как случайные кадры.
F6_LEAD_PAD_SEC = 0.0
F6_TAIL_PAD_SEC = 0.0

DEFAULT_MAIN_COMP = "Comp 1"

# Звук вырезки: мягкие фейды по краям, чтобы врезка не щёлкала.
F6_AUDIO_ENVELOPE = {
    "fade_in_s": 0.05,
    "fade_out_s": 0.10,
    "min_db": -48.0,
}

# Насколько приглушается ТРЕК под звук вырезки. Жёстче, чем у F5/F1 (25%→100%):
# под интервью трек должен быть фоном, иначе речь не разобрать.
F6_TRACK_DUCK_FROM_PCT = 15.0
F6_TRACK_DUCK_TO_PCT = 100.0

# Запас, с которым гасятся трек-субтитры, перекрытые видео (секунды).
F6_SUBTITLE_CLEAR_MARGIN_SEC = 0.15


def f6_video_window(drop_time: float, duration: float | None = None) -> tuple[float, float]:
    """(in_point, out_point) в comp-relative секундах для pre-drop видео."""
    in_sec = F6_LEAD_PAD_SEC
    out_sec = float(drop_time) - F6_TAIL_PAD_SEC
    if duration is not None:
        dur = float(duration)
        if dur <= 0.0:
            raise ValueError(f"f6 video: duration must be > 0 (got {duration!r})")
        out_sec = min(out_sec, in_sec + dur)
    return in_sec, out_sec


def cover_scale_percent(
    *,
    source_width: float,
    source_height: float,
    comp_width: float,
    comp_height: float,
) -> float:
    """Процент масштаба, при котором исходник накрывает комп целиком (cover)."""
    sw, sh = float(source_width), float(source_height)
    cw, ch = float(comp_width), float(comp_height)
    if sw <= 0 or sh <= 0:
        raise ValueError(f"f6 video: bad source size {source_width!r}x{source_height!r}")
    if cw <= 0 or ch <= 0:
        raise ValueError(f"f6 video: bad comp size {comp_width!r}x{comp_height!r}")
    return max(cw / sw, ch / sh) * 100.0


def _prop(match_name: str, value: Any) -> dict[str, Any]:
    """Дикт в форме asdict(PropertyData) — шаблон читает именно эти поля."""
    return {"match_name": match_name, "value": value, "keyframes": [], "expression": None}


def inject_f6_video(
    footage_layers: list[dict[str, Any]],
    *,
    video_url: str,
    drop_time: float,
    source_width: float,
    source_height: float,
    comp_width: float,
    comp_height: float,
    duration: float | None = None,
    target_comp_name: str = DEFAULT_MAIN_COMP,
) -> list[dict[str, Any]]:
    """Добавляет видео-прогрев как footage-слой со звуком. Pure (без мутаций)."""
    video_url = str(video_url or "").strip()
    if not video_url:
        raise ValueError("f6 video: video_url is empty")

    in_sec, out_sec = f6_video_window(drop_time, duration)
    if not (out_sec > in_sec):
        raise ValueError(
            f"f6 video: non-positive window (drop_time={drop_time}, duration={duration}, "
            f"need drop_time > {F6_LEAD_PAD_SEC + F6_TAIL_PAD_SEC})"
        )

    scale = cover_scale_percent(
        source_width=source_width,
        source_height=source_height,
        comp_width=comp_width,
        comp_height=comp_height,
    )

    # Имя файла из URL — под ним нода положит скачанный файл в media/video/.
    file_name = Path(video_url.split("?", 1)[0].rstrip("/")).name or "f6_video.mp4"

    new_layer: dict[str, Any] = {
        "name": "f6_hook_video",
        "type": "footage",
        "in_point": float(in_sec),
        "out_point": float(out_sec),
        "z_index": F6_VIDEO_Z_INDEX,
        "text": "",
        "adjustment_layer": False,
        "comp_id": None,
        "comp_name": None,
        "source_rect": {},
        "props": {
            "tf_anchor": _prop(
                "ADBE Anchor Point",
                [float(source_width) / 2.0, float(source_height) / 2.0, 0.0],
            ),
            "tf_position": _prop(
                "ADBE Position",
                [float(comp_width) / 2.0, float(comp_height) / 2.0, 0.0],
            ),
            "tf_scale": _prop("ADBE Scale", [scale, scale, 100.0]),
            "tf_rotation": _prop("ADBE Rotate Z", 0),
            "tf_opacity": _prop("ADBE Opacity", 100),
        },
        "effects": {},
        "style_instructions": [],
        "text_data": {
            "layer_meta": {
                "comp_name_target": target_comp_name,
                "startTime": float(in_sec),
                "enabled": True,
                "audioEnabled": True,
                "motionBlur": False,
                "collapseTransformation": False,
                "blendingModeCode": "5212",
            },
            "source_footage": {
                "file_name": file_name,
                "file_path": "",
                "remote_url": video_url,
            },
            "audio_envelope": dict(F6_AUDIO_ENVELOPE),
        },
    }

    # ВАЖНО: позиция в списке — это и есть позиция в стеке AE. Шаблон идёт по
    # массиву с конца, а layers.add() кладёт новый слой наверх, поэтому индекс 0
    # оказывается самым верхним слоем, а z_index дальше нигде не пересортируется.
    # Просто дописать слой в конец (как это делают F1/F5 со своим аудио, где
    # порядок не важен) значило бы положить видео ПОД весь футаж — его бы не было
    # видно. Вставляем по возрастанию z: после текста (1) и аудио (2), перед
    # аджастментами (10+) и футажом (100+).
    out = list(footage_layers)
    insert_at = len(out)
    for idx, layer in enumerate(out):
        try:
            z = int(layer.get("z_index"))
        except (AttributeError, TypeError, ValueError):
            continue
        if z > F6_VIDEO_Z_INDEX:
            insert_at = idx
            break
    out.insert(insert_at, new_layer)

    logger.info(
        "f6.inject video_layer name=%s in=%.3f out=%.3f scale=%.1f%% src=%sx%s "
        "comp=%sx%s stack_index=%d/%d url=%s",
        new_layer["name"], in_sec, out_sec, scale,
        source_width, source_height, comp_width, comp_height,
        insert_at, len(out), video_url[:80],
    )
    return out


def clear_track_subtitles_under_video(
    text_layers: list[dict[str, Any]],
    *,
    drop_time: float,
    duration: float | None = None,
) -> list[dict[str, Any]]:
    """Убирает трек-субтитры, перекрытые видео-прогревом.

    Под чужой вырезкой (интервью и т.п.) строки песни читаются как баг, а не как
    приём: кадр целиком чужой, а поверх бегут слова трека. Ядро переиспользуем у
    F5 — там та же задача (не дать трек-субтитрам налезть на врезку).
    """
    from mlcore.hooks.f5_cognition.inject import _remove_track_subtitles_in_window

    in_sec, out_sec = f6_video_window(drop_time, duration)
    kept, removed = _remove_track_subtitles_in_window(
        text_layers,
        window_start_sec=in_sec,
        window_end_sec=out_sec,
        margin_sec=F6_SUBTITLE_CLEAR_MARGIN_SEC,
    )
    logger.info(
        "f6.inject cleared %d track subtitle(s) under video window [%.3f..%.3f]",
        removed, in_sec, out_sec,
    )
    return kept
