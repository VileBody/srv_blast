# -*- coding: utf-8 -*-
"""F3 «Эффект»: stretch the grade (extra) over the whole video. Team UX +
tg_bot_public mirror (stage/state/buttons/client) for the CI parity gate."""
from __future__ import annotations


def test_stage_and_field_mirrored():
    from services.tg_bot_botapi.state_store import (
        STAGE_WAIT_EFFECT_EXTRA_FULL as A, ChatState as CT_team)
    from services.tg_bot_public.state_store import (
        STAGE_WAIT_EFFECT_EXTRA_FULL as B, ChatState as CT_pub)

    assert A == B == "WAIT_EFFECT_EXTRA_FULL"
    assert CT_team(chat_id=1).effect_extra_full is False
    assert CT_pub(chat_id=1).effect_extra_full is False


def test_buttons_mirrored():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    assert team.BTN_FX_EXTRA_FULL_ALL == pub.BTN_FX_EXTRA_FULL_ALL
    assert team.BTN_FX_EXTRA_FULL_PREDROP == pub.BTN_FX_EXTRA_FULL_PREDROP


def test_orchestrator_client_accepts_kwarg_both_bots():
    import inspect
    from services.tg_bot_botapi.orchestrator_client import OrchestratorClient as T
    from services.tg_bot_public.orchestrator_client import OrchestratorClient as P

    for cls in (T, P):
        sig = inspect.signature(cls.send_audio_s3)
        assert "effect_extra_full" in sig.parameters


def test_schema_has_effect_extra_full():
    from services.orchestrator.schemas import SendAudioS3Request

    req = SendAudioS3Request(
        audio_s3_url="https://example.com/a.mp3",
        mode="with_gemini",
        lyrics_text="x",
        target_fragment="x",
        effect_extra="xerox",
        effect_extra_full=True,
        user_drop_t=3.0,
    )
    assert req.effect_extra_full is True


def test_overlay_extra_full_uses_null_duration():
    from mlcore.hooks.f3_effect.overlay import build_overlay_jsx

    full = build_overlay_jsx(extra="xerox", extra_full=True, drop_time=3.0)
    pre = build_overlay_jsx(extra="xerox", extra_full=False, drop_time=3.0)
    assert "duration: null" in full
    assert "duration: (__f3_drop>0?__f3_drop:null)" in pre
