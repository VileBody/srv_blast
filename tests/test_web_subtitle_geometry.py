"""Геометрия субтитров для JS-превью: числа — из движка рендера, невозможное — 422, как на отправке."""
from __future__ import annotations

import importlib
import sys

import pytest

from tests.test_web_asr_preview import _env

STYLES = ("jakson", "impulse", "tape", "trendy", "brat")


@pytest.fixture(autouse=True)
def _own_app_package():
    """Пакет `app` здесь — веб-бэкенд; корень репо тоже `app`. Подменяем на время теста и
    возвращаем как было, чтобы соседние тесты сборки не получили чужой пакет."""
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    yield
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def _st(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    return importlib.import_module("app.subtitle_text")


def test_defaults_are_the_production_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _st(monkeypatch)
    g = {style: st.geometry({}, style=style, render_preset="vertical") for style in STYLES}
    for style, geo in g.items():
        assert geo["style"] == style and geo[style], style
        assert geo["comp"] == {"w": 1080, "h": 1920}
        assert (geo["alignX"], geo["centerY"]) == ("center", 0.5)
    # прод-числа шаблонов: jakson 80/120 капсом, impulse 100, tape 60/80 в коробе 900, trendy 130 ×4, brat 130 в 80%
    j = g["jakson"]["jakson"]
    assert (j["sizeBase"], j["sizeLine2"], j["sizeFocus"], j["leadingType1"], j["tracking"]) == (80.0, 120.0, 200.0, 115.0, -50)
    assert g["jakson"]["accentColor"] == "#FD1614"
    assert g["impulse"]["impulse"]["size"] == 100.0 and g["impulse"]["impulse"]["longHold"] == 75.0
    t = g["tape"]["tape"]
    assert (t["size"], t["leading"], t["boxW"], t["focusColor"]) == (60.0, 80.0, 900.0, "#E51515")
    tr = g["trendy"]["trendy"]
    assert (tr["fontSize"], tr["verticalScale"], tr["tracking"]) == (130.0, 4.0, -55)
    b = g["brat"]["brat"]
    assert (b["fontSize"], b["boxWFactor"], b["layerScale"], b["wordsPerLine"], b["maxLines"]) == (130.0, 0.8, 0.8, 2, 4)
    # метрики шрифтов на 100 pt — по ним превью подгоняет чужой шрифт под AE
    assert g["jakson"]["fonts"]["Point-SemiBold"]["capH"] > 0


def test_settings_change_the_numbers_like_the_render(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _st(monkeypatch)
    small = st.geometry({"size": "small", "position": "left", "accentColor": "#00ff00"}, style="jakson", render_preset="vertical")
    assert small["jakson"]["sizeBase"] == 64.0
    assert small["alignX"] == "left" and small["accentColor"] == "#00ff00"
    brat = st.geometry({"size": "medium", "focusStyle": "italic"}, style="brat", render_preset="vertical")["brat"]
    assert brat["fontSize"] == 117.0 and brat["focusFont"] == "ArialNarrow-Italic"
    down = st.geometry({"position": "down"}, style="tape", render_preset="wide")
    assert down["centerY"] == 0.64


def test_impossible_settings_are_the_same_error_as_on_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _st(monkeypatch)
    with pytest.raises(st.SubtitleTextError, match="16:9"):
        st.geometry({"position": "down"}, style="jakson", render_preset="vertical")
    with pytest.raises(st.SubtitleTextError, match="Jakson|jakson"):
        st.geometry({"font": "Katherine-Plus"}, style="jakson", render_preset="vertical")
    with pytest.raises(st.SubtitleTextError):
        st.geometry({}, style="karaoke", render_preset="vertical")


def test_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        ok = tc.post("/api/wizard/subtitle-geometry", json={"style": "trendy", "settings": {}, "renderPreset": "vertical"})
        assert ok.status_code == 200 and ok.json()["trendy"]["fontSize"] == 130.0
        bad = tc.post("/api/wizard/subtitle-geometry", json={"style": "jakson", "settings": {"position": "down"}, "renderPreset": "vertical"})
        assert bad.status_code == 422 and "16:9" in bad.json()["detail"]
        preset = tc.post("/api/wizard/subtitle-geometry", json={"style": "jakson", "settings": {}, "renderPreset": "square"})
        assert preset.status_code == 422
