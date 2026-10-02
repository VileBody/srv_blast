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

# subtitles_mode → стиль каталога; шрифт «стандартный для стиля» — STYLE_DEFAULT_FONTS движка
STYLE_BY_MODE = {
    SUBTITLES_MODE_SCENES_3RD: "jakson",
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP: "jakson",
    SUBTITLES_MODE_IMPULSE_2ND: "impulse",
    SUBTITLES_MODE_TEMPLATE_4TH: "tape",
    SUBTITLES_MODE_TRENDY_5TH: "trendy",
    SUBTITLES_MODE_BRAT_5TH: "brat",
}


def _default_font(mode: str) -> Optional[str]:
    from app.subtitle_font_layout import STYLE_DEFAULT_FONTS

    return STYLE_DEFAULT_FONTS.get(STYLE_BY_MODE.get(mode, ""))


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


def kant_layout_config(mode: str, style: Optional[SubtitleTextStyle] = None,
                       accent_color: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Тайтл Kant: шрифт, цвет и анимация зашиты в .aep — из настроек текста применимы только
    размер и положение (layout для build_kant_title_overlay). Остальное — ошибка, не тихий сброс.
    None — прод (крупно, по центру)."""
    from app.subtitle_font_layout import POSITION_PRESETS, SAFE_MARGIN_X, SIZE_PRESETS

    if accent_color is not None:
        raise ValueError(f"{mode}: Kant titles have no accent color")
    if style is None:
        return None
    if (style.font, style.accent_font, style.focus_style, style.height, style.shadow) != (None, None, None, "normal", "soft"):
        raise ValueError(f"{mode}: Kant titles take only size and position")
    if style.size not in SIZE_PRESETS or style.position not in POSITION_PRESETS:
        raise ValueError(f"{mode}: unknown size/position {style.size!r}/{style.position!r}")
    if style.size == "large" and style.position == "center":
        return None
    align, center_y = POSITION_PRESETS[style.position]
    return {"scale": round(SIZE_PRESETS[style.size] * 100.0, 3), "align": align,
            "centerY": center_y, "marginX": SAFE_MARGIN_X}


def jsx_style_config(mode: str, style: Optional[SubtitleTextStyle] = None,
                     accent_color: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """trendy/brat: CONFIG-оверрайды для скрипта (None — прод)."""
    from app.subtitle_font_layout import brat_layout, hex_to_rgb01, trendy_layout
    from core.subtitles_mode import SUBTITLES_MODE_KANT_TITLES

    if mode in SUBTITLES_MODE_KANT_TITLES:
        return kant_layout_config(mode, style, accent_color)
    if style is None or style.is_default():
        # выбран только цвет — прод-CONFIG скрипта, меняется лишь цвет фокус-слов
        return None if accent_color is None else {"focusFillColor": hex_to_rgb01(accent_color)}
    params = _params(style, accent_color)
    if mode == SUBTITLES_MODE_TRENDY_5TH:
        if style.focus_style is not None:
            raise ValueError("focus_style is a brat-only setting")
        font = style.font or _default_font(mode)
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
    from app.subtitle_font_layout import hex_to_rgb01, impulse_layout, jakson_layout, tape_layout

    if style is None or style.is_default():
        # без настроек — прод-раскладка. Выбран только цвет: jakson красит сам
        # (SUBTITLES_FOCUS_HEX), tape/impulse — через override цвета билдера.
        rgb = hex_to_rgb01(accent_color) if accent_color else None
        tape.apply_accent_color(rgb)
        impulse.apply_accent_color(rgb)
        try:
            yield
        finally:
            tape.apply_accent_color(None)
            impulse.apply_accent_color(None)
        return
    if style.focus_style is not None:
        raise ValueError("focus_style is a brat-only setting")
    params = _params(style, accent_color)
    preset = _render_preset()
    font = style.font or _default_font(mode)
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
