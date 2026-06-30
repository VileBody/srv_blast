# -*- coding: utf-8 -*-
"""Parity: B/W strobe background option mirrored to tg_bot_public + schema."""
from __future__ import annotations


def test_strobe_button_mirrored():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    assert team.BTN_BG_STROBE == pub.BTN_BG_STROBE == "Строб Ч/Б"


def test_schema_accepts_solid_strobe():
    from services.orchestrator.schemas import SendAudioS3Request

    req = SendAudioS3Request(
        audio_s3_url="https://example.com/a.mp3", mode="with_gemini",
        lyrics_text="x", target_fragment="x", bg_mode="solid_strobe",
    )
    assert req.bg_mode == "solid_strobe"


def test_both_bots_have_default_artist_helper():
    from services.tg_bot_botapi.app import BlastBotApp as T
    from services.tg_bot_public.app import BlastBotApp as P

    assert hasattr(T, "_ensure_solid_default_artist")
    assert hasattr(P, "_ensure_solid_default_artist")


def test_strobe_cut_sends_transition_previews_both_bots():
    import inspect
    from services.tg_bot_botapi.app import BlastBotApp as T
    from services.tg_bot_public.app import BlastBotApp as P

    for cls in (T, P):
        src = inspect.getsource(cls._ask_strobe_cut)
        assert "_send_option_previews" in src
        assert "effect_transition:" in src


def test_strobe_cut_stage_and_methods_mirrored():
    from services.tg_bot_botapi.state_store import STAGE_WAIT_STROBE_CUT as A
    from services.tg_bot_public.state_store import STAGE_WAIT_STROBE_CUT as B
    from services.tg_bot_botapi.app import BlastBotApp as T
    from services.tg_bot_public.app import BlastBotApp as P

    assert A == B == "WAIT_STROBE_CUT"
    for cls in (T, P):
        for m in ("_ask_strobe_cut", "_handle_wait_strobe_cut", "_default_strobe_drop"):
            assert hasattr(cls, m), (cls.__name__, m)


def test_default_strobe_drop_clamps_inside_clip():
    from services.tg_bot_botapi.app import BlastBotApp as T
    from services.tg_bot_botapi.state_store import ChatState

    bot = T.__new__(T)
    st = ChatState(chat_id=1)
    st.user_clip_start_sec = 0.0
    st.user_clip_end_sec = 12.0
    # no candidates → clip_start + 1.0
    assert abs(bot._default_strobe_drop(st) - 1.0) < 1e-6
    # analysed candidate inside clip is used
    st.hook_drop_candidates = [{"t": 5.5}]
    assert abs(bot._default_strobe_drop(st) - 5.5) < 1e-6


def test_jsx_subtitles_inject_difference_blend():
    from app.jsx_subtitles_builder import build_jsx_subtitles_overlay, word_timings_from_transcript
    from core.subtitles_mode import SUBTITLES_MODE_BRAT_5TH

    wt = word_timings_from_transcript([{"text": "йо", "t_start": 0.0, "t_end": 0.4}])
    js = build_jsx_subtitles_overlay(mode=SUBTITLES_MODE_BRAT_5TH, word_timings=wt, subs_blend="difference")
    assert '$.global.__BLAST_SUBS_BLEND = "difference"' in js


def test_f3_detect_cuts_recognises_strobe_bg():
    from mlcore.hooks.f3_effect.overlay import build_overlay_jsx
    js = build_overlay_jsx(transition="snap_wipe", drop_time=3.0)
    assert 'strobe_bg_' in js  # detectCuts also picks the strobe segment solids
