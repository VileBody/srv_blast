"""Настройки текста визарда: каталог шрифтов для фронта и сверка с правилами рендера.

Правила живут в ОДНОМ месте — движке рендера `app/subtitle_font_layout.py` в корне
репо (+ `config/styles/subtitle_font_*.json`). Сайт не импортирует его пакетом
(у бэкенда свой пакет `app`), а грузит файл по пути: так фронт видит ровно те
шрифты/пары/ограничения, которые примет сборка, и невозможная комбинация
отклоняется на отправке, а не падает на ноде. В образе файлы лежат по тем же
относительным путям (Dockerfile.production).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENGINE_PATH = _REPO_ROOT / "app" / "subtitle_font_layout.py"
# Тайтлы Kant: спека, по которой собирает рендер (app/jsx_subtitles_builder.build_kant_title_overlay)
_KANT_SPEC_PATH = _REPO_ROOT / "5th_template" / "kant_titles" / "kant_titles.json"

# subtitles_mode (WEB_SUBTITLE_MODE_MAP_JSON) → стиль каталога (excluded_styles)
STYLE_BY_MODE = {
    "scenes_3rd": "jakson",
    "scenes_3rd_single_step": "jakson",
    "impulse_2nd": "impulse",
    "template_4th": "tape",
    "trendy_5th": "trendy",
    "brat_5th": "brat",
}
KANT_PREFIX = "kant_"


@lru_cache(maxsize=1)
def kant_spec() -> dict[str, Any]:
    import json

    if not _KANT_SPEC_PATH.exists():
        raise RuntimeError(f"kant titles spec missing: {_KANT_SPEC_PATH}")
    return json.loads(_KANT_SPEC_PATH.read_text(encoding="utf-8"))


def kant_title_by_style() -> dict[str, str]:
    """Стиль сайта (= subtitles_mode, напр. kant_gum) → id тайтла (gum)."""
    return {row["mode"]: tid for tid, row in kant_spec()["titles"].items()}


def is_kant_style(style: str | None) -> bool:
    return bool(style) and str(style) in kant_title_by_style()


def kant_fonts() -> list[str]:
    """Все шрифты тайтлов: оригиналы (латиница) и кириллические замены."""
    out: set[str] = set()
    for row in kant_spec()["titles"].values():
        out.update(row["fonts"])
        out.update(row["cyr"].values())
    return sorted(out)
SIZES = ("large", "medium", "small")
HEIGHTS = ("compact", "normal", "tall")
POSITIONS = ("center", "left", "right", "down")
SHADOWS = ("none", "soft", "strong")
FOCUS_STYLES = ("italic", "bold_italic", "faux_italic")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SubtitleTextError(ValueError):
    """Настройки текста, которые рендер не примет (текст — для человека)."""


@lru_cache(maxsize=1)
def engine() -> ModuleType:
    if not _ENGINE_PATH.exists():
        raise RuntimeError(f"subtitle layout engine missing: {_ENGINE_PATH}")
    name = "blast_subtitle_font_layout"
    spec = importlib.util.spec_from_file_location(name, _ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module      # dataclasses резолвят аннотации через sys.modules
    spec.loader.exec_module(module)
    return module


def font_catalog() -> dict[str, Any]:
    """Для фронта: шрифты с ролями/категорией/засечками/стилями и допустимыми акцентами."""
    eng = engine()
    fonts = []
    for ps, row in eng.load_catalog().items():
        roles = list(row.get("roles") or [])
        if row.get("hidden") or ("base" not in roles and "accent" not in roles):
            continue   # hidden — стандартный шрифт стиля: не выбирается, но пары для него считаются
        fonts.append({
            "ps": ps,
            "label": row.get("label") or ps,
            "category": row.get("category"),
            "roles": roles,
            "serif": row.get("serif") is True,
            "excludedStyles": list(row.get("excluded_styles") or []),
            "accents": eng.accents_for(ps) if "base" in roles else [],
            "lowercase": eng.text_case(ps) == "lower",
        })
    # пары при «стандартном для стиля» шрифте (font = null)
    defaults = dict(eng.STYLE_DEFAULT_FONTS)
    default_accents = {style: eng.accents_for(ps) for style, ps in defaults.items()}
    # у тайтлов Kant шрифт, цвет и анимация зашиты в шаблон — настроек текста нет
    return {"fonts": fonts, "defaults": defaults, "defaultAccents": default_accents,
            "accentColors": dict(eng.STYLE_ACCENT_DEFAULTS), "lockedFontStyles": ["brat"],
            "fixedStyles": sorted(kant_title_by_style())}


def _choice(settings: dict[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = settings.get(key) or default
    if value not in allowed:
        raise SubtitleTextError(f"Неизвестное значение «{key}»: {value!r}")
    return str(value)


def accent_color(settings: dict[str, Any] | None) -> str | None:
    value = (settings or {}).get("accentColor")
    if not value:
        return None
    if not _HEX_RE.match(str(value)):
        raise SubtitleTextError(f"Акцентный цвет должен быть #RRGGBB, получено {value!r}")
    return str(value)


def resolve(settings: dict[str, Any] | None, *, subtitles_mode: str, render_preset: str) -> dict[str, Any] | None:
    """Настройки визарда → `subtitle_text_style` оркестратора для ОДНОЙ вариации.

    Приведение к стилю — явное и описанное в UI: у brat шрифт зафиксирован
    (шрифт/пара/высота не применяются), курсив фокуса — только у brat. Всё, что
    стиль принять не может, — ошибка (сборка проверила бы то же самое позже).
    None — всё по умолчанию: рендер не трогает прод-раскладку.
    """
    settings = dict(settings or {})
    style = STYLE_BY_MODE.get(subtitles_mode)
    if is_kant_style(subtitles_mode):
        # тайтл: цвет акцента рендер тоже отверг бы (jsx_style_config) — ловим на отправке
        if _has_text_settings(settings, accent=True):
            raise SubtitleTextError("У тайтла настройки текста зашиты в шаблон — шрифт, размер, цвет и положение не меняются")
        return None
    if style is None:
        if _has_text_settings(settings):
            raise SubtitleTextError(f"Настройки текста не поддерживаются для режима {subtitles_mode!r}")
        return None
    out, params = _normalize(settings, style)
    eng = engine()
    try:
        _dry_run(eng, style, out, params, render_preset)
    except (ValueError, RuntimeError) as exc:
        raise SubtitleTextError(_human(exc, style)) from exc
    if out == {"size": "large", "height": "normal", "position": "center", "shadow": "soft"}:
        return None
    return out


def _has_text_settings(settings: dict[str, Any], *, accent: bool = False) -> bool:
    """Выбрано что-то кроме значений по умолчанию (accent — считать и акцентный цвет)."""
    keys = ("font", "accentFont", "focusStyle", *(("accentColor",) if accent else ()))
    return any(settings.get(k) for k in keys) or any(
        settings.get(k) not in (None, d) for k, d in
        (("size", "large"), ("height", "normal"), ("position", "center"), ("shadow", "soft")))


def _normalize(settings: dict[str, Any], style: str) -> tuple[dict[str, Any], Any]:
    """Настройки визарда → поля `subtitle_text_style` + параметры движка для стиля."""
    out: dict[str, Any] = {
        "size": _choice(settings, "size", SIZES, "large"),
        "height": _choice(settings, "height", HEIGHTS, "normal"),
        "position": _choice(settings, "position", POSITIONS, "center"),
        "shadow": _choice(settings, "shadow", SHADOWS, "soft"),
    }
    font = settings.get("font") or None
    accent = settings.get("accentFont") or None
    focus_style = settings.get("focusStyle") or None
    if focus_style is not None and focus_style not in FOCUS_STYLES:
        raise SubtitleTextError(f"Неизвестный курсив фокус-слова: {focus_style!r}")
    if style == "brat":
        # шрифт brat зафиксирован (Arial Narrow), пар и высоты нет — не применяются
        out["height"] = "normal"
        if focus_style:
            out["focus_style"] = focus_style
    else:
        if font:
            out["font"] = str(font)
        if accent:
            out["accent_font"] = str(accent)
    params = engine().JaksonTextParams(size=out["size"], height=out["height"], position=out["position"],
                                       shadow=out["shadow"], accent_color=accent_color(settings))
    return out, params


def _dry_run(eng: ModuleType, style: str, out: dict[str, Any], params: Any, render_preset: str) -> None:
    """Та же раскладка, что посчитает сборка, — любая ошибка всплывёт здесь."""
    font = out.get("font") or eng.STYLE_DEFAULT_FONTS.get(style)
    accent = out.get("accent_font")
    if style == "jakson":
        eng.check_style_allowed(font, "jakson")
        if (eng.load_catalog().get(font) or {}).get("category") == "script":
            raise ValueError(f"script font {font!r} cannot be the jakson base font yet")
        eng.jakson_layout(font, accent_font=accent, params=params, render_preset=render_preset)
    elif style == "impulse":
        eng.impulse_layout(font, params=params, render_preset=render_preset, accent_font=accent)
    elif style == "tape":
        eng.tape_layout(font, params=params, render_preset=render_preset, accent_font=accent)
    elif style == "trendy":
        eng.trendy_layout(font, params=params, render_preset=render_preset, accent_font=accent)
    elif style == "brat":
        eng.brat_layout(params=params, render_preset=render_preset, focus_style=out.get("focus_style"))


def _human(exc: Exception, style: str) -> str:
    msg = str(exc)
    if "only for render presets" in msg:
        return "Положение «снизу» доступно только для горизонтальных видео 16:9"
    if "only for serif fonts" in msg:
        return "Высоту букв можно менять только у шрифтов с засечками"
    if "excluded for subtitle style" in msg:
        return f"Выбранный шрифт недоступен для стиля {style}"
    if "font pair not allowed" in msg:
        return "Этот акцентный шрифт не сочетается с выбранным основным"
    if "cannot be the jakson base font" in msg:
        return "Рукописный шрифт нельзя сделать основным в стиле Jakson"
    if "no AE metrics for font" in msg:
        return "Шрифт не найден в каталоге рендера"
    return f"Настройки текста не подходят стилю {style}: {msg}"


# ---------------------------------------------------------------------------
# Геометрия для превью сайта: те же числа, что раскладка рендера отдаёт в AE
# ---------------------------------------------------------------------------

def _font_box(eng: ModuleType, ps: str) -> dict[str, Any]:
    """Метрики шрифта на 100 pt (замер в AE): по ним превью подгоняет чернила
    браузерного шрифта под AE, когда сам шрифт у человека не установлен."""
    m = eng.font_metrics(ps)
    return {
        "capH": m.cap_h, "advance": m.advance_per_char, "lcAdvance": m.lc_advance_per_char,
        "bodyTop": m.body_top, "bodyBottom": m.body_bottom,
        "lowercase": eng.text_case(ps) == "lower",
    }


def _accent_box(a: Any) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"font": a.font, "size": a.size, "tracking": a.tracking, "baselineShift": a.baseline_shift,
            "advance": a.advance_px, "spaceTracking": a.space_tracking}


def geometry(settings: dict[str, Any] | None, *, style: str, render_preset: str) -> dict[str, Any]:
    """Числа раскладки стиля для JS-превью субтитров (`lib/subtitleGeometry.ts`).

    Считает тот же движок, что сборка (`app/subtitle_font_layout.py`): размеры,
    интервалы, поля, центр, трекинг, вместимость строки — превью раскладывает слова
    по правилам AE-шаблона стиля этими числами. Невозможные настройки — та же
    ошибка, что на отправке (SubtitleTextError → 422), а не тихая подмена.
    """
    if is_kant_style(style):
        if _has_text_settings(settings or {}, accent=True):
            raise SubtitleTextError("У тайтла настройки текста зашиты в шаблон — шрифт, размер, цвет и положение не меняются")
        return kant_geometry(style)
    if style not in STYLE_BY_MODE.values():
        raise SubtitleTextError(f"Неизвестный стиль субтитров: {style!r}")
    out, params = _normalize(dict(settings or {}), style)
    eng = engine()
    try:
        _dry_run(eng, style, out, params, render_preset)
        body = _geometry_body(eng, style, out, params, render_preset)
    except (ValueError, RuntimeError) as exc:
        raise SubtitleTextError(_human(exc, style)) from exc
    align, center_y = eng.POSITION_PRESETS[params.position]
    return {
        "style": style,
        "comp": {"w": 1080, "h": 1920},
        "alignX": align,
        "centerY": center_y,
        "marginX": eng.SAFE_MARGIN_X,
        "marginY": eng.SAFE_MARGIN_Y,
        "shadow": params.shadow,
        "accentColor": params.accent_color or eng.STYLE_ACCENT_DEFAULTS.get(style),
        **body,
    }


def _geometry_body(eng: ModuleType, style: str, out: dict[str, Any], params: Any, render_preset: str) -> dict[str, Any]:
    font = out.get("font") or eng.STYLE_DEFAULT_FONTS.get(style)
    accent = out.get("accent_font")
    fonts: dict[str, Any] = {}
    if style == "jakson":
        lay = eng.jakson_layout(font, accent_font=accent, params=params, render_preset=render_preset)
        # TYPE_4 (ударное слово) — тот же пересчёт, что apply_font_layout сборки
        size_focus = round(200.0 * lay.size_base / 80.0, 2)
        for ps in {lay.font_base, lay.font_focus, *([accent] if accent else [])}:
            fonts[ps] = _font_box(eng, ps)
        return {"fonts": fonts, "jakson": {
            "font": lay.font_base, "fontFocus": lay.font_focus,
            "sizeBase": lay.size_base, "sizeLine2": lay.size_line2, "sizeFocus": size_focus,
            "leadingSingle": lay.leading_single, "leadingType1": lay.leading_type1,
            "capHBase": lay.cap_h_base, "verticalScale": lay.vertical_scale, "tracking": -50,
            "advanceBase": lay.advance_base, "lineCharsType1": lay.line_chars_type1,
            "lineCharsTwoGroups": lay.line_chars_two_groups, "accent": _accent_box(lay.accent),
        }}
    if style == "impulse":
        lay = eng.impulse_layout(font, params=params, render_preset=render_preset, accent_font=accent)
        fonts[lay.font] = _font_box(eng, lay.font)
        acc = None
        if lay.accent is not None:
            fonts[lay.accent.font] = _font_box(eng, lay.accent.font)
            acc = {"font": lay.accent.font, "size": lay.accent.size, "stroke": lay.accent.stroke_px,
                   "anchorY": lay.accent.anchor_y, "advance": lay.accent.advance_px}
        return {"fonts": fonts, "impulse": {
            "font": lay.font, "size": lay.size, "stroke": lay.stroke_px, "anchorY": lay.anchor_y,
            "advance": lay.advance_px, "safeWidth": lay.safe_width, "verticalScale": lay.vertical_scale,
            "tracking": eng.IMPULSE_TRACKING, "longHold": eng.IMPULSE_LONG_HOLD_SCALE,
            "shortMinPeak": eng.IMPULSE_SHORT_MIN_PEAK, "accent": acc,
            "positionLong": lay.position(short=False), "positionShort": lay.position(short=True),
        }}
    if style == "tape":
        lay = eng.tape_layout(font, params=params, render_preset=render_preset, accent_font=accent)
        fonts[lay.font] = _font_box(eng, lay.font)
        if lay.accent is not None:
            fonts[lay.accent.font] = _font_box(eng, lay.accent.font)
        return {"fonts": fonts, "tape": {
            "font": lay.font, "size": lay.size, "leading": lay.leading, "advance": lay.advance_px,
            "fauxItalic": lay.faux_italic, "case": lay.case, "verticalScale": lay.vertical_scale,
            "tracking": eng.TAPE_TRACKING, "boxW": eng.TAPE_BOX_W,
            "focusColor": "#{:02X}{:02X}{:02X}".format(*(round(c * 255) for c in lay.focus_rgb)),
            "accent": _accent_box(lay.accent),
        }}
    if style == "trendy":
        lay = eng.trendy_layout(font, params=params, render_preset=render_preset, accent_font=accent)
        fonts[lay.base.font] = _font_box(eng, lay.base.font)
        if lay.accent is not None:
            fonts[lay.accent.font] = _font_box(eng, lay.accent.font)
        return {"fonts": fonts, "trendy": lay.jsx_config()}
    lay = eng.brat_layout(params=params, render_preset=render_preset, focus_style=out.get("focus_style"))
    # brat_subtitles.jsx: трекинг −20, по 2 слова в строке, до 4 строк, слой 80%, fit 0.97
    return {"fonts": fonts, "brat": {**lay.jsx_config(), "layerScale": eng.BRAT_LAYER_SCALE, "tracking": -20,
                                     "wordsPerLine": 2, "maxLines": 4, "fitMargin": 0.97,
                                     "focusStyle": out.get("focus_style")}}



def kant_geometry(style: str) -> dict[str, Any]:
    """Тайтл Kant для JS-превью: шаблон текста, вход, разбиение на экраны — из той же
    спеки, что читает рендер. Раскладка всегда по центру кадра, поверх всех слоёв."""
    spec = kant_spec()
    tid = kant_title_by_style()[style]
    row = spec["titles"][tid]
    fonts = sorted({*row["fonts"], *row["cyr"].values()})
    return {
        "style": style,
        "comp": {"w": 1080, "h": 1920},
        "alignX": "center",
        "centerY": 0.5,
        "marginX": 0.0,
        "marginY": 0.0,
        "shadow": "none",
        "accentColor": None,
        "fonts": {ps: {"capH": 0, "advance": 0, "lcAdvance": None, "bodyTop": None, "bodyBottom": None,
                       "lowercase": False} for ps in fonts},
        "kant": {
            "id": tid,
            "fonts": list(row["fonts"]),
            "cyr": dict(row["cyr"]),
            "cyrSqueeze": row.get("cyrSqueeze"),
            "intro": float(row["intro"]),
            "text": dict(row["text"]),
            "flashes": list(row.get("flashes") or []),
            "look": row["look"],
            "split": dict(spec["split"]),
            "phrase": dict(spec["phrase"]),
        },
    }
