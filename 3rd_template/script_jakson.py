#!/usr/bin/env python3
"""
build_layers.py — конвертирует scenes.json в text_layers для render.jsx

Использование:
    python build_layers.py --scenes scenes.json --jsx render_template.jsx --out render_out.jsx

    # Только вывести text_layers как JSON (для отладки):
    python build_layers.py --scenes scenes.json --dump

Типы сцен:
    TYPE_1  — 3–4 слова, две строки, нет акцента
    TYPE_2  — 4–5 слов, одно фокус-слово курсивом (ExtraBold)
    TYPE_3  — нарастающие слои: "his" → "his eyes" → "his eyes were"
    TYPE_4  — одно слово, красный цвет, precomp-структура
    TYPE_5  — 4–5 слов, outline + fill layers, поэтапное наполнение
    TYPE_6  — 2 группы, поочерёдное появление без выбивания
"""

import argparse
import json
import re
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Константы рендера
# ---------------------------------------------------------------------------

RENDER = {
    "comp_w":          1080,
    "comp_h":          1920,
    "fps":             23.976,
    "font_base":       "Point-SemiBold",
    "font_focus":      "Point-ExtraBold",
    "size_base":       80,            # первая строка
    "size_line2":      120,           # вторая строка TYPE_1 — чуть крупнее
    "size_focus":      200,           # TYPE_4 mine layer
    "color_white":     [1, 1, 1],
    "color_red":       [0.99216, 0.08627, 0.07843],
    "tracking":        -50,
    "leading":         88,            # для однострочных типов (TYPE_2/3/5/6)
    # TYPE_1 leading вычисляется динамически: int((size_base + size_line2) / 2 * 1.15)
    # При 80+120 → среднее 100 × 1.15 = 115
    "stroke_width":    5,
    "first_line_indent": 0,
    "comp_text":       "Текст",
    "comp_mine":       'Текст "Mine"',
    "scale_default":   [100, 100, 100],
}

# Leading для TYPE_1 — пропорционален среднему между двумя размерами строк
TYPE1_LEADING = int((RENDER["size_base"] + RENDER["size_line2"]) / 2 * 1.15)  # = 115


def compute_comp_dur(scenes: List[Dict], tail_buffer: float = 2.0) -> float:
    """
    Вычисляет длительность компа из таймингов сцен.
    tail_buffer — секунды после последней сцены (чтобы не обрезало).
    """
    if not scenes:
        return 10.0
    max_end = max(s["end"] for s in scenes)
    # Округляем вверх до кратного кадру
    frames = int(max_end * RENDER["fps"]) + int(tail_buffer * RENDER["fps"]) + 1
    return round(frames / RENDER["fps"], 6)


FRAME = 1.0 / RENDER["fps"]   # ~0.04171s

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def kf(t: float, v: Any,
       iit: str = "6612", oit: str = "6612",
       ease_in: Optional[List] = None,
       ease_out: Optional[List] = None) -> Dict:
    """Создаёт keyframe-запись."""
    return {
        "t": round(t, 7),
        "v": v,
        "iit": iit, "oit": oit,
        "ease_in":  ease_in  or [{"speed": 0.0, "influence": 16.666666667}],
        "ease_out": ease_out or [{"speed": 0.0, "influence": 16.666666667}],
    }


def kf_ease(t: float, v: Any, speed_out: float = 0.0, speed_in: float = 0.0) -> Dict:
    """Keyframe с ease-in/out по скорости."""
    return kf(
        t, v,
        ease_in=  [{"speed": speed_in,  "influence": 16.666666667}],
        ease_out= [{"speed": speed_out, "influence": 16.666666667}],
    )


def prop(match_name: str, value: Any = None,
         keyframes: Optional[List] = None,
         expression: Optional[str] = None) -> Dict:
    """Создаёт prop-запись."""
    return {
        "match_name": match_name,
        "value": value if not keyframes else None,
        "keyframes": keyframes or [],
        "expression": expression,
    }


def base_transforms(anchor=None, position=None, scale=None) -> Dict:
    """Возвращает стандартный набор трансформов."""
    cx, cy = RENDER["comp_w"] / 2, RENDER["comp_h"] / 2
    return {
        "tf_anchor":   prop("ADBE Anchor Point", anchor   or [cx, cy, 0]),
        "tf_position": prop("ADBE Position",     position or [cx, cy, 0]),
        "tf_scale":    prop("ADBE Scale",        scale    or list(RENDER["scale_default"])),
        "tf_rotation": prop("ADBE Rotate Z",     0),
    }


def text_base_dict(font=None, fill_color=None, italic=False,
                   apply_stroke=False, stroke_color=None, font_size=None) -> Dict:
    """Возвращает text_base."""
    return {
        "font":             font or RENDER["font_base"],
        "fontSize":         font_size or RENDER["size_base"],
        "applyFill":        not apply_stroke,
        "fillColor":        fill_color or RENDER["color_white"],
        "applyStroke":      apply_stroke,
        "strokeWidth":      RENDER["stroke_width"] if apply_stroke else 0,
        "strokeColor":      stroke_color,
        "tracking":         RENDER["tracking"],
        "leading":          RENDER["leading"],
        "autoLeading":      False,
        "justificationCode":"7415",
        "allCaps":          True,
        "leftIndent":       0,
        "rightIndent":      0,
        "firstLineIndent":  RENDER["first_line_indent"],
        "spaceBefore":      0,
        "spaceAfter":       0,
    }


def s_drop_shadow() -> Dict:
    """Sapphire S_DropShadow — мягкая тень субтитра.

    Индексы свойств сняты дампом реального текстового слоя; значения — по
    согласованному скрину. Применяется только к текстовым слоям.
    """
    return {
        "0050": prop("S_DropShadow-0050", [0, 0, 0, 1]),  # Shadow Color (чёрный)
        "0051": prop("S_DropShadow-0051", 2.0),           # Shadow Opacity
        "0052": prop("S_DropShadow-0052", 60),            # Shadow Blur
        "0053": prop("S_DropShadow-0053", 0),             # Shift X
        "0054": prop("S_DropShadow-0054", 0),             # Shift Y
        "0055": prop("S_DropShadow-0055", 1.0),           # Fg Opacity
        "0056": prop("S_DropShadow-0056", 1),             # Comp Premult
        "0057": prop("S_DropShadow-0057", 2),             # Matte Use = Alpha
        "0058": prop("S_DropShadow-0058", 0),             # Invert Matte
        "0059": prop("S_DropShadow-0059", 1),             # Expand Borders
        "0200": prop("S_DropShadow-0200", 1),             # Show Shift
    }


def _with_subtitle_shadow(effects: Dict) -> Dict:
    """Подмешивает S_DropShadow в effects текстового слоя (идемпотентно)."""
    eff = dict(effects or {})
    eff.setdefault("S_DropShadow", s_drop_shadow())
    return eff


def turbulent_displace() -> Dict:
    return {
        "0001": prop("ADBE Turbulent Displace-0001", 1),
        "0002": prop("ADBE Turbulent Displace-0002", 7.5),
        "0003": prop("ADBE Turbulent Displace-0003", 50.0),
        "0004": prop("ADBE Turbulent Displace-0004", [540, 960]),
        "0005": prop("ADBE Turbulent Displace-0005", 1.0),
        "0006": prop("ADBE Turbulent Displace-0006", expression="time*500"),
        "0012": prop("ADBE Turbulent Displace-0012", 3),
    }


def turbulent_displace_whirl(t_out: float) -> Dict:
    """
    Вихревой выход: TurbulentDisplace amount взлетает 7.5 → 647 за ~0.33s.
    Используется на последнем слое TYPE_3 с exit='whirl'.
    Паттерн из оригинального проекта.
    """
    whirl_start = t_out - 0.58   # ~0.58s до конца — начало раскрутки
    whirl_peak  = t_out - 0.25   # ~0.25s до конца — пик вихря
    return {
        "0001": prop("ADBE Turbulent Displace-0001", 1),
        "0002": prop("ADBE Turbulent Displace-0002", keyframes=[
            kf_ease(whirl_start, 7.5, speed_out=1916.5815),
            kf_ease(whirl_peak,  647, speed_in=1916.5815),
        ]),
        "0003": prop("ADBE Turbulent Displace-0003", 50.0),
        "0004": prop("ADBE Turbulent Displace-0004", [540, 960]),
        "0005": prop("ADBE Turbulent Displace-0005", 1.0),
        "0006": prop("ADBE Turbulent Displace-0006", expression="time*500"),
        "0012": prop("ADBE Turbulent Displace-0012", 3),
    }


def posterize_time() -> Dict:
    return {
        "0001": prop("ADBE Posterize Time-0001", 5),
    }


def box_blur_kf(t_start: float, t_end: float, v_start=0.0, v_end=3.0) -> Dict:
    return {
        "0001": prop("ADBE Box Blur2-0001", keyframes=[
            kf_ease(t_start, v_start, speed_out=0.0),
            kf_ease(t_end,   v_end,   speed_in=0.0),
        ]),
        "0002": prop("ADBE Box Blur2-0002", 3),
        "0003": prop("ADBE Box Blur2-0003", 1),
        "0004": prop("ADBE Box Blur2-0004", 0),
    }


def minimax_intro(t_in: float) -> Dict:
    """Minimax эффект на входе слоя (15→0 за 1 кадр). TYPE_1."""
    return {
        "0001": prop("ADBE Minimax-0001", 2),
        "0002": prop("ADBE Minimax-0002", keyframes=[
            kf_ease(t_in,          15,  speed_out=-359.64),
            kf_ease(t_in + FRAME,  0,   speed_in=-359.64),
        ]),
        "0003": prop("ADBE Minimax-0003", 2),
    }


def minimax_exit(t_out: float) -> Dict:
    """Minimax эффект на выходе слоя (0→32 за 1 кадр до t_out). TYPE_2."""
    return {
        "0001": prop("ADBE Minimax-0001", 2),
        "0002": prop("ADBE Minimax-0002", keyframes=[
            kf_ease(t_out - FRAME * 2,  0,  speed_out=359.64),
            kf_ease(t_out - FRAME,      32, speed_in=359.64),
        ]),
        "0003": prop("ADBE Minimax-0003", 2),
    }


def geometry2_scale_anim(t_in: float, t_out: float,
                          scale_start=85, scale_end=100,
                          skew_start=0.0, skew_end=0.0) -> Dict:
    """Стандартная анимация Transform effect на adj-слое."""
    cx, cy = RENDER["comp_w"] / 2, RENDER["comp_h"] / 2
    params: Dict = {
        "0001": prop("ADBE Geometry2-0001", [cx, cy]),
        "0002": prop("ADBE Geometry2-0002", [cx, cy]),
        "0011": prop("ADBE Geometry2-0011", 1),
        "0003": prop("ADBE Geometry2-0003", keyframes=[
            kf(t_in,  scale_start, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
            kf(t_out + 0.5, scale_end, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
        ]),
        "0004": prop("ADBE Geometry2-0004", 100),
        "0008": prop("ADBE Geometry2-0008", 100),
    }
    if skew_start != 0.0 or skew_end != 0.0:
        params["0007"] = prop("ADBE Geometry2-0007", keyframes=[
            kf(t_in,  skew_start, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 599.4, "influence": 0.1}]),
            kf(t_out, skew_end, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 0.1}]),
        ])
    return params


def geometry2_type3a(t_in: float, t_out: float) -> Dict:
    """
    TYPE_3A adj: scale 85→97→133 (0%/80%/100%), slight skew 0→-3.5 на последних 20%.
    Создаёт ощущение нарастающего давления к концу.
    """
    cx, cy = RENDER["comp_w"] / 2, RENDER["comp_h"] / 2
    dur = t_out - t_in
    t80 = t_in + dur * 0.80
    return {
        "0001": prop("ADBE Geometry2-0001", [cx, cy]),
        "0002": prop("ADBE Geometry2-0002", [cx, cy]),
        "0011": prop("ADBE Geometry2-0011", 1),
        "0003": prop("ADBE Geometry2-0003", keyframes=[
            kf(t_in,  85,     iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
            kf(t80,   97.2,   iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 50.0}],
               ease_out=[{"speed": 0.0, "influence": 50.0}]),
            kf(t_out, 132.94, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
        ]),
        "0004": prop("ADBE Geometry2-0004", 100),
        "0007": prop("ADBE Geometry2-0007", keyframes=[
            kf(t80,   0.0,  iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
            kf(t_out, -3.5, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
        ]),
        "0008": prop("ADBE Geometry2-0008", 100),
    }


def geometry2_type3b(t_in: float, t_out: float) -> Dict:
    """
    TYPE_3B adj (exit=whirl): дикий skew -43→12→0.77→-7.22 — "закручивание".
    scale 85→95→141 (0%/74%/100%).
    """
    cx, cy = RENDER["comp_w"] / 2, RENDER["comp_h"] / 2
    dur = t_out - t_in
    t06 = t_in + dur * 0.06
    t74 = t_in + dur * 0.74
    return {
        "0001": prop("ADBE Geometry2-0001", [cx, cy]),
        "0002": prop("ADBE Geometry2-0002", [cx, cy]),
        "0011": prop("ADBE Geometry2-0011", 1),
        "0003": prop("ADBE Geometry2-0003", keyframes=[
            kf(t_in,  85,     iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
            kf(t74,   95.23,  iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 50.0}],
               ease_out=[{"speed": 0.0, "influence": 50.0}]),
            kf(t_out, 141.23, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
        ]),
        "0004": prop("ADBE Geometry2-0004", 100),
        "0007": prop("ADBE Geometry2-0007", keyframes=[
            kf(t_in,  -43.0, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 599.4, "influence": 0.1}]),
            kf(t06,    12.0, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 50.0}],
               ease_out=[{"speed": 0.0, "influence": 50.0}]),
            kf(t74,    0.777, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 50.0}],
               ease_out=[{"speed": 0.0, "influence": 50.0}]),
            kf(t_out, -7.223, iit="6613", oit="6613",
               ease_in=[{"speed": 0.0, "influence": 95.0}],
               ease_out=[{"speed": 0.0, "influence": 4.0}]),
        ]),
        "0008": prop("ADBE Geometry2-0008", 100),
    }


def reveal_keyframes(words_with_times: List[Tuple[str, float, float]],
                     n_words_total: int) -> List[Dict]:
    """
    Генерирует PercentStart keyframes для Range Selector «На основе: Слова».

    Word-точная математика: при basedOn=Words процент равномерен ПО СЛОВАМ,
    N слов → граница слова k на pct(k)=k/N*100. Слово i становится видимым,
    когда Start% ≥ pct(i+1). На старте слова держим pct(i), через кадр прыгаем
    на pct(i+1) — открываем РОВНО это слово. Т.к. hold(i+1)==jump(i)==pct(i+1),
    между словами значение постоянно — ничего не «уезжает» и не сшивается.

    (Старая версия брала 25+75/N·i — уровни попадали в СЕРЕДИНУ слова, и
    квадратный селектор цеплял половину следующего. Это и был рассинхрон.)
    """
    n = len(words_with_times)
    if n == 0:
        return []

    def pct(k: int) -> float:
        return k / n * 100.0   # граница k-го слова (basedOn=Words)

    kfs = []
    jump_duration = FRAME * 1  # 1 кадр на прыжок
    prev_jump = None

    for i, (word, t_start, t_end) in enumerate(words_with_times):
        hold_t = t_start
        # слова ближе кадра — не накладываем кейфреймы друг на друга
        if prev_jump is not None and hold_t <= prev_jump:
            hold_t = prev_jump + FRAME * 0.5
        jump_t = hold_t + jump_duration

        # Hold на текущей границе → прыжок на следующую (раскрытие ровно слова i)
        kfs.append(kf_ease(hold_t, pct(i),     speed_out=0.0 if i == 0 else 599.4))
        kfs.append(kf_ease(jump_t, pct(i + 1), speed_in=599.4, speed_out=0.0))
        prev_jump = jump_t

    return kfs


MIN_REVEAL_DUR = 0.30   # «очень короткое» последнее слово строки (с)
SHORT_WORD_LEAD = 0.12  # на сколько раньше его открыть (НЕ слияние!)
REVEAL_SHIFT   = 0.15   # оставлен для совместимости

def compensate_short_words(word_times: List[Tuple[str, float, float]],
                            lines: List[List[str]],
                            threshold: float = MIN_REVEAL_DUR,
                            lead: float = SHORT_WORD_LEAD,
                            ) -> List[Tuple[str, float, float]]:
    """
    Последнее слово каждой строки: если оно очень короткое (< threshold),
    открываем его ЧУТЬ РАНЬШЕ — сдвигаем start назад на lead, чтобы оно
    успело раскрыться до конца строки/сцены.

    Важно: НЕ сливаем с предыдущим словом (как было раньше) — у короткого
    слова остаётся СВОЙ процент-слот, раскрытие остаётся пословным, просто с
    форой по времени. Кламп: не раньше старта предыдущего слова + кадр, чтобы
    не нарушить порядок слов.
    """
    if not lines:
        return word_times

    # Индексы последних слов каждой строки в плоском списке
    last_indices: set = set()
    flat_idx = 0
    for line in lines:
        flat_idx += len(line)
        last_indices.add(flat_idx - 1)

    result = list(word_times)
    for i, (word, t_start, t_end) in enumerate(result):
        if i not in last_indices:
            continue
        if t_end - t_start >= threshold:
            continue
        if i == 0:
            continue  # первое слово — нет предыдущего
        new_start = t_start - lead
        prev_start = result[i - 1][1]
        if new_start < prev_start + FRAME:
            new_start = prev_start + FRAME   # не обгоняем предыдущее слово
        result[i] = (word, new_start, t_end)

    return result


def opacity_fadeout(t_fade_start: float, t_out: float) -> Dict:
    """Layer opacity 100→0 на выходе."""
    return prop("ADBE Opacity", keyframes=[
        kf_ease(t_fade_start, 100, speed_out=-99.9),
        kf_ease(t_out,          0, speed_in=-99.9),
    ])


def text_animator_cfg(n_words: int) -> Dict:
    return {
        "name":    "Animator 1",
        "opacity": 0,
        "selector": {
            "name": "Range Selector 1",
            "advanced": {
                "units": 1, "basedOn": 3, "mode": 1,
                "maxAmount": 100, "shape": 1,
                "smoothness": 0, "hiEase": 0, "loEase": 0,
                "randomizeOrder": 0,
            },
            "percentEnd": 100,
        }
    }


# ---------------------------------------------------------------------------
# Построение char_styles (для выделения focus-слова)
# ---------------------------------------------------------------------------

def build_char_styles_type1(lines: List[List[str]]) -> List[Dict]:
    """
    TYPE_1: первая строка — size_base (80), вторая строка — size_line2 (120).
    ~30% прирост, leading вычисляется пропорционально (TYPE1_LEADING).
    """
    styles = []
    char_i = 0
    for line_idx, line in enumerate(lines):
        big = (line_idx > 0)
        for w in line:
            for _ch in w:
                entry: Dict = {"i": char_i, "font": RENDER["font_base"]}
                if big:
                    entry["fontSize"] = RENDER["size_line2"]
                styles.append(entry)
                char_i += 1
            char_i += 1  # пробел
        # \r не занимает char_i
    return styles


def build_char_styles(text: str, focus_word: Optional[str],
                      focus_style: Optional[str]) -> List[Dict]:
    """
    Возвращает char_styles_ungrouped.
    focus_style="italic" → fauxItalic=True на базовом шрифте, размер НЕ меняет.
    focus_style="red"    → не используется здесь (TYPE_4 — отдельный слой).
    focus_style="size"   → focus word получает font_focus + size_focus (для TYPE_3 последнего слоя).
    """
    styles = []
    words = text.replace('\r', ' ').split(' ')
    char_i = 0

    for w in words:
        is_focus = focus_word and w.upper() == focus_word.upper()
        for _ch in w:
            entry: Dict = {"i": char_i}
            if is_focus and focus_style == "italic":
                entry["font"] = RENDER["font_base"]
                entry["fauxItalic"] = True
            elif is_focus and focus_style == "size":
                entry["font"] = RENDER["font_focus"]
                entry["fontSize"] = RENDER["size_focus"]
            else:
                entry["font"] = RENDER["font_base"]
            styles.append(entry)
            char_i += 1
        char_i += 1  # пробел

    return styles


def build_char_styles_uniform(text: str) -> List[Dict]:
    """Все символы — font_base, без переопределения fontSize (наследует text_base)."""
    styles = []
    char_i = 0
    for ch in text:
        if ch not in (' ', '\r'):
            styles.append({"i": char_i, "font": RENDER["font_base"]})
        char_i += 1
    return styles


# ---------------------------------------------------------------------------
# Сборщики слоёв по типам
# ---------------------------------------------------------------------------

class LayerFactory:
    def __init__(self):
        self._z = 1000  # убывающий z_index (высокий = верхний слой в AE)

    def _z_next(self) -> int:
        z = self._z
        self._z -= 1
        return z

    # --- Базовые строительные блоки ---

    def adj_layer(self, name: str, t_in: float, t_out: float,
                  start_time: float = 0.0,
                  scale_start=85, scale_end=100,
                  skew_start=0.0, skew_end=0.0,
                  comp_name: Optional[str] = None) -> Dict:
        return {
            "name":             name,
            "type":             "adjustment",
            "in_point":         t_in,
            "out_point":        t_out,
            "z_index":          self._z_next(),
            "text":             "",
            "adjustment_layer": True,
            "source_rect":      {},
            "props": {
                **base_transforms(),
                "tf_opacity": prop("ADBE Opacity", 100),
            },
            "effects": {
                "ADBE Geometry2": geometry2_scale_anim(
                    t_in, t_out, scale_start, scale_end,
                    skew_start, skew_end
                ),
            },
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": start_time,
                    "comp_name_target": comp_name or RENDER["comp_text"],
                    "enabled": True,
                },
                "layer_styles_enabled": False,
            },
        }

    def text_layer(self, name: str, text: str,
                   t_in: float, t_out: float,
                   start_time: float = 0.0,
                   props_extra: Optional[Dict] = None,
                   effects_extra: Optional[Dict] = None,
                   text_base: Optional[Dict] = None,
                   char_styles: Optional[List] = None,
                   animator_cfg: Optional[Dict] = None,
                   reveal_kfs: Optional[List] = None,
                   reveal_end_kfs: Optional[List] = None,
                   opacity_kfs: Optional[List] = None,
                   no_animator: bool = False,
                   no_layout_pass: bool = False,
                   collapse_tr: bool = True,
                   scale: Optional[List] = None,
                   comp_name: Optional[str] = None) -> Dict:

        p = {**base_transforms(scale=scale)}
        if opacity_kfs:
            p["layer_opacity"] = prop("ADBE Opacity", keyframes=opacity_kfs)
        else:
            p["tf_opacity"] = prop("ADBE Opacity", 100)
        if reveal_kfs:
            p["reveal"] = prop("ADBE Text Percent Start", keyframes=reveal_kfs)
        if reveal_end_kfs:
            p["reveal_end"] = prop("ADBE Text Percent End", keyframes=reveal_end_kfs)
        if not no_animator:
            p["anim_opacity"] = prop("ADBE Opacity", 0)
        if props_extra:
            p.update(props_extra)

        td: Dict = {
            "layer_meta": {
                "blendingModeCode": "5212",
                "startTime": start_time,
                "comp_name_target": comp_name or RENDER["comp_text"],
                "enabled": True,
                "collapseTransformation": collapse_tr,
            },
            "layer_styles_enabled": False,
            "text_base":  text_base or text_base_dict(),
        }
        if char_styles:
            td["char_styles_ungrouped"] = char_styles
        if no_animator:
            td["no_text_animator"] = True
        elif animator_cfg:
            td["text_animator"] = animator_cfg
        if no_layout_pass:
            td["no_layout_pass"] = True

        return {
            "name":             name,
            "type":             "text",
            "in_point":         t_in,
            "out_point":        t_out,
            "z_index":          self._z_next(),
            "text":             text,
            "adjustment_layer": False,
            "source_rect":      {},
            "props":            p,
            "effects":          _with_subtitle_shadow(effects_extra or {
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
            }),
            "style_instructions": [],
            "text_data":        td,
        }

    # --- Типы ---

    def build_type1(self, scene: Dict, word_timings=None) -> List[Dict]:
        """TYPE_1: 1 text layer (2 строки) + adj layer."""
        t_in   = scene["start"]
        t_out  = scene["end"]
        dur    = t_out - t_in
        text   = words_to_text(scene["lines"])
        n      = len(scene["words"])

        word_times = word_times_from_scene(scene, word_timings)
        word_times = compensate_short_words(word_times, scene["lines"])
        rev_kfs    = reveal_keyframes(word_times, n)

        fade_dur = max(dur * 0.3, FRAME * 5)
        op_kfs   = [
            kf_ease(t_out - fade_dur, 100, speed_out=-99.9),
            kf_ease(t_out,              0, speed_in=-99.9),
        ]

        adj  = self.adj_layer(f"adj_{scene['id']}", t_in, t_out)
        tb   = text_base_dict()
        tb["leading"] = TYPE1_LEADING
        text_l = self.text_layer(
            name=text.replace("\r", " "),
            text=text,
            t_in=t_in, t_out=t_out,
            reveal_kfs=rev_kfs,
            opacity_kfs=op_kfs,
            animator_cfg=text_animator_cfg(n),
            char_styles=build_char_styles_type1(scene["lines"]),
            text_base=tb,
            effects_extra={
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
                "ADBE Minimax":           minimax_intro(t_in),
            }
        )
        return [adj, text_l]

    def build_type2(self, scene: Dict, word_timings=None) -> List[Dict]:
        """TYPE_2: focus-слово italic (ExtraBold шрифт, тот же размер), + adj."""
        t_in  = scene["start"]
        t_out = scene["end"]
        dur   = t_out - t_in
        text  = words_to_text(scene["lines"])
        n     = len(scene["words"])
        focus = scene.get("focus_word")

        word_times = word_times_from_scene(scene, word_timings)
        word_times = compensate_short_words(word_times, scene["lines"])
        rev_kfs    = reveal_keyframes(word_times, n)

        fade_dur = max(dur * 0.3, FRAME * 5)
        op_kfs   = [
            kf_ease(t_out - fade_dur, 100, speed_out=-99.9),
            kf_ease(t_out,              0, speed_in=-99.9),
        ]

        char_styles = build_char_styles(text, focus, "italic")
        adj = self.adj_layer(f"adj_{scene['id']}", t_in, t_out)
        text_l = self.text_layer(
            name=text.replace("\r", " "),
            text=text,
            t_in=t_in, t_out=t_out,
            reveal_kfs=rev_kfs,
            opacity_kfs=op_kfs,
            animator_cfg=text_animator_cfg(n),
            char_styles=char_styles,
            effects_extra={
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
                "ADBE Minimax":           minimax_exit(t_out),
            }
        )
        return [adj, text_l]

    def build_type3(self, scene: Dict, word_timings=None) -> List[Dict]:
        """
        TYPE_3: нарастающие слои.
        "word1" → "word1 word2" → ... каждый появляется по таймингу своего последнего слова.
        """
        words  = scene["words"]
        t_in   = scene["start"]
        t_out  = scene["end"]
        n      = len(words)

        all_times = word_times_from_scene(scene, word_timings)
        layers: List[Dict] = []
        use_whirl = scene.get("exit") == "whirl"

        # Adj с правильной анимацией в зависимости от варианта
        if use_whirl:
            adj_effects = {"ADBE Geometry2": geometry2_type3b(t_in, t_out)}
        else:
            adj_effects = {"ADBE Geometry2": geometry2_type3a(t_in, t_out)}

        layers.append({
            "name": f"adj_{scene['id']}",
            "type": "adjustment",
            "in_point": t_in,
            "out_point": t_out,
            "z_index": self._z_next(),
            "text": "",
            "adjustment_layer": True,
            "source_rect": {},
            "props": {**base_transforms(), "tf_opacity": prop("ADBE Opacity", 100)},
            "effects": adj_effects,
            "style_instructions": [],
            "text_data": {"layer_meta": {
                "blendingModeCode": "5212",
                "startTime": 0.0,
                "comp_name_target": RENDER["comp_text"],
                "enabled": True,
                "collapseTransformation": True,
            }},
        })

        for i in range(n):
            sub_words = words[:i + 1]
            is_last   = (i == n - 1)
            sub_in    = all_times[i][1]   # start последнего слова в группе

            if not is_last:
                # Промежуточный слой: hard-cut, живёт ровно до старта следующего слова
                sub_out   = all_times[i + 1][1]
                op_extra  = {"tf_opacity": prop("ADBE Opacity", 100)}
                turb      = turbulent_displace()
                blur_fx   = {}
                use_opacity_kfs = False
            else:
                # Последний слой: fade-out + эффект выхода
                sub_out   = t_out
                fade_dur  = max((t_out - sub_in) * 0.3, FRAME * 5)
                if use_whirl:
                    # Вихревой выход: turb spike 7.5→647, быстрый fade
                    fade_start = t_out - 0.46
                    fade_end   = t_out - 0.13
                    turb       = turbulent_displace_whirl(t_out)
                    blur_fx    = box_blur_kf(fade_start, t_out - 0.17)
                else:
                    # Обычный выход: box blur + fade
                    fade_start = t_out - fade_dur
                    fade_end   = t_out
                    turb       = turbulent_displace()
                    blur_fx    = box_blur_kf(fade_start, fade_end)
                op_extra = {}
                use_opacity_kfs = True
                _op_kfs = [
                    kf_ease(fade_start, 100, speed_out=-299.7 if use_whirl else -599.4),
                    kf_ease(fade_end,     0, speed_in=-299.7  if use_whirl else -599.4),
                ]

            effects = {"ADBE Turbulent Displace": turb, "ADBE Posterize Time": posterize_time()}
            if blur_fx:
                effects["ADBE Box Blur2"] = blur_fx

            text = _words_with_linebreaks(sub_words, scene.get("lines", []))

            # TYPE_3 всегда uniform — все слова одного размера, focus_word не увеличивается
            cs = build_char_styles_uniform(text)

            text_l = self.text_layer(
                name=text.replace("\r", " "),
                text=text,
                t_in=sub_in, t_out=sub_out,
                opacity_kfs=_op_kfs if use_opacity_kfs else None,
                props_extra=op_extra if not use_opacity_kfs else None,
                no_animator=True,
                scale=[75, 75, 100],   # как в оригинале
                char_styles=cs,
                effects_extra=effects,
            )
            layers.append(text_l)

        return layers

    def build_type4(self, scene: Dict) -> List[Dict]:
        """
        TYPE_4: красное слово/фраза (хук).
        Поддерживает 1 или 2 слова — всегда на ОДНОЙ строке (без \r).
        """
        word   = " ".join(w.upper() for w in scene["words"])
        t_in   = scene["start"]
        t_out  = scene["end"]
        dur    = t_out - t_in

        fade_dur   = min(0.5, dur * 0.2)
        fade_start = t_out - fade_dur

        mine_in  = t_in - 0.3
        mine_out = t_out + 0.1
        mine_text = {
            "name":             "mine",
            "type":             "text",
            "in_point":         mine_in,
            "out_point":        mine_out,
            "z_index":          self._z_next(),
            "text":             word,
            "adjustment_layer": False,
            "source_rect":      {},
            "props": {
                "tf_anchor":   prop("ADBE Anchor Point",  [0, -33.5, 0]),
                "tf_position": prop("ADBE Position",      [540, 960, 0]),
                "tf_scale":    prop("ADBE Scale",         [100, 100, 100]),
                "tf_rotation": prop("ADBE Rotate Z",      0),
                "layer_opacity": prop("ADBE Opacity", keyframes=[
                    kf_ease(t_out - fade_dur,  100, speed_out=-99.9),
                    kf_ease(mine_out - FRAME,    0, speed_in=-99.9),
                ]),
            },
            "effects": {
                "ADBE Drop Shadow": {
                    "0001": prop("ADBE Drop Shadow-0001", [1, 1, 1, 1]),
                    "0002": prop("ADBE Drop Shadow-0002", 50),
                    "0003": prop("ADBE Drop Shadow-0003", 0),
                    "0004": prop("ADBE Drop Shadow-0004", 3.0),
                    "0005": prop("ADBE Drop Shadow-0005", 0.0),
                    "0006": prop("ADBE Drop Shadow-0006", 0),
                },
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
                "ADBE Box Blur2": box_blur_kf(t_out - fade_dur, mine_out - FRAME),
            },
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0.0,
                    "comp_name_target": RENDER["comp_mine"],
                    "enabled": True,
                },
                "layer_styles_enabled": False,
                "text_base": text_base_dict(
                    font=RENDER["font_focus"],
                    fill_color=RENDER["color_red"],
                ),
                "char_styles_ungrouped": [
                    {"i": j, "font": RENDER["font_focus"],
                     "fontSize": RENDER["size_base"]}
                    for j in range(len(word))  # word уже содержит пробелы для фразы
                ],
                "no_text_animator": True,
                "no_layout_pass":   True,
            },
        }

        # --- Main precomp (в "Текст") ---
        main_z = self._z_next()
        precomp_main = {
            "name":             'Текст "Mine"',
            "type":             "precomp",
            "in_point":         t_in,
            "out_point":        t_out,
            "z_index":          main_z,
            "text":             "",
            "adjustment_layer": False,
            "source_rect":      {},
            "props": {
                "tf_anchor":   prop("ADBE Anchor Point",  [540, 960, 0]),
                "tf_position": prop("ADBE Position",      [540, 960, 0]),
                "tf_scale":    prop("ADBE Scale",         [100, 100, 100]),
                "tf_rotation": prop("ADBE Rotate Z",      0),
                "tf_opacity":  prop("ADBE Opacity",       100),
            },
            "effects": {},
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0,   # 0 → Mine comp time == abs comp time
                    "motionBlur": True,
                    "enabled": True,
                    "comp_name_target": RENDER["comp_text"],
                },
                "layer_styles_enabled": False,
                "precomp_source": {"comp_name": RENDER["comp_mine"]},
            },
        }

        # --- Glow precomp (в "Текст") ---
        glow_z = self._z_next()
        precomp_glow = {
            "name":             'Текст "Mine" glow',
            "type":             "precomp",
            "in_point":         t_in,
            "out_point":        t_out,
            "z_index":          glow_z,
            "text":             "",
            "adjustment_layer": False,
            "source_rect":      {},
            "props": {
                "tf_anchor":   prop("ADBE Anchor Point",  [540, 960, 0]),
                "tf_position": prop("ADBE Position",      [540, 960, 0]),
                "tf_scale":    prop("ADBE Scale", keyframes=[
                    kf(t_in,          [150.0, 150.0, 100.0], iit="6613", oit="6613",
                       ease_in=[{"speed": 0.0, "influence": 95.0}] * 3,
                       ease_out=[{"speed": 1045.1, "influence": 4.0}] * 2 + [{"speed": 0.0, "influence": 4.0}]),
                    kf(t_in + 0.5,    [250.0, 250.0, 100.0], iit="6613", oit="6613",
                       ease_in=[{"speed": 5.82, "influence": 95.0}] * 2 + [{"speed": 0.0, "influence": 95.0}],
                       ease_out=[{"speed": 0.0, "influence": 4.0}] * 3),
                ]),
                "tf_rotation": prop("ADBE Rotate Z", 0),
                "tf_opacity":  prop("ADBE Opacity",  40),
            },
            "effects": {
                "ADBE Box Blur2": {
                    "0001": prop("ADBE Box Blur2-0001", 5),
                    "0002": prop("ADBE Box Blur2-0002", 3),
                },
            },
            "style_instructions": [],
            "text_data": {
                "layer_meta": {
                    "blendingModeCode": "5212",
                    "startTime": 0,   # 0 → Mine comp time == abs comp time
                    "motionBlur": True,
                    "enabled": True,
                    "comp_name_target": RENDER["comp_text"],
                },
                "layer_styles_enabled": False,
                "precomp_source": {"comp_name": RENDER["comp_mine"]},
            },
        }

        return [mine_text, precomp_main, precomp_glow]

    def build_type5(self, scene: Dict, word_timings=None) -> List[Dict]:
        """
        TYPE_5: outline (PercentEnd reveal) + fill (PercentStart reveal).
        Оригинал:
          outline — reveal_end, tf_opacity=100, ends at ~75% scene (no fade)
          fill    — reveal, layer_opacity fade + box blur exit
        """
        t_in   = scene["start"]
        t_out  = scene["end"]
        dur    = t_out - t_in
        text   = words_to_text(scene["lines"])
        n      = len(scene["words"])

        word_times  = word_times_from_scene(scene, word_timings)
        word_times  = compensate_short_words(word_times, scene["lines"])
        rev_kfs     = reveal_keyframes(word_times, n)

        # Outline заканчивается раньше — примерно в точке последнего кейфрейма reveal + чуть
        outline_out = word_times[-1][2] + FRAME * 4   # конец последнего слова + 4 кадра

        fade_dur = max(dur * 0.25, FRAME * 5)
        fill_op_kfs = [
            kf_ease(t_out - fade_dur, 100, speed_out=-99.9),
            kf_ease(t_out,              0, speed_in=-99.9),
        ]

        adj = self.adj_layer(f"adj_{scene['id']}", t_in, t_out)

        # Outline: PercentEnd animator, статичная opacity, исчезает сразу после reveal
        outline_td = text_base_dict(apply_stroke=True, stroke_color=RENDER["color_white"])
        outline = self.text_layer(
            name=text.replace("\r", " ") + " outline",
            text=text,
            t_in=t_in, t_out=outline_out,
            reveal_end_kfs=rev_kfs,       # PercentEnd
            props_extra={"tf_opacity": prop("ADBE Opacity", 100)},
            animator_cfg=text_animator_cfg(n),
            text_base=outline_td,
            char_styles=build_char_styles(text, None, None),
            effects_extra={
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
            }
        )

        # Fill: PercentStart animator, layer_opacity fade + box blur
        fill = self.text_layer(
            name=text.replace("\r", " "),
            text=text,
            t_in=t_in, t_out=t_out,
            reveal_kfs=rev_kfs,
            opacity_kfs=fill_op_kfs,
            animator_cfg=text_animator_cfg(n),
            char_styles=build_char_styles(text, None, None),
            effects_extra={
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
                "ADBE Box Blur2":         box_blur_kf(t_out - fade_dur, t_out),
            }
        )

        return [adj, outline, fill]

    def build_type6(self, scene: Dict, word_timings=None) -> List[Dict]:
        """
        TYPE_6: 2 группы, поочерёдное появление.
        Один слой с \r-разделителем. Группа A открывается первой,
        группа B — по таймингу своего первого слова через PercentStart.
        """
        lines  = scene["lines"]
        t_in   = scene["start"]
        t_out  = scene["end"]
        dur    = t_out - t_in

        group_a = lines[0]
        group_b = lines[1] if len(lines) > 1 else []
        n_a, n_b = len(group_a), len(group_b)
        n_total  = n_a + n_b

        all_times = word_times_from_scene(scene, word_timings)
        all_times = compensate_short_words(all_times, scene["lines"])

        # Reveal по всем словам как одному тексту
        rev_kfs = reveal_keyframes(all_times, n_total)

        fade_dur = max(dur * 0.25, FRAME * 5)
        op_kfs   = [
            kf_ease(t_out - fade_dur, 100, speed_out=-99.9),
            kf_ease(t_out,              0, speed_in=-99.9),
        ]

        text = words_to_text(lines)
        adj  = self.adj_layer(f"adj_{scene['id']}", t_in, t_out)
        layer = self.text_layer(
            name=text.replace("\r", " "),
            text=text,
            t_in=t_in, t_out=t_out,
            reveal_kfs=rev_kfs,
            opacity_kfs=op_kfs,
            animator_cfg=text_animator_cfg(n_total),
            char_styles=build_char_styles(text, None, None),
            effects_extra={
                "ADBE Turbulent Displace": turbulent_displace(),
                "ADBE Posterize Time":    posterize_time(),
            }
        )
        return [adj, layer]


# ---------------------------------------------------------------------------
# Вспомогательная: раскладываем слова по времени
# ---------------------------------------------------------------------------

def _equidistant_word_times(words: List[str], t_in: float, t_out: float
                             ) -> List[Tuple[str, float, float]]:
    """Равномерное распределение если точных таймингов нет."""
    n = len(words)
    if n == 0:
        return []
    seg = (t_out - t_in) / n
    return [(w, t_in + i * seg, t_in + (i + 1) * seg) for i, w in enumerate(words)]


def word_times_from_scene(scene: Dict,
                           word_timings: Optional[Dict] = None
                           ) -> List[Tuple[str, float, float]]:
    """
    Берёт тайминги слов в порядке приоритета:
    1. scene["word_timings"]  — если модель вернула их в scenes.json (лучший вариант)
    2. word_timings.json      — внешний файл (--timings)
    3. Равномерное распределение по длительности сцены (fallback)
    """
    words = scene["words"]

    # 1. Тайминги внутри сцены (от LLM)
    scene_wt = scene.get("word_timings")
    if scene_wt:
        result = []
        for i, w in enumerate(words):
            if i < len(scene_wt):
                result.append((w, float(scene_wt[i]["start"]), float(scene_wt[i]["end"])))
            else:
                result.append((w, scene["start"], scene["end"]))
        return result

    # 2. Внешний word_timings.json
    if word_timings:
        # Строим словарь word→(start,end) — берём первое вхождение в диапазоне сцены
        wlist = word_timings.get("words", [])
        t_in, t_out = scene["start"], scene["end"]
        # Только слова внутри временного окна сцены (±0.5s допуск)
        in_range = [w for w in wlist
                    if w["start"] >= t_in - 0.5 and w["end"] <= t_out + 0.5]
        wmap = {}
        for entry in in_range:
            key = entry["word"].lower()
            if key not in wmap:
                wmap[key] = (entry["start"], entry["end"])

        result = []
        used_indices: set = set()
        for w in words:
            times = wmap.get(w.lower())
            if times:
                result.append((w, times[0], times[1]))
            else:
                result.append((w, scene["start"], scene["end"]))
        return result

    # 3. Fallback: равномерно
    return _equidistant_word_times(words, scene["start"], scene["end"])


def words_to_text(lines: List[List[str]]) -> str:
    """Объединяет lines в строку с \r-разделителем, все слова UPPER."""
    return "\r".join(" ".join(w.upper() for w in line) for line in lines)


def _words_with_linebreaks(sub_words: List[str], scene_lines: List[List[str]]) -> str:
    """
    Строит накопленный текст для TYPE_3 с учётом переносов строк из scene["lines"].
    Пример: sub_words=["что","грели","когда-то","сотри"],
            scene_lines=[["что","грели","когда-то"],["сотри"]]
    → "ЧТО ГРЕЛИ КОГДА-ТО\rСОТРИ"
    """
    if not scene_lines:
        return " ".join(w.upper() for w in sub_words)
    # Строим карту слово→номер строки (по первому совпадению)
    word_line: Dict[str, int] = {}
    for li, line in enumerate(scene_lines):
        for w in line:
            key = w.lower()
            if key not in word_line:
                word_line[key] = li
    parts: List[str] = []
    prev_li: Optional[int] = None
    for w in sub_words:
        li = word_line.get(w.lower(), 0)
        if prev_li is None:
            parts.append(w.upper())
        elif li != prev_li:
            parts.append("\r" + w.upper())
        else:
            parts.append(" " + w.upper())
        prev_li = li
    return "".join(parts)


# ---------------------------------------------------------------------------
# Главная функция конвертации
# ---------------------------------------------------------------------------

def build_all_layers(scenes: List[Dict],
                     word_timings: Optional[Dict] = None) -> List[Dict]:
    factory = LayerFactory()
    all_layers: List[Dict] = []

    sorted_scenes = sorted(scenes, key=lambda s: s["start"])

    for idx, scene in enumerate(sorted_scenes):
        scene_type = scene.get("type", "TYPE_1")
        builders = {
            "TYPE_1": factory.build_type1,
            "TYPE_2": factory.build_type2,
            "TYPE_3": factory.build_type3,
            "TYPE_4": factory.build_type4,
            "TYPE_5": factory.build_type5,
            "TYPE_6": factory.build_type6,
        }
        builder = builders.get(scene_type)
        if not builder:
            print(f"WARN: unknown type {scene_type} scene={scene.get('id')} — skipping",
                  file=sys.stderr)
            continue

        if scene_type == "TYPE_4":
            layers = builder(scene)
        else:
            layers = builder(scene, word_timings)

        all_layers.extend(layers)
        print(f"OK  scene {scene.get('id'):>2} {scene_type}  "
              f"[{scene['start']:.3f}–{scene['end']:.3f}]  "
              f"words={scene['words']}  → {len(layers)} layers")

    return all_layers


# ---------------------------------------------------------------------------
# Инжект в render.jsx
# ---------------------------------------------------------------------------

def inject_into_jsx(layers: List[Dict], jsx_path: str, out_path: str,
                    comp_dur: Optional[float] = None,
                    comp_text: Optional[str] = None,
                    comp_mine: Optional[str] = None):
    content = open(jsx_path, encoding="utf-8").read()

    # --- 1. Патч text_layers ---
    m = re.search(r'var text_layers = (\[.*?\]);', content, re.DOTALL)
    if not m:
        raise ValueError("var text_layers not found in JSX file")
    layers_json = json.dumps(layers, ensure_ascii=False, separators=(',', ':'))
    content = content[:m.start()] + f'var text_layers = {layers_json};' + content[m.end():]

    # --- 2. Патч compsSpec: обновляем dur и workAreaDuration по имени компа ---
    if comp_dur is not None:
        cs_m = re.search(r'var compsSpec = (\[.*?\]);', content, re.DOTALL)
        if cs_m:
            try:
                specs = json.loads(cs_m.group(1))
                for spec in specs:
                    name = spec.get("name", "")
                    # Патчим ВСЕ компы на полную длину трека (включая Mine-комп)
                    spec["dur"]              = comp_dur
                    spec["workAreaDuration"] = comp_dur
                new_specs = json.dumps(specs, ensure_ascii=False, separators=(',', ':'))
                content = (content[:cs_m.start()]
                           + f'var compsSpec = {new_specs};'
                           + content[cs_m.end():])
                print(f"Patched compsSpec: main/text comp dur = {comp_dur:.3f}s")
            except Exception as e:
                print(f"WARN: compsSpec patch failed: {e}", file=sys.stderr)

    # --- 3. Переименование компов (если переданы кастомные имена) ---
    if comp_text and comp_text != RENDER["comp_text"]:
        content = content.replace(
            json.dumps(RENDER["comp_text"]),
            json.dumps(comp_text)
        )
        print(f"Renamed comp_text: '{RENDER['comp_text']}' → '{comp_text}'")

    if comp_mine and comp_mine != RENDER["comp_mine"]:
        content = content.replace(
            json.dumps(RENDER["comp_mine"]),
            json.dumps(comp_mine)
        )
        print(f"Renamed comp_mine: '{RENDER['comp_mine']}' → '{comp_mine}'")

    # --- 4. Валидация скобок ---
    opens  = content.count('{')
    closes = content.count('}')
    if opens != closes:
        raise ValueError(f"Brace mismatch after inject: {{ {opens} / }} {closes}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Injected {len(layers)} layers → {out_path}  (braces OK)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="scenes.json → text_layers → render.jsx")
    parser.add_argument("--scenes",    required=True, help="Путь к scenes.json")
    parser.add_argument("--timings",   default=None,  help="Путь к word_timings.json (опционально)")
    parser.add_argument("--jsx",       default=None,  help="Путь к render_template.jsx")
    parser.add_argument("--out",       default=None,  help="Выходной JSX (по умолчанию render_out.jsx)")
    parser.add_argument("--dump",      action="store_true",
                        help="Только вывести text_layers JSON без записи JSX")
    parser.add_argument("--comp-text", default=None,
                        help=f"Имя основного текстового компа (по умолч.: '{RENDER['comp_text']}')")
    parser.add_argument("--comp-mine", default=None,
                        help=f"Имя Mine-компа (по умолч.: '{RENDER['comp_mine']}')")
    parser.add_argument("--tail",      type=float, default=2.0,
                        help="Буфер в секундах после последней сцены (по умолч.: 2.0)")
    parser.add_argument("--offset",    type=float, default=0.0,
                        help="Вычесть это значение из всех start/end (по умолч.: 0). "
                             "Используй если тайминги сцен в абсолютном времени аудио, "
                             "а AE-комп начинается от 0. Например: --offset 56.409")
    args = parser.parse_args()

    # Переопределяем имена компов если переданы
    if args.comp_text:
        RENDER["comp_text"] = args.comp_text
    if args.comp_mine:
        RENDER["comp_mine"] = args.comp_mine

    # Читаем scenes
    with open(args.scenes, encoding="utf-8") as f:
        data = json.load(f)
    scenes = data.get("scenes") or data  # поддержка обоих форматов

    # Вычисляем длительность компа из таймингов
    comp_dur = compute_comp_dur(scenes, tail_buffer=args.tail)
    print(f"Comp duration from scenes: {comp_dur:.3f}s  "
          f"(last scene end={max(s['end'] for s in scenes):.3f}s + {args.tail}s buffer)")

    # Читаем тайминги слов (опционально)
    word_timings = None
    if args.timings:
        with open(args.timings, encoding="utf-8") as f:
            word_timings = json.load(f)

    # Применяем offset (сдвиг таймлайна)
    if args.offset != 0.0:
        print(f"Applying time offset: −{args.offset:.3f}s to all scenes")
        for sc in scenes:
            sc["start"] -= args.offset
            sc["end"]   -= args.offset
            if sc.get("word_timings"):
                for wt in sc["word_timings"]:
                    wt["start"] -= args.offset
                    wt["end"]   -= args.offset

    # Строим слои
    layers = build_all_layers(scenes, word_timings)
    print(f"\nВсего слоёв: {len(layers)}")

    if args.dump:
        print("\n--- text_layers JSON ---")
        print(json.dumps(layers, ensure_ascii=False, indent=2))
        return

    if not args.jsx:
        print("ERROR: укажи --jsx <template.jsx> или используй --dump", file=sys.stderr)
        sys.exit(1)

    out_path = args.out or "render_out.jsx"
    inject_into_jsx(
        layers, args.jsx, out_path,
        comp_dur=comp_dur,
        comp_text=args.comp_text,
        comp_mine=args.comp_mine,
    )


if __name__ == "__main__":
    main()