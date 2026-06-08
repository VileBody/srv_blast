# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the F1 «Звук» data layer of tg_bot_botapi.

The upload UX lives in tg_bot_botapi; the public bot mirrors the state machine
(stage + ChatState field) and the orchestrator-client `f1_sound_url` kwarg
(UI gated by HOOK_FLOW_ENABLED). F1 carries a URL, not an enum — no id-set.
"""
from __future__ import annotations

import inspect


def test_f1_stage_present_and_in_hook_stages():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import STAGE_WAIT_F1_SOUND

    assert STAGE_WAIT_F1_SOUND in pub.HOOK_STAGES


def test_chatstate_has_f1_field_defaulting_empty():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.f1_sound_url == ""


def test_orchestrator_client_accepts_f1_kwarg():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    assert "f1_sound_url" in sig.parameters


def test_schema_has_f1_sound_url_field():
    from services.orchestrator.schemas import SendAudioS3Request

    assert "f1_sound_url" in SendAudioS3Request.model_fields
