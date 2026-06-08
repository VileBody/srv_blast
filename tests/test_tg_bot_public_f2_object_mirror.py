# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the F2 «Объект» data layer of tg_bot_botapi.

The single-step shape picker UX lives in tg_bot_botapi; the public bot only
mirrors the state machine (stage + ChatState field), the shape id set / RU-label
map, and the orchestrator-client `f2_shape` kwarg (UI gated by HOOK_FLOW_ENABLED).
"""
from __future__ import annotations

import inspect


def test_f2_stage_present_and_in_hook_stages():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import STAGE_WAIT_F2_SHAPE

    assert STAGE_WAIT_F2_SHAPE in pub.HOOK_STAGES


def test_f2_id_set_and_labels_consistent():
    from services.tg_bot_public import app as pub

    assert pub.F2_SHAPE_IDS == {"rhomb", "square", "star1", "star2", "elipse"}
    # every RU label maps to a known id
    assert set(pub.F2_SHAPE_LABELS_RU.values()) == pub.F2_SHAPE_IDS
    # exactly 5 buttons
    assert len(pub.F2_SHAPE_LABELS_RU) == 5


def test_chatstate_has_f2_field_defaulting_empty():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.f2_shape == ""


def test_orchestrator_client_accepts_f2_kwarg():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    assert "f2_shape" in sig.parameters


def test_f2_shape_ids_match_orchestrator_schema():
    """The shape Literal in the schema is the contract — public bot's id set
    must match it exactly (drift here = silent prod failure when the public
    bot mirrors state but the orchestrator rejects f2_shape)."""
    from services.tg_bot_public import app as pub
    from services.orchestrator.schemas import SendAudioS3Request

    # Pull the Literal arguments out of the f2_shape field annotation.
    field = SendAudioS3Request.model_fields["f2_shape"]
    # Optional[Literal[...]] → unwrap.
    import typing

    ann = field.annotation
    schema_ids: set[str] = set()
    for arg in typing.get_args(ann):
        if arg is type(None):
            continue
        # nested Literal
        for lit in typing.get_args(arg):
            schema_ids.add(str(lit))
    assert schema_ids == pub.F2_SHAPE_IDS
