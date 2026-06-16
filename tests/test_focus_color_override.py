# -*- coding: utf-8 -*-
"""Customization: focus/accent word color override (jakson TYPE_4)."""
from __future__ import annotations

import importlib


def test_focus_color_override_from_env(monkeypatch):
    mod = importlib.import_module("app.scenes_3rd_reference_builder")
    default = list(mod.RENDER["color_red"])
    try:
        monkeypatch.setenv("SUBTITLES_FOCUS_HEX", "#00FF00")
        mod._apply_focus_color_override()
        assert mod.RENDER["color_red"][0] == 0.0
        assert mod.RENDER["color_red"][1] == 1.0
        assert mod.RENDER["color_red"][2] == 0.0
    finally:
        mod.RENDER["color_red"] = default  # restore module state


def test_focus_color_noop_when_absent(monkeypatch):
    mod = importlib.import_module("app.scenes_3rd_reference_builder")
    default = list(mod.RENDER["color_red"])
    monkeypatch.delenv("SUBTITLES_FOCUS_HEX", raising=False)
    mod._apply_focus_color_override()
    assert mod.RENDER["color_red"] == default
    # invalid hex → unchanged too
    monkeypatch.setenv("SUBTITLES_FOCUS_HEX", "zzz")
    mod._apply_focus_color_override()
    assert mod.RENDER["color_red"] == default
