"""Живые превью каталога для мок-режима.

В моке у футажей и фото нет настоящих файлов, и карточки выбора фона стояли пустыми —
по ним нельзя было оценить ни ленту, ни превью. Здесь каждая запись каталога получает
анимированную SVG-картинку (SMIL играет прямо в <img>): своя палитра по смыслу подборки
и один мотив — огни города, неон, солнце, прожекторы, боке, фары. Фото — та же картинка,
но почти неподвижная: медленный наезд, как у «живых» обложек.

Только для mock-бэкенда: продакшн отдаёт ссылки из настоящего каталога.
"""

from __future__ import annotations

import zlib

# палитра: верх, низ, акцент, второй акцент
_PALETTES: dict[str, tuple[str, str, str, str]] = {
    "night-city": ("#0b1233", "#1d0f3a", "#ffcf6e", "#6fa8ff"),
    "neon": ("#12021f", "#2a0638", "#ff3fb4", "#39e6ff"),
    "sunset": ("#ff9a5a", "#5b1f4f", "#ffe08a", "#ff5f6d"),
    "backstage": ("#140b09", "#2a1712", "#ffd9a0", "#ff9b54"),
    "street": ("#1b2230", "#0d1118", "#9fb4d8", "#ffb86b"),
    "cine-new-york": ("#1a2340", "#0a0d1c", "#ffd27a", "#8fb7ff"),
    "cine-tokyo": ("#2b0d1a", "#12060c", "#ff6b6b", "#ffc2d1"),
    "film-brat": ("#3a3a36", "#151513", "#d9d4c7", "#8a8578"),
    "film-bumer": ("#0e0e12", "#1d1414", "#ff4040", "#fff1c9"),
    "photo-desert": ("#f2a65a", "#6b3a2a", "#ffe7b0", "#c8553d"),
    "photo-portrait": ("#2d1f3d", "#120c1a", "#f3c9b8", "#9b7bd8"),
    "photo-studio": ("#1c1c24", "#08080c", "#f5f5ff", "#8b6fe6"),
    "photo-street": ("#22303c", "#0f151b", "#e6b980", "#7fa3c0"),
}

_MOTIFS: dict[str, str] = {
    "night-city": "skyline",
    "cine-new-york": "skyline",
    "neon": "neon",
    "sunset": "sun",
    "photo-desert": "sun",
    "backstage": "beams",
    "photo-studio": "beams",
    "cine-tokyo": "lanterns",
    "film-bumer": "streaks",
    "street": "bokeh",
    "photo-street": "bokeh",
    "photo-portrait": "bokeh",
    "film-brat": "grain",
}


def _rng(seed: int):
    state = seed or 1

    def next_value() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return next_value


def _motif(kind: str, w: int, h: int, accent: str, accent2: str, rnd) -> str:
    parts: list[str] = []
    if kind == "skyline":
        x = 0.0
        while x < w:
            bw = w * (0.08 + rnd() * 0.1)
            bh = h * (0.18 + rnd() * 0.32)
            parts.append(f'<rect x="{x:.0f}" y="{h - bh:.0f}" width="{bw:.0f}" height="{bh:.0f}" fill="#05060f" opacity=".9"/>')
            for _ in range(int(bh / (h * 0.05))):
                wx = x + bw * (0.15 + rnd() * 0.6)
                wy = h - bh + bh * rnd() * 0.9
                dur = 1.5 + rnd() * 3
                parts.append(
                    f'<rect x="{wx:.0f}" y="{wy:.0f}" width="{w * .012:.1f}" height="{h * .008:.1f}" fill="{accent}">'
                    f'<animate attributeName="opacity" values="1;.15;1" dur="{dur:.1f}s" begin="{rnd() * 3:.1f}s" repeatCount="indefinite"/></rect>'
                )
            x += bw + w * 0.01
        parts.append(
            f'<rect x="-{w}" y="{h * .35:.0f}" width="{w * .6:.0f}" height="2" fill="{accent2}" opacity=".6">'
            f'<animate attributeName="x" from="-{w}" to="{w}" dur="6s" repeatCount="indefinite"/></rect>'
        )
    elif kind == "neon":
        for i in range(4):
            y = h * (0.25 + i * 0.17)
            color = accent if i % 2 == 0 else accent2
            parts.append(
                f'<path d="M0 {y:.0f} Q {w / 2:.0f} {y - h * .12:.0f} {w} {y:.0f}" stroke="{color}" stroke-width="{w * .012:.1f}" fill="none" opacity=".85">'
                f'<animate attributeName="opacity" values=".9;.25;.9" dur="{1.2 + i * .4:.1f}s" repeatCount="indefinite"/></path>'
            )
    elif kind == "sun":
        parts.append(
            f'<circle cx="{w / 2:.0f}" cy="{h * .55:.0f}" r="{min(w, h) * .22:.0f}" fill="{accent}" opacity=".9">'
            f'<animate attributeName="cy" values="{h * .5:.0f};{h * .62:.0f};{h * .5:.0f}" dur="14s" repeatCount="indefinite"/></circle>'
        )
        parts.append(f'<rect x="0" y="{h * .7:.0f}" width="{w}" height="{h * .3:.0f}" fill="{accent2}" opacity=".55"/>')
    elif kind == "beams":
        for i in range(3):
            cx = w * (0.2 + i * 0.3)
            parts.append(
                f'<polygon points="{cx:.0f},0 {cx - w * .25:.0f},{h} {cx + w * .25:.0f},{h}" fill="{accent}" opacity=".16">'
                f'<animateTransform attributeName="transform" type="rotate" values="-12 {cx:.0f} 0;12 {cx:.0f} 0;-12 {cx:.0f} 0" dur="{6 + i * 2}s" repeatCount="indefinite"/></polygon>'
            )
    elif kind == "lanterns":
        for _ in range(9):
            cx, cy = w * rnd(), h * (0.15 + rnd() * 0.6)
            r = min(w, h) * (0.03 + rnd() * 0.04)
            parts.append(
                f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="{accent}" opacity=".8">'
                f'<animate attributeName="cy" values="{cy:.0f};{cy - h * .03:.0f};{cy:.0f}" dur="{3 + rnd() * 3:.1f}s" repeatCount="indefinite"/></circle>'
            )
    elif kind == "streaks":
        for i in range(6):
            y = h * (0.55 + rnd() * 0.3)
            color = accent if i % 2 else accent2
            dur = 1.4 + rnd() * 1.6
            parts.append(
                f'<rect x="-{w * .5:.0f}" y="{y:.0f}" width="{w * .45:.0f}" height="{h * .006 + 1:.1f}" rx="2" fill="{color}" opacity=".8">'
                f'<animate attributeName="x" from="-{w * .5:.0f}" to="{w * 1.1:.0f}" dur="{dur:.1f}s" begin="{rnd() * 2:.1f}s" repeatCount="indefinite"/></rect>'
            )
    elif kind == "bokeh":
        for _ in range(10):
            cx, cy = w * rnd(), h * rnd()
            r = min(w, h) * (0.05 + rnd() * 0.1)
            color = accent if rnd() > 0.5 else accent2
            parts.append(
                f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="url(#glow-{color[1:]})">'
                f'<animate attributeName="cx" values="{cx:.0f};{cx + w * .06:.0f};{cx:.0f}" dur="{7 + rnd() * 6:.1f}s" repeatCount="indefinite"/></circle>'
            )
    if kind in {"grain", "skyline", "streaks"}:
        parts.append(
            f'<rect width="{w}" height="{h}" fill="#fff" opacity=".04">'
            f'<animate attributeName="opacity" values=".02;.07;.03;.06" dur=".4s" repeatCount="indefinite"/></rect>'
        )
    return "".join(parts)


def animated_svg(item_id: str, *, aspect: str, still: bool = False) -> str:
    """SVG-превью записи каталога. `aspect` — '9:16', '16:9' или '4:3'."""
    w, h = {"9:16": (360, 640), "16:9": (640, 360), "4:3": (640, 480)}.get(aspect, (360, 640))
    seed = zlib.crc32(item_id.encode())
    rnd = _rng(seed)
    top, bottom, accent, accent2 = _PALETTES.get(item_id) or (
        f"hsl({seed % 360},45%,22%)", f"hsl({(seed // 7) % 360},50%,8%)", f"hsl({(seed // 3) % 360},80%,70%)", "#ffffff"
    )
    motif = _motif(_MOTIFS.get(item_id, "bokeh"), w, h, accent, accent2, rnd)
    # фото «полуживые»: вместо движения мотива — медленный наезд всей картинки
    zoom = (
        f'<animateTransform attributeName="transform" type="scale" values="1;1.08;1" dur="16s" repeatCount="indefinite" additive="sum"/>'
        if still else ""
    )
    glows = "".join(
        f'<radialGradient id="glow-{c[1:]}"><stop offset="0" stop-color="{c}" stop-opacity=".7"/><stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>'
        for c in {accent, accent2} if c.startswith("#")
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice">'
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>{glows}</defs>'
        f'<g transform-origin="{w / 2:.0f} {h / 2:.0f}">{zoom}<rect width="{w}" height="{h}" fill="url(#bg)"/>{motif}</g>'
        "</svg>"
    )


# ── превью стилей субтитров: строка отрывка в манере стиля, слова появляются по очереди ──
_SUB_WORDS = ("этот", "город", "не", "уснёт")


def _sub_words(words: list[tuple[str, dict[str, str]]], y: float, gap: float, w: int) -> str:
    """Строка слов по центру: одна <text> c <tspan> на слово — ширину и пробелы считает сам шрифт
    (угадывать ширину системного шрифта нельзя). Слова проявляются по очереди, цикл 3.2 с."""
    del gap
    spans: list[str] = []
    for index, (text, attrs) in enumerate(words):
        style = " ".join(f'{k}="{v}"' for k, v in attrs.items() if not k.startswith("_"))
        spans.append(
            f'<tspan {style} fill-opacity="0">{text}{" " if index < len(words) - 1 else ""}'
            f'<animate attributeName="fill-opacity" values="0;1;1;0" keyTimes="0;.12;.85;1" dur="3.2s" begin="{index * 0.35:.2f}s" repeatCount="indefinite"/></tspan>'
        )
    return f'<text x="{w / 2:.0f}" y="{y:.0f}" text-anchor="middle" style="white-space:pre">{"".join(spans)}</text>'


def subtitle_svg(style_id: str) -> str:
    """4:3-превью стиля субтитров для ленты выбора стиля (мок)."""
    w, h = 640, 480
    bg = '<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1d1535"/><stop offset="1" stop-color="#07030f"/></linearGradient></defs><rect width="640" height="480" fill="url(#bg)"/>'
    caps = {"font-family": "Arial, sans-serif", "font-weight": "800", "font-size": "44", "fill": "#f6f5fd", "_cw": "34"}
    if style_id == "brat":
        bg = '<rect width="640" height="480" fill="#8ace00"/>'
        body = _sub_words([(t, {"font-family": "'Arial Narrow', Arial, sans-serif", "font-size": "58", "fill": "#111", "_cw": "26"}) for t in _SUB_WORDS], 262, 16, w)
    elif style_id == "jakson":
        words = [(t.upper(), caps) for t in _SUB_WORDS[:3]] + [("уснёт", {"font-family": "'Segoe Script', 'Brush Script MT', cursive", "font-size": "58", "fill": "#ff4b4b", "_cw": "28"})]
        body = _sub_words(words, 262, 16, w)
    elif style_id == "impulse":
        body = _sub_words([(t.upper(), caps) for t in _SUB_WORDS[:2]], 222, 18, w) + _sub_words([("НЕ УСНЁТ", {**caps, "font-size": "64", "fill": "#c6b6ff", "_cw": "48"})], 312, 0, w)
    elif style_id == "tape":
        strips = '<rect x="36" y="206" width="568" height="74" rx="4" fill="#f6f5fd" transform="rotate(-2 320 243)"/>'
        body = strips + _sub_words([(t.upper(), {**caps, "fill": "#111", "font-size": "40", "_cw": "31"}) for t in _SUB_WORDS], 258, 14, w)
    else:  # trendy и остальные
        palette = ["#f6f5fd", "#ffd166", "#f6f5fd", "#7ae3ff"]
        body = _sub_words([(t.upper(), {**caps, "fill": palette[i % 4]}) for i, t in enumerate(_SUB_WORDS)], 262, 16, w)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice">{bg}{body}</svg>'


def frame_svg(frame_id: str) -> str:
    """Демо-рамка 9:16 для мока: чёрная маска с прозрачным окном, как PNG рамок рендера
    (на проде превью — сам PNG из бакета ассетов)."""
    w, h = 1080, 1920
    if frame_id == "rounded":
        mask = '<path fill-rule="evenodd" fill="#000" d="M0 0H1080V1920H0Z M140 260H940A90 90 0 0 1 1030 350V1570A90 90 0 0 1 940 1660H140A90 90 0 0 1 50 1570V350A90 90 0 0 1 140 260Z"/>'
    elif frame_id == "soft_bars":
        mask = (
            '<defs><linearGradient id="t" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000"/><stop offset=".72" stop-color="#000"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>'
            '<linearGradient id="b" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#000"/><stop offset=".72" stop-color="#000"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient></defs>'
            '<rect width="1080" height="360" fill="url(#t)"/><rect y="1560" width="1080" height="360" fill="url(#b)"/>'
        )
    else:  # letterbox
        mask = '<rect width="1080" height="300" fill="#000"/><rect y="1620" width="1080" height="300" fill="#000"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">{mask}</svg>'

