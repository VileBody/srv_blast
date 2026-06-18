# -*- coding: utf-8 -*-
"""Parity: hook battery is team-bot only; public mirrors the data + gate=False."""
from __future__ import annotations


def test_public_battery_disabled():
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert pub.BATTERY_ENABLED is False
    assert team.BATTERY_ENABLED is True


def test_public_chatstate_has_battery_fields():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.battery_mode is False
    assert st.battery_cases == []


def test_battery_stage_mirrored():
    from services.tg_bot_botapi.state_store import STAGE_WAIT_BATTERY_SOUND as A
    from services.tg_bot_public.state_store import STAGE_WAIT_BATTERY_SOUND as B

    assert A == B == "WAIT_BATTERY_SOUND"


def _battery_stub(team):
    # _build_battery_cases calls self._f4_effective_lead — provide it.
    class _Stub:
        _f4_effective_lead = staticmethod(team.BlastBotApp._f4_effective_lead)
        _build_battery_cases = team.BlastBotApp._build_battery_cases
    return _Stub()


def test_team_battery_cases_one_per_category():
    """_build_battery_cases yields unique categories (no repeat within a track)."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState

    stub = _battery_stub(team)
    # bpm>0, a sound, and a late-enough drop → all 5 categories
    st = ChatState(chat_id=1)
    st.hook_analysis_bpm = 120.0
    st.f1_sound_url = "s3://b/snd.mp3"
    st.hook_drop_t = 12.0
    cases = stub._build_battery_cases(st)
    cats = [c["hook_category"] for c in cases]
    assert sorted(cats) == ["effect", "motion", "object", "sound", "thought"]
    assert len(cats) == len(set(cats))  # no repeats
    # every case carries its own drop
    assert all(c.get("hook_drop_t") is not None for c in cases)


def test_team_battery_cases_inherit_colors():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState

    stub = _battery_stub(team)
    st = ChatState(chat_id=9)
    st.hook_analysis_bpm = 120.0
    st.hook_drop_t = 12.0
    st.subtitle_color_hex = "#FF2D55"
    st.accent_color_hex = "#34C759"
    cases = stub._build_battery_cases(st)
    assert cases and all(c["subtitle_color_hex"] == "#FF2D55" for c in cases)
    assert all(c["accent_color_hex"] == "#34C759" for c in cases)
    # no bpm, no sound → object/effect/thought only
    st2 = ChatState(chat_id=2)
    st2.hook_drop_t = 12.0
    cases2 = stub._build_battery_cases(st2)
    assert sorted(c["hook_category"] for c in cases2) == ["effect", "object", "thought"]


def test_team_battery_f4_skipped_when_drop_too_early():
    """F4 needs drop >= lead; an early drop with no later candidate drops F4."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState

    stub = _battery_stub(team)
    st = ChatState(chat_id=3)
    st.hook_analysis_bpm = 92.0          # slow → big lead (~6s)
    st.hook_drop_t = 1.4                 # too early for F4
    st.hook_drop_candidates = []         # no later candidate
    cases = stub._build_battery_cases(st)
    cats = [c["hook_category"] for c in cases]
    assert "motion" not in cats          # F4 dropped
    assert "object" in cats and "thought" in cats  # others still there
    # a later candidate rescues F4
    st.hook_drop_candidates = [{"t": 10.0, "confidence": 0.5}]
    cases2 = stub._build_battery_cases(st)
    motion = [c for c in cases2 if c["hook_category"] == "motion"]
    assert motion and abs(motion[0]["hook_drop_t"] - 10.0) < 1e-6
