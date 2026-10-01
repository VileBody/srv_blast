"""Тайтлы Kant как стиль субтитров: спека ↔ JSX-библиотека ↔ режимы ↔ сборка оверлея ↔ шрифты сайта."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.jsx_subtitles_builder import KANT_SPEC, build_jsx_subtitles_overlay, kant_phrases
from app.render_plan import _subtitle_operation
from app.subtitle_text_style import jsx_style_config
from core.subtitles_mode import (
    KANT_TITLE_BY_MODE,
    SUBTITLES_MODE_JSX_5TH,
    SUBTITLES_MODE_KANT_TITLES,
    SUBTITLES_MODE_VALUES,
)

ROOT = Path(__file__).resolve().parents[1]
KANT_DIR = ROOT / "5th_template" / "kant_titles"
LIB = (KANT_DIR / "kant_titles.jsx").read_text(encoding="utf-8-sig")
FONT_MANIFEST = ROOT / "web_app" / "frontend" / "public" / "fonts" / "subtitles" / "manifest.json"
WORDS = [
    {"word": "тёмный", "start": 0.4, "end": 0.8},
    {"word": "район", "start": 0.85, "end": 1.2},
    {"word": "мы", "start": 1.25, "end": 1.4},
    {"word": "не", "start": 1.45, "end": 1.6},
    {"word": "спим", "start": 1.65, "end": 2.0},
    {"word": "газ", "start": 3.5, "end": 3.9},
]


def _jsx_titles() -> dict[str, str]:
    """id тайтла → его строка в TITLES библиотеки."""
    out = {}
    for m in re.finditer(r'^\s*"([a-z-]+)":\s*\{ title:(.*?)(?=^\s*"[a-z-]+":\s*\{ title:|^\s*\};)', LIB, re.M | re.S):
        out[m.group(1)] = m.group(2)
    return out


def _jsx_const(name: str) -> float:
    return float(re.search(rf"var {name} = ([0-9.]+);", LIB).group(1))


def test_spec_matches_the_jsx_library():
    titles = _jsx_titles()
    assert set(titles) == set(KANT_SPEC["titles"])
    for tid, row in KANT_SPEC["titles"].items():
        body = titles[tid]
        assert json.loads(re.search(r"fonts: (\[.*?\])", body).group(1)) == row["fonts"], tid
        assert float(re.search(r"intro: ([0-9.]+)", body).group(1)) == row["intro"], tid
        cyr = dict(re.findall(r'"([^"]+)": "([^"]+)"', re.search(r"cyr: \{(.*?)\}", body, re.S).group(1)))
        assert cyr == row["cyr"], tid
        squeeze = re.search(r"cyrSqueeze: ([0-9.]+)", body)
        assert (float(squeeze.group(1)) if squeeze else None) == row.get("cyrSqueeze"), tid
        assert row["text"]["font"] == row["fonts"][0], tid
    split = KANT_SPEC["split"]
    assert _jsx_const("MAX_WEIGHT") == split["maxWeight"]
    assert _jsx_const("MIN_FIT") == split["minFit"]
    assert _jsx_const("MIN_SCREEN") == split["minScreen"]
    assert _jsx_const("INTRO_SHARE") == split["introShare"]
    assert _jsx_const("MIN_STRETCH") == split["minStretch"]
    assert f"opts.maxWidth : {split['maxWidth']}" in LIB


def test_every_title_has_a_mode_and_a_template():
    assert {row["mode"]: tid for tid, row in KANT_SPEC["titles"].items()} == KANT_TITLE_BY_MODE
    for mode, tid in KANT_TITLE_BY_MODE.items():
        assert (KANT_DIR / "aep" / f"{tid}.aep").is_file(), tid
        assert mode in SUBTITLES_MODE_VALUES and mode in SUBTITLES_MODE_JSX_5TH


def test_every_title_font_is_served_to_the_preview():
    bundled = {f["ps"] for f in json.loads(FONT_MANIFEST.read_text(encoding="utf-8"))["fonts"]}
    for tid, row in KANT_SPEC["titles"].items():
        need = {*row["fonts"], *row["cyr"].values(), *(f["font"] for f in row.get("flashes") or [] if f.get("font"))}
        assert need <= bundled, (tid, need - bundled)


def test_length_cut_keeps_a_particle_with_its_word():
    words = [{"word": w, "start": 0.3 + i * 0.45, "end": 0.7 + i * 0.45}
             for i, w in enumerate("ночь горит неоном я снова не дома".split())]
    assert [p["text"] for p in kant_phrases(words)] == ["ночь горит неоном я снова", "не дома"]


def test_phrases_split_on_pauses_and_hold_a_short_tail():
    ph = kant_phrases(WORDS)
    assert [p["text"] for p in ph] == ["тёмный район мы не спим", "газ"]
    assert ph[0]["end"] == pytest.approx(2.0 + KANT_SPEC["phrase"]["tail"])
    assert len(ph[0]["words"]) == 5


@pytest.mark.parametrize("mode", sorted(SUBTITLES_MODE_KANT_TITLES))
def test_overlay_targets_the_main_comp_and_carries_the_template(mode):
    js = build_jsx_subtitles_overlay(mode=mode, word_timings=WORDS)
    tid = KANT_TITLE_BY_MODE[mode]
    assert f"KantTitles.placeLine(comp, {json.dumps(tid)}" in js
    assert "KANT_TITLES_AEP_DIR" in js and f"{tid}.aep" in js
    # билдер ролика имени не передаёт («Comp 1») — тайтл обязан найти MAIN_COMP шаблона
    assert "MAIN_COMP instanceof CompItem" in js


def test_overlay_rejects_settings_the_title_cannot_apply():
    for kw in ({"fill_hex": "#FF0000"}, {"subs_blend": "difference"}, {"style_config": {"fontSize": 90}}):
        with pytest.raises(ValueError):
            build_jsx_subtitles_overlay(mode="kant_gum", word_timings=WORDS, **kw)
    with pytest.raises(ValueError):
        jsx_style_config("kant_gum", None, "#FF0000")
    assert jsx_style_config("kant_gum", None, None) is None


def test_render_plan_keeps_the_title_operation():
    op = _subtitle_operation("kant_vhs", {"subtitles_jsx": {"mode": "kant_vhs", "word_timings": WORDS}})
    assert op.type == "subtitle.kant_title.v1"
    assert op.params == {"source_mode": "kant_vhs", "word_timings": WORDS}
