# -*- coding: utf-8 -*-
"""Parity: hook battery is team-bot only; public mirrors the data + gate=False."""
from __future__ import annotations

import asyncio


class _StubStore:
    def __init__(self, cursor):
        self._cursor = cursor

    async def get_rotation_cursor(self, chat_id, artist_id):
        return self._cursor

    async def get_rotation_history(self, chat_id, artist_id):
        return []


def _resolve(mod, *, cursor, offset, slots):
    """Drive a bot's _resolve_rotation_slot_for_enqueue with stubbed deps."""
    orig = mod.get_artist_rotation_slots
    mod.get_artist_rotation_slots = lambda artist_id: slots
    try:
        stub = mod.BlastBotApp.__new__(mod.BlastBotApp)
        stub.store = _StubStore(cursor)

        class _St:
            footage_artist_id = "artist_x"
            chat_id = 7

        coro = mod.BlastBotApp._resolve_rotation_slot_for_enqueue(stub, st=_St(), offset=offset)
        return asyncio.run(coro)
    finally:
        mod.get_artist_rotation_slots = orig


def test_rotation_offset_spreads_versions_both_bots():
    """offset=version_index lands each batch version on a different subgroup."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    slots = [("t0", "g0"), ("t1", "g1"), ("t2", "g2")]
    for mod in (team, pub):
        # cursor=0: version 0 keeps base slot, versions 1/2 step forward.
        assert _resolve(mod, cursor=0, offset=0, slots=slots)[:2] == ("t0", "g0")
        assert _resolve(mod, cursor=0, offset=1, slots=slots)[:2] == ("t1", "g1")
        assert _resolve(mod, cursor=0, offset=2, slots=slots)[:2] == ("t2", "g2")
        # wraps around the slot list, and respects the persisted cursor base.
        assert _resolve(mod, cursor=2, offset=2, slots=slots)[:2] == ("t1", "g1")


def test_public_battery_disabled():
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert pub.BATTERY_ENABLED is False
    assert team.BATTERY_ENABLED is True


def test_f5_lead_sec_mirrored():
    """F5 clip-reframe lead must match across bots (parity)."""
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert team.F5_LEAD_SEC == pub.F5_LEAD_SEC == 4.0


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


def test_min_reframe_clip_sec_mirrored():
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert team.MIN_REFRAME_CLIP_SEC == pub.MIN_REFRAME_CLIP_SEC == 7.0


def test_team_battery_f4_skipped_but_f5_uses_primary_on_early_drop():
    """F4's fixed cover needs drop >= lead → skipped on an early drop with no
    later candidate. F5 always uses the user's primary drop (adaptive lead), so
    it renders without auto-walking to a later drop. object/effect stay."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState

    stub = _battery_stub(team)
    st = ChatState(chat_id=3)
    st.hook_analysis_bpm = 92.0          # slow → big F4 lead (~6s)
    st.hook_drop_t = 1.4                 # too early for F4
    st.hook_drop_candidates = []         # no later candidate
    cats = {c["hook_category"]: c for c in stub._build_battery_cases(st)}
    assert "motion" not in cats          # F4 dropped (fixed cover, no room)
    assert "thought" in cats             # F5 renders on primary
    assert abs(cats["thought"]["hook_drop_t"] - 1.4) < 1e-6
    assert "object" in cats and "effect" in cats
    # A later candidate rescues F4; F5 still stays on the user's primary drop.
    st.hook_drop_candidates = [{"t": 10.0, "confidence": 0.5}]
    cases2 = {c["hook_category"]: c for c in stub._build_battery_cases(st)}
    assert abs(cases2["motion"]["hook_drop_t"] - 10.0) < 1e-6
    assert abs(cases2["thought"]["hook_drop_t"] - 1.4) < 1e-6  # primary, not walked


def test_team_battery_f4_avoids_too_late_drop_on_short_clip():
    """On a short clip, F4 must not pick a late drop that shrinks the reframed
    window below MIN_REFRAME_CLIP_SEC (which would crash the build's fast-start)."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState

    stub = _battery_stub(team)
    st = ChatState(chat_id=4)
    st.hook_analysis_bpm = 150.0         # fast → small lead (~3.7s)
    st.user_clip_start_sec = 0.0
    st.user_clip_end_sec = 11.5          # short clip
    st.hook_drop_t = 4.5                 # user's drop: leaves 11.5-(4.5-lead) clip
    # a strong but very late candidate at 10.7 → reframe would leave < 7s → reject
    st.hook_drop_candidates = [{"t": 10.7, "confidence": 0.9}]
    cats = {c["hook_category"]: c for c in stub._build_battery_cases(st)}
    assert "motion" in cats
    # nearest fitting drop is the early user drop, not the late 10.7 candidate
    assert cats["motion"]["hook_drop_t"] == 4.5
