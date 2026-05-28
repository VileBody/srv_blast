"""
Unit tests for the pure helpers added to BlastBotApp for the hook flow.
Covers parsing/validation only — aiogram message dispatch is not exercised.
"""

from __future__ import annotations

import pytest

from services.tg_bot_botapi.app import BlastBotApp
from services.tg_bot_botapi.state_store import ChatState


def test_parse_single_timing_accepts_seconds():
    assert BlastBotApp._parse_single_timing("12") == 12.0
    assert BlastBotApp._parse_single_timing("83.5") == 83.5


def test_parse_single_timing_accepts_mmss():
    assert BlastBotApp._parse_single_timing("1:23") == pytest.approx(83.0)
    assert BlastBotApp._parse_single_timing("0:45") == pytest.approx(45.0)
    assert BlastBotApp._parse_single_timing("2:00") == pytest.approx(120.0)


def test_parse_single_timing_rejects_bad_input():
    assert BlastBotApp._parse_single_timing("") is None
    assert BlastBotApp._parse_single_timing("abc") is None
    assert BlastBotApp._parse_single_timing("-5") is None
    # Seconds must be < 60 in mm:ss form.
    assert BlastBotApp._parse_single_timing("1:75") is None


def test_parse_hook_drop_label_matches_candidate():
    candidates = [
        {"t": 12.0, "confidence": 0.87, "snapped_to_beat": True, "source": "x"},
        {"t": 19.5, "confidence": 0.62, "snapped_to_beat": False, "source": "y"},
    ]
    # Label format the bot produces: "<mm:ss> (NN%)" optionally prefixed by 🎯.
    assert BlastBotApp._parse_hook_drop_label(
        "🎯 0:12 (87%)", candidates=candidates
    ) == 12.0
    assert BlastBotApp._parse_hook_drop_label(
        "0:19 (62%)", candidates=candidates
    ) == 19.5


def test_parse_hook_drop_label_unmatched_returns_none():
    candidates = [{"t": 12.0, "confidence": 0.87, "snapped_to_beat": False, "source": "x"}]
    assert BlastBotApp._parse_hook_drop_label("0:30 (50%)", candidates=candidates) is None
    assert BlastBotApp._parse_hook_drop_label("", candidates=candidates) is None


def test_validate_hook_drop_inside_clip_with_window():
    st = ChatState(chat_id=1, user_clip_start_sec=10.0, user_clip_end_sec=30.0)
    assert BlastBotApp._validate_hook_drop_inside_clip(15.0, st) is True
    assert BlastBotApp._validate_hook_drop_inside_clip(10.0, st) is True
    assert BlastBotApp._validate_hook_drop_inside_clip(30.0, st) is True
    assert BlastBotApp._validate_hook_drop_inside_clip(9.99, st) is False
    assert BlastBotApp._validate_hook_drop_inside_clip(30.01, st) is False


def test_validate_hook_drop_no_clip_accepts_any_nonneg():
    """When user skipped focus clip (start==end==0) any non-negative t is OK."""
    st = ChatState(chat_id=1, user_clip_start_sec=0.0, user_clip_end_sec=0.0)
    assert BlastBotApp._validate_hook_drop_inside_clip(0.0, st) is True
    assert BlastBotApp._validate_hook_drop_inside_clip(120.0, st) is True
    assert BlastBotApp._validate_hook_drop_inside_clip(-1.0, st) is False


def test_chat_state_hook_fields_have_safe_defaults():
    st = ChatState(chat_id=42)
    assert st.hook_enabled is False
    assert st.hook_drop_t is None
    assert st.hook_type == "standard"
    assert st.hook_analysis_status == ""
    assert st.hook_drop_candidates == []
