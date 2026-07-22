# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the photo flow (4:3) data layer of tg_bot_botapi.

The two-step photo picker UX (stylization → transition) lives in tg_bot_botapi; the
public bot mirrors the state machine (stages + ChatState fields), the style/transition
id sets + RU-label maps, and the orchestrator-client photo_* kwargs. The picker UX is
gated behind PHOTO_FLOW_ENABLED, but state/client/stages mirror regardless for CI parity.
"""
from __future__ import annotations

import inspect

from pathlib import Path
import typing


def _schema_literal_ids(field_name: str) -> set[str]:
    """Pull the Literal arguments out of an Optional[Literal[...]] schema field."""
    from services.orchestrator.schemas import SendAudioS3Request

    ann = SendAudioS3Request.model_fields[field_name].annotation
    out: set[str] = set()
    for arg in typing.get_args(ann):
        if arg is type(None):
            continue
        for lit in typing.get_args(arg):
            out.add(str(lit))
    return out


def test_photo_stages_present_and_in_photo_stages():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import (
        STAGE_WAIT_PHOTO_STYLE,
        STAGE_WAIT_PHOTO_TRANSITION,
    )

    assert STAGE_WAIT_PHOTO_STYLE in pub.PHOTO_STAGES
    assert STAGE_WAIT_PHOTO_TRANSITION in pub.PHOTO_STAGES


def test_team_photo_flag_defaults_on_and_public_stays_default_off(monkeypatch):
    from services.tg_bot_botapi import app as team

    monkeypatch.delenv("PHOTO_FLOW_ENABLED", raising=False)
    assert team._photo_flow_enabled()

    root = Path(__file__).resolve().parents[1]
    public_src = (root / "services" / "tg_bot_public" / "app.py").read_text(encoding="utf-8")
    assert 'os.environ.get("PHOTO_FLOW_ENABLED", "0")' in public_src

def test_photo_style_id_set_and_labels_consistent():
    from services.tg_bot_public import app as pub

    assert pub.PHOTO_STYLE_IDS == {"none", "warm", "cold", "vintage", "bw", "vhs", "night_vision"}
    # every RU label maps to a known id
    assert set(pub.PHOTO_STYLE_LABELS_RU.values()) == pub.PHOTO_STYLE_IDS
    assert len(pub.PHOTO_STYLE_LABELS_RU) == 7


def test_photo_transition_id_set_and_labels_consistent():
    from services.tg_bot_public import app as pub

    assert pub.PHOTO_TRANSITION_IDS == {"flash", "none", "slide", "zoom", "whip"}
    assert set(pub.PHOTO_TRANSITION_LABELS_RU.values()) == pub.PHOTO_TRANSITION_IDS
    assert len(pub.PHOTO_TRANSITION_LABELS_RU) == 5


def test_chatstate_has_photo_fields_defaulting_empty():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.photo_style == ""
    assert st.photo_transition == ""


def test_orchestrator_client_accepts_photo_kwargs():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    assert "photo_style" in sig.parameters
    assert "photo_transition" in sig.parameters


def test_photo_ids_match_orchestrator_schema():
    """The photo_style / photo_transition Literals in the schema are the contract —
    the public bot's id sets must match exactly (drift here = silent prod failure
    when the bot mirrors state but the orchestrator rejects the value)."""
    from services.tg_bot_public import app as pub

    assert _schema_literal_ids("photo_style") == pub.PHOTO_STYLE_IDS
    assert _schema_literal_ids("photo_transition") == pub.PHOTO_TRANSITION_IDS


def test_team_and_public_photo_maps_match():
    """Both bots must offer the same style/transition id sets (mirror parity)."""
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert team.PHOTO_STYLE_IDS == pub.PHOTO_STYLE_IDS
    assert team.PHOTO_TRANSITION_IDS == pub.PHOTO_TRANSITION_IDS
