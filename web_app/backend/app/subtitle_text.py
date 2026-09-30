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

# subtitles_mode (WEB_SUBTITLE_MODE_MAP_JSON) → стиль каталога (excluded_styles)
STYLE_BY_MODE = {
    "scenes_3rd": "jakson",
    "scenes_3rd_single_step": "jakson",
    "impulse_2nd": "impulse",
    "template_4th": "tape",
    "trendy_5th": "trendy",
    "brat_5th": "brat",
}
# шрифт «стандартный для стиля» (прод) — как в app/subtitle_text_style.py сборки
DEFAULT_FONT_BY_STYLE = {
    "jakson": "Point-SemiBold",
    "impulse": "Point-Light",
    "tape": "Montserrat-BoldItalic",
    "trendy": "Montserrat-Bold",
}
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
    default_accents = {style: eng.accents_for(ps) for style, ps in DEFAULT_FONT_BY_STYLE.items()}
    return {"fonts": fonts, "defaults": DEFAULT_FONT_BY_STYLE, "defaultAccents": default_accents,
            "lockedFontStyles": ["brat"]}


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
    if style is None:
        if any(settings.get(k) for k in ("font", "accentFont", "focusStyle")) or any(
                settings.get(k) not in (None, d) for k, d in
                (("size", "large"), ("height", "normal"), ("position", "center"), ("shadow", "soft"))):
            raise SubtitleTextError(f"Настройки текста не поддерживаются для режима {subtitles_mode!r}")
        return None
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
    eng = engine()
    params = eng.JaksonTextParams(size=out["size"], height=out["height"], position=out["position"],
                                  shadow=out["shadow"], accent_color=accent_color(settings))
    try:
        _dry_run(eng, style, out, params, render_preset)
    except (ValueError, RuntimeError) as exc:
        raise SubtitleTextError(_human(exc, style)) from exc
    if out == {"size": "large", "height": "normal", "position": "center", "shadow": "soft"}:
        return None
    return out


def _dry_run(eng: ModuleType, style: str, out: dict[str, Any], params: Any, render_preset: str) -> None:
    """Та же раскладка, что посчитает сборка, — любая ошибка всплывёт здесь."""
    font = out.get("font") or DEFAULT_FONT_BY_STYLE.get(style)
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
