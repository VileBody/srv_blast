"""Настройки текста визарда на границе сайт → оркестратор (production_backend + subtitle_text)."""
from __future__ import annotations

import pytest

from tests.test_web_production_backend import _backend, _config, _job, _module


# --- web → orchestrator payload ---------------------------------------------

def _payload(monkeypatch, text, *, style="Impulse", mode="impulse_2nd"):
    module = _module(monkeypatch)
    config = _config(module)
    config = type(config)(**{**config.__dict__, "subtitle_modes": {style: mode}})
    backend = _backend(module, config)
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["subtitle"] = {"style": style, "color": "#ffffff", "text": text}
    return module, backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)


def test_web_sends_resolved_style_and_the_text_accent(monkeypatch):
    _, payload = _payload(monkeypatch, {"font": "Inter-Bold", "size": "small", "accentColor": "#FF5FA8"})
    assert payload["subtitle_text_style"] == {"font": "Inter-Bold", "size": "small", "height": "normal",
                                              "position": "center", "shadow": "soft"}
    # один акцентный цвет в кадре — из настроек текста, не из скрытого final.accentColor
    assert payload["accent_color_hex"] == "#FF5FA8"


def test_web_defaults_leave_production_layout_and_colours(monkeypatch):
    _, payload = _payload(monkeypatch, {})
    assert "subtitle_text_style" not in payload
    assert "accent_color_hex" not in payload


def test_web_rejects_what_the_style_cannot_render(monkeypatch):
    # _payload перезагружает модуль — класс ошибки сверяем по имени
    with pytest.raises(Exception, match="Jakson") as script_base:
        _payload(monkeypatch, {"font": "Katherine-Plus"}, style="Jakson", mode="scenes_3rd")
    with pytest.raises(Exception, match="16:9") as down_vertical:
        _payload(monkeypatch, {"position": "down"})
    for err in (script_base, down_vertical):
        assert type(err.value).__name__ == "ProductionBackendError"


def test_web_brat_keeps_its_fixed_font(monkeypatch):
    _, payload = _payload(monkeypatch, {"font": "Point-SemiBold", "accentFont": "Katherine-Plus", "focusStyle": "italic"},
                          style="Brat", mode="brat_5th")
    style = payload["subtitle_text_style"]
    assert "font" not in style and "accent_font" not in style and style["focus_style"] == "italic"
