"""Настройки текста визарда на стороне сборки: env → движок раскладки нужного стиля.

Контракт (web → orchestrator → build): `SendAudioS3Request.subtitle_text_style`
→ env `SUBTITLE_TEXT_STYLE_JSON` (snake_case, ключи — `SubtitleTextStyle`).
Акцентный цвет едет отдельно, существующим `accent_color_hex` → env
`SUBTITLES_FOCUS_HEX` (одно место на все стили; он же красит фигуру F2).

Все значения по умолчанию (шрифт = стандартный для стиля) → раскладка не
трогается вовсе: прод без изменений. Иначе — движок стиля
(`app/subtitle_font_layout`), а невозможная комбинация (шрифт запрещён стилю,
пара запрещена, высота у гротеска, «вниз» в вертикали, шрифт у brat) — ошибка
сборки, не молчаливая подмена (No Fallback).
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, fields
from typing import Any, Dict, Iterator, Optional

from core.subtitles_mode import (
    SUBTITLES_MODE_BRAT_5TH,
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    SUBTITLES_MODE_TRENDY_5TH,
)

SUBTITLE_TEXT_STYLE_ENV = "SUBTITLE_TEXT_STYLE_JSON"
FOCUS_HEX_ENV = "SUBTITLES_FOCUS_HEX"

JAKSON_MODES = frozenset({SUBTITLES_MODE_SCENES_3RD, SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP})

# шрифт «стандартный для стиля» — прод каждого стиля (эталоны движка)
DEFAULT_FONT_BY_MODE = {
    SUBTITLES_MODE_SCENES_3RD: "Point-SemiBold",
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP: "Point-SemiBold",
    SUBTITLES_MODE_IMPULSE_2ND: "Point-Light",
    SUBTITLES_MODE_TEMPLATE_4TH: "Montserrat-BoldItalic",
    SUBTITLES_MODE_TRENDY_5TH: "Montserrat-Bold",
}


@dataclass(frozen=True)
class SubtitleTextStyle:
    font: Optional[str] = None           # PostScript из каталога; None — стандартный для стиля
    accent_font: Optional[str] = None    # акцентный шрифт пары (фокус-слова); None — без пары
    size: str = "large"
    height: str = "normal"
    position: str = "center"
    shadow: str = "soft"
    focus_style: Optional[str] = None    # brat: курсив фокус-слова

    @classmethod
    def from_json(cls, raw: str) -> "SubtitleTextStyle":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError(f"{SUBTITLE_TEXT_STYLE_ENV} must be a JSON object")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise RuntimeError(f"{SUBTITLE_TEXT_STYLE_ENV}: unknown keys {sorted(unknown)}")
        return cls(**{k: data[k] for k in known if data.get(k) is not None})

    def is_default(self) -> bool:
        return self == SubtitleTextStyle()


def style_from_env() -> Optional[SubtitleTextStyle]:
    raw = str(os.environ.get(SUBTITLE_TEXT_STYLE_ENV) or "").strip()
    return SubtitleTextStyle.from_json(raw) if raw else None


def accent_color_from_env() -> Optional[str]:
    raw = str(os.environ.get(FOCUS_HEX_ENV) or "").strip()
    if not raw:
        return None
    return raw if raw.startswith("#") else "#" + raw


def _params(style: SubtitleTextStyle, accent_color: Optional[str]):
    from app.subtitle_font_layout import JaksonTextParams

    return JaksonTextParams(size=style.size, height=style.height, position=style.position,
                            shadow=style.shadow, accent_color=accent_color)


def _render_preset() -> str:
    from app.render_presets import active_preset

    return active_preset().name


def jsx_style_config(mode: str, style: Optional[SubtitleTextStyle] = None,
                     accent_color: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """trendy/brat: CONFIG-оверрайды для скрипта (None — прод)."""
    from app.subtitle_font_layout import brat_layout, trendy_layout

    if style is None or style.is_default():
        if accent_color is None:
            return None
        style = style or SubtitleTextStyle()
    params = _params(style, accent_color)
    if mode == SUBTITLES_MODE_TRENDY_5TH:
        if style.focus_style is not None:
            raise ValueError("focus_style is a brat-only setting")
        font = style.font or DEFAULT_FONT_BY_MODE[mode]
        return trendy_layout(font, params=params, render_preset=_render_preset(),
                             accent_font=style.accent_font).jsx_config()
    if mode == SUBTITLES_MODE_BRAT_5TH:
        return brat_layout(params=params, render_preset=_render_preset(), font=style.font,
                           accent_font=style.accent_font, focus_style=style.focus_style).jsx_config()
    raise ValueError(f"jsx_style_config: not a 5th-template mode: {mode!r}")


@contextmanager
def applied_text_style(mode: str, style: Optional[SubtitleTextStyle] = None,
                       accent_color: Optional[str] = None) -> Iterator[None]:
    """Python-стили (jakson / impulse / tape): раскладка движка на время сборки
    слоёв, затем сброс (модульные глобалы билдеров не переживают джобу)."""
    from app import scenes_3rd_reference_builder as jakson
    from app import template_4th_reference_builder as tape
    from app import text_flow_renderer as impulse
    from app.subtitle_font_layout import impulse_layout, jakson_layout, tape_layout

    if style is None or style.is_default():
        # без настроек — прод; акцентный цвет jakson прод красит сам (SUBTITLES_FOCUS_HEX)
        yield
        return
    if style.focus_style is not None:
        raise ValueError("focus_style is a brat-only setting")
    params = _params(style, accent_color)
    preset = _render_preset()
    font = style.font or DEFAULT_FONT_BY_MODE.get(mode)
    if mode in JAKSON_MODES:
        _check_jakson_base(font)
        apply, reset = jakson.apply_font_layout, lambda: jakson.apply_font_layout(None)
        layout = jakson_layout(font, accent_font=style.accent_font, params=params, render_preset=preset)
    elif mode == SUBTITLES_MODE_IMPULSE_2ND:
        apply, reset = impulse.apply_impulse_layout, lambda: impulse.apply_impulse_layout(None)
        layout = impulse_layout(font, params=params, render_preset=preset, accent_font=style.accent_font)
    elif mode == SUBTITLES_MODE_TEMPLATE_4TH:
        apply, reset = tape.apply_tape_layout, lambda: tape.apply_tape_layout(None)
        layout = tape_layout(font, params=params, render_preset=preset, accent_font=style.accent_font)
    else:
        raise ValueError(f"subtitle text settings are not supported for subtitles_mode={mode!r}")
    apply(layout)
    try:
        yield
    finally:
        reset()


def _check_jakson_base(font: str) -> None:
    """jakson набирает капсом (TYPE_1..6): скрипт основным там пока не собран —
    капсом рукописный текст не читается (смотр 2026-09-30). Явная ошибка."""
    from app.subtitle_font_layout import check_style_allowed, load_catalog

    check_style_allowed(font, "jakson")
    if (load_catalog().get(font) or {}).get("category") == "script":
        raise ValueError(f"script font {font!r} cannot be the jakson base font yet (caps-only layout)")
