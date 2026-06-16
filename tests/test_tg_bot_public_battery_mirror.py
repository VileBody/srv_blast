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


def test_team_battery_cases_one_per_category():
    """_build_battery_cases yields unique categories (no repeat within a track)."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState

    # bpm>0 and a sound → all 5 categories
    st = ChatState(chat_id=1)
    st.hook_analysis_bpm = 120.0
    st.f1_sound_url = "s3://b/snd.mp3"
    st.hook_drop_t = 8.0
    cases = team.BlastBotApp._build_battery_cases(None, st)  # self unused
    cats = [c["hook_category"] for c in cases]
    assert sorted(cats) == ["effect", "motion", "object", "sound", "thought"]
    assert len(cats) == len(set(cats))  # no repeats
    # no bpm, no sound → object/effect/thought only
    st2 = ChatState(chat_id=2)
    st2.hook_drop_t = 8.0
    cases2 = team.BlastBotApp._build_battery_cases(None, st2)
    assert sorted(c["hook_category"] for c in cases2) == ["effect", "object", "thought"]
