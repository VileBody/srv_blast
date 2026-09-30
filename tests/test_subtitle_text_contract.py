"""Настройки текста визарда: orchestrator schema/env → build (движок стиля). Web-часть —
tests/test_web_subtitle_text.py (у сайта свой пакет `app`, в одном процессе с корневым нельзя)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app import scenes_3rd_reference_builder as jakson
from app import template_4th_reference_builder as tape
from app.subtitle_text_style import (
    SubtitleTextStyle,
    applied_text_style,
    jsx_style_config,
    style_from_env,
)
from core.subtitles_mode import (
    SUBTITLES_MODE_BRAT_5TH,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_TEMPLATE_4TH,
    SUBTITLES_MODE_TRENDY_5TH,
)
from services.orchestrator.schemas import SubtitleTextStyle as SchemaStyle
from services.orchestrator.tasks import subtitle_text_style_env_value

# --- orchestrator schema/env ------------------------------------------------

def test_schema_is_strict_and_env_round_trips_into_the_build():
    with pytest.raises(ValidationError):
        SchemaStyle.model_validate({"font": "Inter-Bold", "outline": "thick"})
    with pytest.raises(ValidationError):
        SchemaStyle.model_validate({"size": "huge"})
    raw = subtitle_text_style_env_value({"subtitle_text_style": {"font": "Inter-Bold", "shadow": "none"}})
    assert json.loads(raw)["font"] == "Inter-Bold"
    assert subtitle_text_style_env_value({}) is None
    assert SubtitleTextStyle.from_json(raw) == SubtitleTextStyle(font="Inter-Bold", shadow="none")


def test_build_env_is_strict(monkeypatch):
    monkeypatch.setenv("SUBTITLE_TEXT_STYLE_JSON", json.dumps({"font": "Inter-Bold", "outline": 3}))
    with pytest.raises(RuntimeError, match="unknown keys"):
        style_from_env()


# --- build: engine per subtitles mode ----------------------------------------

def test_build_applies_and_resets_python_style_layouts(monkeypatch):
    monkeypatch.delenv("RENDER_PRESET", raising=False)
    with applied_text_style(SUBTITLES_MODE_SCENES_3RD, SubtitleTextStyle(font="Inter-Bold")):
        assert jakson._LAYOUT is not None and jakson.RENDER["font_base"] == "Inter-Bold"
    assert jakson._LAYOUT is None and jakson.RENDER["font_base"] == "Point-SemiBold"
    with applied_text_style(SUBTITLES_MODE_TEMPLATE_4TH, SubtitleTextStyle(size="small")):
        assert tape._TAPE_LAYOUT is not None and tape._TAPE_LAYOUT.font == "Montserrat-BoldItalic"
    assert tape._TAPE_LAYOUT is None
    # без настроек — прод не трогаем вовсе
    with applied_text_style(SUBTITLES_MODE_SCENES_3RD, SubtitleTextStyle()):
        assert jakson._LAYOUT is None


def test_build_rejects_impossible_settings(monkeypatch):
    monkeypatch.delenv("RENDER_PRESET", raising=False)
    with pytest.raises(ValueError, match="script font"):
        with applied_text_style(SUBTITLES_MODE_SCENES_3RD, SubtitleTextStyle(font="Katherine-Plus")):
            pass
    with pytest.raises(ValueError, match="only for render presets"):
        with applied_text_style(SUBTITLES_MODE_SCENES_3RD, SubtitleTextStyle(position="down")):
            pass
    with pytest.raises(ValueError, match="not supported"):
        with applied_text_style(SUBTITLES_MODE_LEGACY_BLOCKS, SubtitleTextStyle(size="small")):
            pass
    assert jakson._LAYOUT is None


def test_build_jsx_modes_get_engine_config(monkeypatch):
    monkeypatch.delenv("RENDER_PRESET", raising=False)
    assert jsx_style_config(SUBTITLES_MODE_TRENDY_5TH, None) is None
    trendy = jsx_style_config(SUBTITLES_MODE_TRENDY_5TH, SubtitleTextStyle(font="Inter-Bold"))
    assert trendy["font"] == "Inter-Bold" and trendy["verticalScale"] == 4.0
    brat = jsx_style_config(SUBTITLES_MODE_BRAT_5TH, SubtitleTextStyle(focus_style="italic"), "#FF5FA8")
    assert brat["focusFont"] == "ArialNarrow-Italic" and brat["focusFillColor"][0] == 1.0
    with pytest.raises(ValueError):
        jsx_style_config(SUBTITLES_MODE_BRAT_5TH, SubtitleTextStyle(font="Point-SemiBold"))
