# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the footage precision (vibe) flow data
layer of tg_bot_botapi (Phase 2b).

The ranked-shortlist vibe multi-select UX (genre/artist → vibe reroute, enqueue
bucket distribution, auto-cursor removal) lives in tg_bot_botapi. The public bot
only mirrors the state machine (STAGE_WAIT_VIBE + ChatState vibe_* fields), the
OrchestratorClient.rank_buckets wiring, and an env flag + routing guard that
stays OFF (handlers are not ported) until roll-forward.
"""
from __future__ import annotations

import inspect


def test_vibe_stage_strings_match_across_bots():
    from services.tg_bot_botapi.state_store import STAGE_WAIT_VIBE as team_stage
    from services.tg_bot_public.state_store import STAGE_WAIT_VIBE as pub_stage

    assert team_stage == pub_stage == "WAIT_VIBE"


def test_vibe_stage_in_public_vibe_stages_frozenset():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import STAGE_WAIT_VIBE

    assert STAGE_WAIT_VIBE in pub.VIBE_STAGES


def test_chatstate_vibe_fields_default_consistent_across_bots():
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public.state_store import ChatState as PubState

    team = TeamState(chat_id=1)
    pub = PubState(chat_id=1)
    for st in (team, pub):
        assert st.vibe_ranked_ids == []
        assert st.vibe_labels_by_id == {}
        assert st.vibe_page == 0
        assert st.vibe_selected_ids == []
        assert st.vibe_rank_status == ""


def test_chatstate_vibe_fields_roundtrip():
    """A public chat state carrying mirrored vibe_* values must round-trip
    through JSON (so a chat pre-populated by a roll-forward survives reload)."""
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(
        chat_id=7,
        vibe_ranked_ids=["heartbreak_minor:eerie_nature", "love_major:warm_sun"],
        vibe_labels_by_id={"heartbreak_minor:eerie_nature": "Тревожная природа"},
        vibe_page=1,
        vibe_selected_ids=["heartbreak_minor:eerie_nature"],
        vibe_rank_status="ready",
    )
    again = ChatState.model_validate_json(st.model_dump_json())
    assert again.vibe_ranked_ids == st.vibe_ranked_ids
    assert again.vibe_labels_by_id == st.vibe_labels_by_id
    assert again.vibe_page == 1
    assert again.vibe_selected_ids == st.vibe_selected_ids
    assert again.vibe_rank_status == "ready"


def test_orchestrator_client_rank_buckets_signature_parity():
    from services.tg_bot_botapi.orchestrator_client import (
        OrchestratorClient as TeamClient,
    )
    from services.tg_bot_public.orchestrator_client import (
        OrchestratorClient as PubClient,
    )

    team_sig = inspect.signature(TeamClient.rank_buckets)
    pub_sig = inspect.signature(PubClient.rank_buckets)
    assert set(team_sig.parameters) == set(pub_sig.parameters)
    for name in ("lyrics", "mood", "top"):
        assert name in pub_sig.parameters


def test_public_vibe_flow_flag_off_by_default(monkeypatch):
    """Default-off in public: with no env override the routing guard is False
    even for a chat already parked on STAGE_WAIT_VIBE (handlers not ported)."""
    monkeypatch.delenv("FOOTAGE_VIBE_FLOW_ENABLED", raising=False)
    import importlib

    from services.tg_bot_public import app as pub
    pub = importlib.reload(pub)
    from services.tg_bot_public.state_store import ChatState, STAGE_WAIT_VIBE

    assert pub.FOOTAGE_VIBE_FLOW_ENABLED is False
    st = ChatState(chat_id=1, stage=STAGE_WAIT_VIBE)
    assert pub._should_route_to_vibe_flow(st) is False
