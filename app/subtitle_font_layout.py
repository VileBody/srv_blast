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
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

METRICS_PATH = Path(__file__).resolve().parents[1] / "config" / "styles" / "subtitle_font_metrics.json"

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


@dataclass(frozen=True)
class FontMetrics:
    ps: str
    cap_h: float
    accent_top: float
    desc_bottom: float
    advance_per_char: float

    def per_pt(self, value: float, size: float) -> float:
        return value * size / 100.0


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
        )
    return out


def font_metrics(ps: str, *, path: Path = METRICS_PATH) -> FontMetrics:
    table = _load_metrics(str(path))
    m = table.get(ps)
    if m is None:
        raise RuntimeError(f"no AE metrics for font {ps!r} in {path} (measured: {sorted(table)})")
    return m


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

    def leading_for(self, text: str, *, type1: bool) -> float:
        """Интервал для конкретного текста (строки разделены символом CR, как в AE).

        Обычный зазор по Point; раздвигаем только при реальной паре
        «выносная в строке i» + «надстрочная в строке i+1».
        """
        s1 = self.size_base
        s2 = self.size_line2 if type1 else self.size_base
        m = self.metrics
        plain = m.per_pt(m.cap_h, s2) + (self.gap_type1 if type1 else self.gap_single) * self.cap_h_base
        lines = [ln.upper() for ln in text.split("\r")]
        clash = any(
            DESC_CHARS & set(a) and ACCENT_CHARS & set(b)
            for a, b in zip(lines, lines[1:])
        )
        if not clash:
            return round(plain, 2)
        safe = m.per_pt(m.desc_bottom, s1) + m.per_pt(m.accent_top, s2) + MIN_CLEARANCE_RATIO * self.cap_h_base
        return round(max(plain, safe), 2)

    def layout_box(self, *, n_lines: int, leading: float) -> Dict[str, Any]:
        """Коробка прописных в координатах слоя (базовая линия 1-й строки = y 0)."""
        return {
            "cap_top": -self.cap_h_base,
            "baseline_last": float(max(0, n_lines - 1)) * leading,
            "margin_x": self.margin_x,
            "margin_y": self.margin_y,
        }


def jakson_layout(font_base: str, font_focus: Optional[str] = None, *, path: Path = METRICS_PATH) -> JaksonLayout:
    ref = font_metrics(REFERENCE_FONT, path=path)
    base = font_metrics(font_base, path=path)
    focus = font_metrics(font_focus or font_base, path=path)

    # 1) размеры по видимой высоте прописных (Point = эталон)
    k = ref.cap_h / base.cap_h
    size_base = round(REF_SIZE_BASE * k, 2)
    size_line2 = round(REF_SIZE_LINE2 * k, 2)
    size_focus_base = round(REF_SIZE_BASE * ref.cap_h / focus.cap_h, 2)

    # 2) межстрочный: доля зазора из прод-раскладки Point
    ref_cap80 = ref.per_pt(ref.cap_h, REF_SIZE_BASE)
    gap_single = (REF_LEADING_SINGLE - ref_cap80) / ref_cap80
    gap_type1 = (REF_LEADING_TYPE1 - ref.per_pt(ref.cap_h, REF_SIZE_LINE2)) / ref_cap80

    cap_h_base = base.per_pt(base.cap_h, size_base)
    leading_single = round(base.per_pt(base.cap_h, size_base) + gap_single * cap_h_base, 2)
    leading_type1 = round(base.per_pt(base.cap_h, size_line2) + gap_type1 * cap_h_base, 2)

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
        cap_h_focus=round(focus.per_pt(focus.cap_h, size_focus_base), 2),
        advance_base=round(adv_base, 2),
        advance_focus=round(focus.per_pt(focus.advance_per_char, size_focus_base), 2),
        margin_x=SAFE_MARGIN_X,
        margin_y=SAFE_MARGIN_Y,
        line_chars_type1=max(6, int(REF_LINE_CHARS_TYPE1 / width_ratio)),
        line_chars_two_groups=max(6, int(REF_LINE_CHARS_TWO_GROUPS / width_ratio)),
        metrics=base,
        gap_single=gap_single,
        gap_type1=gap_type1,
    )
