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
