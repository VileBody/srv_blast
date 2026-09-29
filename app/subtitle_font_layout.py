"""Раскладка субтитров jakson от метрик шрифта, а не от чисел под Point.

Старые константы (`size_base=80`, `size_line2=120`, `leading=88/115`, поле 5%,
якорь TYPE_4 `-33.5`, «50 px на букву») подобраны под Point-SemiBold. У другого
шрифта при тех же pt другая высота прописных, другие выносные и Й/Ё, другая
ширина знака — строки слипаются, группа съезжает по вертикали, строка вылезает.

Здесь все размеры выводятся из МЕТРИК, снятых в самом AE
(`scripts/subtitle_font_lab/measure_font_metrics.jsx` →
`config/styles/subtitle_font_metrics.json`, px на 100 pt):

- `cap_h`      — высота прописной «Н» над базовой линией;
- `accent_top` — верх «ЙЁ» над базовой линией (надстрочные знаки);
- `desc_bottom`— низ «ДЩЦ» под базовой линией (выносные прописных);
- `advance_per_char` — средняя ширина знака капсом с трекингом jakson.

Правила (все — доли высоты прописной первой строки, т.е. масштабно-инвариантны):

1. Размер: pt подбираются так, чтобы видимая высота прописных совпала с Point
   на тех же ролях (80 / 120 / фокус). Point остаётся эталоном: его раскладка
   не меняется.
2. Межстрочный интервал: зазор «низ прописных строки 1 → верх прописных строки 2»
   берётся из Point (`gap_ratio`). Только если в строке 1 есть выносные (Д/Ц/Щ),
   а в строке 2 — надстрочные (Й/Ё), интервал раздвигается ровно настолько,
   чтобы они не касались (`MIN_CLEARANCE_RATIO`). Замер показал, что даже у
   Point при прод-интервале 115 «Ё» второй строки заходит в зону «Д» первой.
   Остальные сцены сохраняют обычный зазор — строки не «гуляют» зря.
3. Центрирование: по коробке прописных (верх прописных первой строки → базовая
   линия последней), а не по чернильной рамке. Иначе группа прыгает по высоте
   от того, есть ли в сцене Й, Ё или Д.
4. Поля: текст (с учётом масштаба слоя) обязан влезть в кадр минус поля по X и Y;
   не влез — равномерно уменьшаем. Поля шире прежних 5%, чтобы был воздух и
   тень не упиралась в край.

Нет метрик для шрифта → ошибка, а не подстановка (No Fallback, AGENTS.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

METRICS_PATH = Path(__file__).resolve().parents[1] / "config" / "styles" / "subtitle_font_metrics.json"
TUNING_PATH = Path(__file__).resolve().parents[1] / "config" / "styles" / "subtitle_font_tuning.json"
CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "styles" / "subtitle_font_catalog.json"
_TUNING_KEYS = {"line_gap_mult", "accent_scale", "accent_baseline_shift", "accent_space_tracking", "note"}
# Тело строчных акцента (ink «о») = высоте прописных основного и стоит по их
# центру: акцент в балансе с капсом, росчерки уходят поверх соседнего текста.
# (0.85 давало акцент заметно мельче и легче капса — смотр пар 2026-09-29.)
ACCENT_X_H_RATIO = 1.0

REFERENCE_FONT = "Point-SemiBold"

# Эталонная (прод) раскладка jakson под Point — из неё выводятся доли.
REF_SIZE_BASE = 80.0
REF_SIZE_LINE2 = 120.0
REF_SIZE_FOCUS = 200.0
REF_LEADING_SINGLE = 88.0       # две строки одного размера (TYPE_2/5/6)
REF_LEADING_TYPE1 = 115.0       # 80 → 120 (TYPE_1)

# Минимальный просвет между выносными строки 1 и надстрочными строки 2.
MIN_CLEARANCE_RATIO = 0.10
# Поля кадра (доля ширины/высоты композиции с каждой стороны).
SAFE_MARGIN_X = 0.07
SAFE_MARGIN_Y = 0.10
# Капс-строка TYPE_1 в промпте: ≤ 12 знаков у Point, TYPE_5/6 — ≤ 13.
REF_LINE_CHARS_TYPE1 = 12
REF_LINE_CHARS_TWO_GROUPS = 13
# Капс-кириллица: выносные вниз / надстрочные вверх.
DESC_CHARS = frozenset("ДЦЩ")
ACCENT_CHARS = frozenset("ЙЁ")

# ---------------------------------------------------------------------------
# Пользовательские параметры текста (визард) — именованные пресеты. Числа живут
# ТОЛЬКО здесь, на стороне рендера; фронт шлёт имена.
# ---------------------------------------------------------------------------
# Имена — как в визарде (web_app/frontend/src/stores/wizardStore.ts, SubtitleTextSettings).
# размер: только МЕНЬШЕ авто-максимума jakson (large = авто, прод)
SIZE_PRESETS = {"large": 1.0, "medium": 0.9, "small": 0.8}
# высота: вертикальное растяжение букв (AE verticalScale); ширина букв всегда 100%
HEIGHT_PRESETS = {"compact": 0.8, "normal": 1.0, "tall": 1.3}
# позиция: (выравнивание по X, центр группы по Y в долях высоты текст-компа 1080×1920)
POSITION_PRESETS = {
    "center": ("center", 0.50),
    "left": ("left", 0.50),
    "right": ("right", 0.50),
    # только для 16:9: текст-комп вложен в кадр 1920×1080, видна его середина
    # (y 420..1500) — 0.64 ≈ нижняя треть видимого кадра
    "down": ("center", 0.64),
}
POSITION_ONLY_FOR_PRESETS = {"down": {"wide"}}
# тень (Sapphire S_DropShadow): soft = прод-значения
SHADOW_PRESETS = {"none": None, "soft": {"opacity": 2.0, "blur": 60.0}, "strong": {"opacity": 4.0, "blur": 34.0}}
JUSTIFICATION_CODES = {"left": "7413", "right": "7414", "center": "7415"}
# Обводки нет сознательно (смотр 2026-09-29: нигде не выглядит хорошо).


def hex_to_rgb01(value: str) -> list:
    s = str(value or "").strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"color must be #RRGGBB, got {value!r}")
    return [round(int(s[i:i + 2], 16) / 255.0, 5) for i in (0, 2, 4)]


@dataclass(frozen=True)
class JaksonTextParams:
    """Выбор пользователя для jakson. Неизвестный пресет — ошибка (No Fallback).

    Цвета: в кадре не больше ДВУХ — основной (заливка, SUBTITLES_FORCE_FILL_HEX) и
    один акцентный. accent_color красит и акцентное слово пары (TYPE_2), и ударное
    слово (TYPE_4). None — прод: акцент как основной текст, ударное — красное.
    """

    size: str = "large"
    height: str = "normal"
    position: str = "center"
    shadow: str = "soft"
    accent_color: Optional[str] = None

    def __post_init__(self) -> None:
        for name, table in (("size", SIZE_PRESETS), ("height", HEIGHT_PRESETS),
                            ("position", POSITION_PRESETS), ("shadow", SHADOW_PRESETS)):
            if getattr(self, name) not in table:
                raise ValueError(f"unknown {name} preset {getattr(self, name)!r} (allowed: {sorted(table)})")
        if self.accent_color is not None:
            hex_to_rgb01(self.accent_color)

    def check_render_preset(self, render_preset: str) -> None:
        allowed = POSITION_ONLY_FOR_PRESETS.get(self.position)
        if allowed is not None and render_preset not in allowed:
            raise ValueError(f"position {self.position!r} is only for render presets {sorted(allowed)}, got {render_preset!r}")


@dataclass(frozen=True)
class FontMetrics:
    ps: str
    cap_h: float
    accent_top: float
    desc_bottom: float
    advance_per_char: float
    # строчные (акцентное слово пары; трекинг 0) — None, если не мерили
    x_h: Optional[float] = None
    x_bottom: Optional[float] = None      # низ ink «о» под базовой линией (≈0; у части скриптов нет)
    body_top: Optional[float] = None      # тело строчных: медиана верха букв н,а,м,е,с,о
    body_bottom: Optional[float] = None   # тело строчных: низ (+ = под базовой)
    lc_asc_top: Optional[float] = None
    lc_desc_bottom: Optional[float] = None
    lc_advance_per_char: Optional[float] = None

    def per_pt(self, value: float, size: float) -> float:
        return value * size / 100.0

    def require_lowercase(self) -> None:
        if None in (self.x_h, self.x_bottom, self.body_top, self.body_bottom, self.lc_asc_top, self.lc_desc_bottom, self.lc_advance_per_char):
            raise RuntimeError(f"no lowercase AE metrics for accent font {self.ps!r} — re-run measure_font_metrics.jsx")


@lru_cache(maxsize=1)
def _load_metrics(path_str: str) -> Dict[str, FontMetrics]:
    path = Path(path_str)
    if not path.exists():
        raise RuntimeError(
            f"subtitle font metrics missing: {path} — run scripts/subtitle_font_lab/measure_font_metrics.jsx in AE"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if float(raw.get("size_pt") or 0) != 100.0:
        raise RuntimeError(f"subtitle font metrics must be measured at 100 pt: {path}")
    out: Dict[str, FontMetrics] = {}
    for row in raw.get("fonts") or []:
        if not row.get("ok"):
            continue
        out[str(row["ps"])] = FontMetrics(
            ps=str(row["ps"]),
            cap_h=float(row["cap_h"]),
            accent_top=float(row["accent_top"]),
            desc_bottom=float(row["desc_bottom"]),
            advance_per_char=float(row["advance_per_char"]),
            **{
                k: (float(row[k]) if row.get(k) is not None else None)
                for k in ("x_h", "x_bottom", "body_top", "body_bottom", "lc_asc_top", "lc_desc_bottom", "lc_advance_per_char")
            },
        )
    return out


def font_tuning(ps: str, *, path: Path = TUNING_PATH) -> Dict[str, Any]:
    """Ручные поправки поверх замеров (что глаз видит, а метрика нет)."""
    if not path.exists():
        return {}
    table = json.loads(path.read_text(encoding="utf-8")).get("fonts") or {}
    tune = dict(table.get(ps) or {})
    unknown = set(tune) - _TUNING_KEYS
    if unknown:
        raise RuntimeError(f"unknown tuning keys for {ps!r} in {path}: {sorted(unknown)}")
    return tune


def font_metrics(ps: str, *, path: Path = METRICS_PATH) -> FontMetrics:
    table = _load_metrics(str(path))
    m = table.get(ps)
    if m is None:
        raise RuntimeError(f"no AE metrics for font {ps!r} in {path} (measured: {sorted(table)})")
    return m


@dataclass(frozen=True)
class AccentLayout:
    """Акцентное слово пары: другой шрифт внутри строки основного.

    Основной шрифт — капсом, акцент — строчными (скрипты в капсе разваливаются).
    Размер: ink-высота тела строчных акцента (медиана по буквам н,а,м,е,с,о — рамку слова
    раздувают соединительные штрихи, одна «о» у скриптов бывает крошечной) =
    ACCENT_X_H_RATIO × высота прописных основного (× ручной accent_scale).
    Сдвиг по вертикали: центр тела встаёт на центр прописных (+ ручной accent_baseline_shift в долях прописных, плюс = вверх).
    Трекинг 0 — рукописные буквы соединяются, −50 их рвёт. Росчерки акцента НЕ
    раздвигают строки: они ложатся поверх соседнего текста (глубина, а не ломка
    композиции). Пробелы вокруг акцента — ручной трекинг (у скриптов бывают
    большие отрицательные отступы, слово липнет к соседу).
    """

    font: str
    size: float
    tracking: float
    baseline_shift: float      # px, AE baselineShift (плюс = вверх)
    asc_px: float              # чернила над базовой линией строки (с учётом сдвига)
    desc_px: float             # чернила под базовой линией строки (с учётом сдвига)
    advance_px: float
    space_tracking: float      # трекинг пробелов до/после акцентного слова


@dataclass(frozen=True)
class JaksonLayout:
    """Числа раскладки jakson для пары (основной, фокусный) шрифт."""

    font_base: str
    font_focus: str
    size_base: float
    size_line2: float
    size_focus_base: float     # TYPE_4: фокусный шрифт на месте size_base
    leading_single: float
    leading_type1: float
    cap_h_base: float          # px, прописные первой строки при size_base
    cap_h_focus: float         # px, прописные TYPE_4 при size_focus_base
    advance_base: float        # px на знак при size_base
    advance_focus: float       # px на знак TYPE_4
    margin_x: float
    margin_y: float
    line_chars_type1: int
    line_chars_two_groups: int
    metrics: FontMetrics
    gap_single: float
    gap_type1: float
    line_gap_mult: float = 1.0
    accent: Optional[AccentLayout] = None
    params: JaksonTextParams = JaksonTextParams()

    @property
    def align_x(self) -> str:
        return POSITION_PRESETS[self.params.position][0]

    @property
    def center_y_frac(self) -> float:
        return POSITION_PRESETS[self.params.position][1]

    @property
    def justification_code(self) -> str:
        return JUSTIFICATION_CODES[self.align_x]

    @property
    def vertical_scale(self) -> float:
        return HEIGHT_PRESETS[self.params.height]

    @property
    def shadow(self) -> Optional[Dict[str, float]]:
        return SHADOW_PRESETS[self.params.shadow]

    def leading_for(self, text: str, *, type1: bool, accent_word: Optional[str] = None) -> float:
        """Интервал для конкретного текста (строки разделены символом CR, как в AE).

        Обычный зазор по Point; раздвигаем только при реальном риске касания
        букв основного шрифта: выносная Д/Ц/Щ в строке i против Й/Ё в строке i+1.
        Акцентное слово (accent_word) из проверки исключается целиком: его
        росчерки намеренно ложатся поверх соседних строк.
        """
        s1 = self.size_base
        s2 = self.size_line2 if type1 else self.size_base
        m = self.metrics
        cap_h2 = m.per_pt(m.cap_h, s2)
        plain = cap_h2 + (self.gap_type1 if type1 else self.gap_single) * self.cap_h_base
        aw = (accent_word or "").lower()
        lines = [
            " ".join(w for w in ln.split(" ") if not (aw and w.lower() == aw)).upper()
            for ln in text.split("\r")
        ]
        leading = plain
        for a, b in zip(lines, lines[1:]):
            bottom = m.per_pt(m.desc_bottom, s1) if DESC_CHARS & set(a) else 0.0
            top = m.per_pt(m.accent_top, s2) if ACCENT_CHARS & set(b) else cap_h2
            if bottom > 0.0 or top > cap_h2:
                leading = max(leading, bottom + top + MIN_CLEARANCE_RATIO * self.cap_h_base)
        # ручная подстройка (subtitle_font_tuning.json): множитель видимого зазора
        return round(cap_h2 + self.line_gap_mult * (leading - cap_h2), 2)

    def layout_box(self, *, n_lines: int, leading: float) -> Dict[str, Any]:
        """Коробка прописных в координатах слоя (базовая линия 1-й строки = y 0)."""
        return {
            "cap_top": -self.cap_h_base,
            "baseline_last": float(max(0, n_lines - 1)) * leading,
            "margin_x": self.margin_x,
            "margin_y": self.margin_y,
            "align_x": self.align_x,
            "center_y": self.center_y_frac,
        }


def accent_layout(accent_font: str, *, cap_h_base: float, path: Path = METRICS_PATH,
                  tuning_path: Path = TUNING_PATH) -> AccentLayout:
    m = font_metrics(accent_font, path=path)
    m.require_lowercase()
    tune = font_tuning(accent_font, path=tuning_path)
    body_ink = m.body_top + m.body_bottom          # ink-высота тела строчных на 100 pt
    size = ACCENT_X_H_RATIO * cap_h_base / (body_ink / 100.0) * float(tune.get("accent_scale", 1.0))
    body_center = m.per_pt(m.body_top - m.body_bottom, size) / 2.0   # центр тела над базовой линией
    shift = cap_h_base / 2.0 - body_center + float(tune.get("accent_baseline_shift", 0.0)) * cap_h_base
    return AccentLayout(
        font=accent_font,
        size=round(size, 2),
        tracking=0.0,
        baseline_shift=round(shift, 2),
        asc_px=round(m.per_pt(m.lc_asc_top, size) + shift, 2),
        desc_px=round(max(0.0, m.per_pt(m.lc_desc_bottom, size) - shift), 2),
        advance_px=round(m.per_pt(m.lc_advance_per_char, size), 2),
        space_tracking=float(tune.get("accent_space_tracking", 0.0)),
    )


def jakson_layout(font_base: str, font_focus: Optional[str] = None, *, accent_font: Optional[str] = None,
                  params: Optional[JaksonTextParams] = None, render_preset: str = "vertical",
                  path: Path = METRICS_PATH) -> JaksonLayout:
    if accent_font:
        check_pair(font_base, accent_font)
    ref = font_metrics(REFERENCE_FONT, path=path)
    base = font_metrics(font_base, path=path)
    focus = font_metrics(font_focus or font_base, path=path)

    params = params or JaksonTextParams()
    params.check_render_preset(render_preset)
    if params.height != "normal" and not allows_height_stretch(font_base):
        raise ValueError(f"height {params.height!r} is only for serif fonts (catalog 'serif': true), got {font_base!r}")
    v = HEIGHT_PRESETS[params.height]
    # 1) размеры по видимой высоте прописных (Point = эталон) × пресет размера.
    #    Растяжение по высоте (v) размер в pt НЕ меняет — ширина букв остаётся той же,
    #    растут только вертикальные метрики (ниже: base_v).
    k = ref.cap_h / base.cap_h * SIZE_PRESETS[params.size]
    size_base = round(REF_SIZE_BASE * k, 2)
    size_line2 = round(REF_SIZE_LINE2 * k, 2)
    size_focus_base = round(REF_SIZE_BASE * ref.cap_h / focus.cap_h * SIZE_PRESETS[params.size], 2)

    # 2) межстрочный: доля зазора из прод-раскладки Point
    ref_cap80 = ref.per_pt(ref.cap_h, REF_SIZE_BASE)   # доли зазора — от прод-раскладки (без пресета)
    gap_single = (REF_LEADING_SINGLE - ref_cap80) / ref_cap80
    gap_type1 = (REF_LEADING_TYPE1 - ref.per_pt(ref.cap_h, REF_SIZE_LINE2)) / ref_cap80

    base_v = replace(base, cap_h=base.cap_h * v, accent_top=base.accent_top * v, desc_bottom=base.desc_bottom * v)
    cap_h_base = base_v.per_pt(base_v.cap_h, size_base)
    leading_single = round(base_v.per_pt(base_v.cap_h, size_base) + gap_single * cap_h_base, 2)
    leading_type1 = round(base_v.per_pt(base_v.cap_h, size_line2) + gap_type1 * cap_h_base, 2)

    # 3) вместимость строки для промпта: у Point 12/13 знаков, шире шрифт → меньше
    adv_base = base.per_pt(base.advance_per_char, size_base)
    width_ratio = adv_base / ref.per_pt(ref.advance_per_char, REF_SIZE_BASE)

    return JaksonLayout(
        font_base=font_base,
        font_focus=font_focus or font_base,
        size_base=size_base,
        size_line2=size_line2,
        size_focus_base=size_focus_base,
        leading_single=leading_single,
        leading_type1=leading_type1,
        cap_h_base=round(cap_h_base, 2),
        cap_h_focus=round(focus.per_pt(focus.cap_h, size_focus_base) * v, 2),
        advance_base=round(adv_base, 2),
        advance_focus=round(focus.per_pt(focus.advance_per_char, size_focus_base), 2),
        margin_x=SAFE_MARGIN_X,
        margin_y=SAFE_MARGIN_Y,
        line_chars_type1=max(6, int(REF_LINE_CHARS_TYPE1 / width_ratio)),
        line_chars_two_groups=max(6, int(REF_LINE_CHARS_TWO_GROUPS / width_ratio)),
        metrics=base_v,
        gap_single=gap_single,
        gap_type1=gap_type1,
        line_gap_mult=float(font_tuning(font_base).get("line_gap_mult", 1.0)),
        accent=accent_layout(accent_font, cap_h_base=cap_h_base, path=path) if accent_font else None,
        params=params,
    )


# ---------------------------------------------------------------------------
# Каталог и правила пар (config/styles/subtitle_font_catalog.json)
# ---------------------------------------------------------------------------

def load_catalog(path: Path = CATALOG_PATH) -> Dict[str, Dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cats = set(raw.get("categories") or {})
    out: Dict[str, Dict[str, Any]] = {}
    for row in raw.get("fonts") or []:
        ps = str(row["ps"])
        if ps in out:
            raise RuntimeError(f"duplicate font {ps!r} in {path}")
        if row.get("category") not in cats:
            raise RuntimeError(f"font {ps!r}: unknown category {row.get('category')!r} in {path}")
        for c in row.get("pairs_with_categories") or []:
            if c not in cats:
                raise RuntimeError(f"font {ps!r}: unknown pairs_with category {c!r} in {path}")
        out[ps] = dict(row)
    return out


def pair_block_reason(font_base: str, accent_font: str, *, path: Path = CATALOG_PATH) -> Optional[str]:
    """None — пара разрешена; иначе человекочитаемая причина запрета."""
    cat = load_catalog(path)
    base, acc = cat.get(font_base), cat.get(accent_font)
    if base is None or "base" not in (base.get("roles") or []):
        return f"{font_base!r} is not a base font in the catalog"
    if acc is None or "accent" not in (acc.get("roles") or []):
        return f"{accent_font!r} is not an accent font in the catalog"
    if base.get("category") == "script":
        return f"{font_base!r} is a script used as base — accents are disabled"
    if base.get("pairable") is False:
        return f"{font_base!r} is self-sufficient (pairable=false)"
    allowed = base.get("allowed_accents")
    if allowed is not None and accent_font not in allowed:
        return f"{font_base!r} allows only accents {allowed}"
    if base.get("category") not in (acc.get("pairs_with_categories") or []):
        return f"{accent_font!r} does not pair with category {base.get('category')!r}"
    return None


def accents_for(font_base: str, *, path: Path = CATALOG_PATH) -> List[str]:
    """Акценты, которые фронт показывает для выбранного основного (пусто → блок скрыт)."""
    return [ps for ps, row in load_catalog(path).items()
            if "accent" in (row.get("roles") or []) and pair_block_reason(font_base, ps, path=path) is None]


def is_pairable(font_base: str) -> bool:
    return bool(accents_for(font_base))


def allows_height_stretch(font_base: str, *, path: Path = CATALOG_PATH) -> bool:
    """Растяжение по высоте — только шрифтам с засечками (catalog "serif": true)."""
    return load_catalog(path).get(font_base, {}).get("serif") is True


def check_pair(font_base: str, accent_font: str) -> None:
    """Запрещённая пара — ошибка на границе, не молчаливая подмена (No Fallback)."""
    reason = pair_block_reason(font_base, accent_font)
    if reason:
        raise RuntimeError(f"font pair not allowed: {reason} (subtitle_font_catalog.json)")
