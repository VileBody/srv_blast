# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the F3 «Эффект» data layer of tg_bot_botapi.

The 3-step picker UX lives in tg_bot_botapi; the public bot only mirrors the
state machine (stages + ChatState fields), the effect id sets / RU-label maps,
and the orchestrator-client effect_* kwargs (UI gated by HOOK_FLOW_ENABLED).
"""
from __future__ import annotations

import inspect


def test_effect_stages_present_and_in_hook_stages():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import (
        STAGE_WAIT_EFFECT_HOOK,
        STAGE_WAIT_EFFECT_TRANSITION,
        STAGE_WAIT_EFFECT_EXTRA,
        STAGE_WAIT_EFFECT_EXTEND,
    )
    for stage in (
        STAGE_WAIT_EFFECT_HOOK,
        STAGE_WAIT_EFFECT_TRANSITION,
        STAGE_WAIT_EFFECT_EXTRA,
        STAGE_WAIT_EFFECT_EXTEND,
    ):
        assert stage in pub.HOOK_STAGES


def test_effect_id_sets_and_labels_consistent():
    from services.tg_bot_public import app as pub

    assert pub.F3_HOOK_IDS == {"hook_light", "shutter_effect", "flash_slow_shutter"}
    assert pub.F3_TRANSITION_IDS == {
        "snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts", "layer_shake",
    }
    assert pub.F3_EXTRA_IDS == {
        "xerox", "analog_glitch", "neon_extract", "old_camera", "pixel_grain", "warm_map",
    }
    # every RU label maps to a known id
    assert set(pub.F3_HOOK_LABELS_RU.values()) == pub.F3_HOOK_IDS
    assert set(pub.F3_TRANSITION_LABELS_RU.values()) == pub.F3_TRANSITION_IDS
    assert set(pub.F3_EXTRA_LABELS_RU.values()) == pub.F3_EXTRA_IDS
    # extend: "" (standard) + the two extension modes
    assert set(pub.F3_EXTEND_LABELS_RU.values()) == {"", "to_end", "after_drop:3"}


def test_chatstate_has_effect_fields_defaulting_empty():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.effect_hook == ""
    assert st.effect_transition == ""
    assert st.effect_extra == ""
    assert st.effect_hook_extend == ""


def test_orchestrator_client_accepts_effect_kwargs():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    for name in ("effect_hook", "effect_transition", "effect_extra", "effect_hook_extend"):
        assert name in sig.parameters, f"missing kwarg {name}"
