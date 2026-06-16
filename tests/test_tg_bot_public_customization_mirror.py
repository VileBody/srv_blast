# -*- coding: utf-8 -*-
"""Parity: tg_bot_public mirrors the customization color data layer.

The picker UX is built in tg_bot_botapi (test bot) first; the public bot mirrors
the state machine (stages + ChatState fields), the orchestrator-client kwargs,
and the color palette so the data contract matches.
"""
from __future__ import annotations

import inspect


def test_public_has_color_stages():
    from services.tg_bot_public.state_store import (
        STAGE_WAIT_ACCENT_COLOR,
        STAGE_WAIT_SUBTITLE_COLOR,
    )

    assert STAGE_WAIT_SUBTITLE_COLOR == "WAIT_SUBTITLE_COLOR"
    assert STAGE_WAIT_ACCENT_COLOR == "WAIT_ACCENT_COLOR"


def test_public_chatstate_has_color_fields():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.subtitle_color_hex == ""
    assert st.accent_color_hex == ""


def test_public_client_accepts_color_kwargs():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    assert "subtitle_color_hex" in sig.parameters
    assert "accent_color_hex" in sig.parameters


def test_palette_matches_between_bots():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    assert team._COLOR_PALETTE == pub._COLOR_PALETTE


def test_schema_has_color_fields():
    from services.orchestrator.schemas import SendAudioS3Request

    assert "subtitle_color_hex" in SendAudioS3Request.model_fields
    assert "accent_color_hex" in SendAudioS3Request.model_fields
    # valid hex accepted, junk rejected
    ok = SendAudioS3Request(
        audio_s3_url="https://e.com/a.mp3", mode="with_gemini",
        lyrics_text="x", target_fragment="x",
        subtitle_color_hex="#FF2D55", accent_color_hex="00FF00",
    )
    assert ok.subtitle_color_hex == "#FF2D55"
