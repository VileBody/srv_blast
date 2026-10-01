"""Тайтлы Kant на сайте: геометрия для JS-превью из спеки рендера, настройки текста отвергаются."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from tests.test_web_asr_preview import _env

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "5th_template" / "kant_titles" / "kant_titles.json").read_text(encoding="utf-8"))
MODES = sorted(row["mode"] for row in SPEC["titles"].values())


@pytest.fixture(autouse=True)
def _own_app_package():
    """Пакет `app` здесь — веб-бэкенд (как в test_web_subtitle_geometry)."""
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    yield
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def _mod(monkeypatch: pytest.MonkeyPatch, name: str):
    _env(monkeypatch)
    return importlib.import_module(f"app.{name}")


@pytest.mark.parametrize("mode", MODES)
def test_geometry_is_the_render_spec(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    st = _mod(monkeypatch, "subtitle_text")
    geo = st.geometry({"size": "large", "position": "center", "shadow": "soft", "height": "normal"},
                      style=mode, render_preset="vertical")
    tid = next(t for t, row in SPEC["titles"].items() if row["mode"] == mode)
    row = SPEC["titles"][tid]
    assert geo["style"] == mode and geo["kant"]["id"] == tid
    assert geo["kant"]["text"] == row["text"] and geo["kant"]["intro"] == row["intro"]
    assert geo["kant"]["split"] == SPEC["split"] and geo["kant"]["phrase"] == SPEC["phrase"]
    # превью грузит ровно эти шрифты (оригинал для латиницы + кириллическая замена)
    assert set(geo["fonts"]) == {*row["fonts"], *row["cyr"].values()}


def test_text_settings_are_rejected_for_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _mod(monkeypatch, "subtitle_text")
    for settings in ({"font": "Inter-Regular"}, {"size": "small"}, {"position": "left"}, {"accentColor": "#ff0000"}):
        with pytest.raises(st.SubtitleTextError):
            st.geometry(settings, style="kant_gum", render_preset="vertical")
        with pytest.raises(st.SubtitleTextError):
            st.resolve(settings, subtitles_mode="kant_gum", render_preset="vertical")
    assert st.resolve({}, subtitles_mode="kant_gum", render_preset="vertical") is None
    # у прочих режимов без стиля акцентный цвет по-прежнему не ошибка (поведение не менялось)
    assert st.resolve({"accentColor": "#ff0000"}, subtitles_mode="legacy_blocks", render_preset="vertical") is None


def test_catalog_marks_titles_fixed_and_serves_their_fonts(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _mod(monkeypatch, "subtitle_text")
    fonts = _mod(monkeypatch, "subtitle_fonts")
    assert st.font_catalog()["fixedStyles"] == MODES
    assert set(st.kant_fonts()) <= set(fonts.required_fonts())


def test_mock_catalog_names_match_the_mode_map(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _mod(monkeypatch, "mock_store")
    env = (ROOT / "web_app" / "backend" / ".env.production.example").read_text(encoding="utf-8")
    line = next(ln for ln in env.splitlines() if ln.startswith("WEB_SUBTITLE_MODE_MAP_JSON="))
    mode_map = json.loads(line.split("=", 1)[1])
    kant = {s["name"]: s["id"] for s in store.SUBTITLE_STYLES if s["id"].startswith("kant_")}
    assert sorted(kant.values()) == MODES
    assert all(mode_map[name] == mode for name, mode in kant.items())
