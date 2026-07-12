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

    assert pub.F3_HOOK_IDS == {"hook_light", "shutter_effect", "flash_slow_shutter", "negative_zoom"}
    assert pub.F3_TRANSITION_IDS == {
        "snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts", "layer_shake",
    }
    assert pub.F3_EXTRA_IDS == {
        "xerox", "analog_glitch", "neon_extract", "old_camera",
        "blackwhite", "crystal_glow", "night_vision", "wave",
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


def test_negative_zoom_hook_wired_end_to_end():
    # overlay + manifest
    from mlcore.hooks.f3_effect.overlay import F3_HOOKS, build_overlay_jsx
    assert "negative_zoom" in F3_HOOKS
    js = build_overlay_jsx(hook="negative_zoom", drop_time=3.0)
    assert "negative zoom" in js  # script body inlined
    # schema accepts it
    from services.orchestrator.schemas import SendAudioS3Request
    req = SendAudioS3Request(
        audio_s3_url="https://example.com/a.mp3", mode="with_gemini",
        lyrics_text="x", target_fragment="x",
        effect_hook="negative_zoom", user_drop_t=3.0,
    )
    assert req.effect_hook == "negative_zoom"
    # bot buttons mirrored
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub
    assert team.BTN_FX_HOOK_NEGZOOM == pub.BTN_FX_HOOK_NEGZOOM
    assert team._FX_HOOK_BY_BUTTON[team.BTN_FX_HOOK_NEGZOOM] == "negative_zoom"
    assert pub._FX_HOOK_BY_BUTTON[pub.BTN_FX_HOOK_NEGZOOM] == "negative_zoom"


def test_orchestrator_client_accepts_effect_kwargs():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    for name in ("effect_hook", "effect_transition", "effect_extra", "effect_hook_extend"):
        assert name in sig.parameters, f"missing kwarg {name}"
