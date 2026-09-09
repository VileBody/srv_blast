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


# ---------- always-full стилизации (blackwhite) ----------


def test_always_full_set_mirrors_the_manifest():
    """Список «всегда на весь ролик» в ботах = manifest.full_window."""
    import json
    from pathlib import Path

    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    manifest = json.loads(
        (Path(__file__).resolve().parents[1]
         / "mlcore" / "hooks" / "f3_effect" / "manifest.json").read_text(encoding="utf-8")
    )
    from_manifest = {
        str(e["id"]) for e in manifest["effects"] if e.get("full_window")
    }
    assert from_manifest == {"blackwhite"}
    assert set(pub.FX_EXTRA_ALWAYS_FULL) == from_manifest
    assert set(team.FX_EXTRA_ALWAYS_FULL) == from_manifest


def test_blackwhite_skips_the_window_question_in_both_bots():
    """Спрашивать окно у ЧБ бессмысленно: build-side форсит полное окно, и
    ответ «до дропа» был бы молча проигнорирован."""
    import inspect

    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    for mod in (pub, team):
        bot_cls = next(
            obj for _, obj in vars(mod).items()
            if inspect.isclass(obj) and hasattr(obj, "_handle_wait_effect_extra")
        )
        src = inspect.getsource(bot_cls._handle_wait_effect_extra)
        assert "FX_EXTRA_ALWAYS_FULL" in src
        assert "effect_extra_full = True" in src


def test_build_side_forces_the_full_window_for_blackwhite():
    """Даже если бот прислал extra_full=False, окно всё равно полное."""
    from mlcore.hooks.f3_effect.overlay import build_overlay_jsx

    js = build_overlay_jsx(extra="blackwhite", extra_full=False, drop_time=4.2)
    assert "duration: null" in js
    # у обычного грейда окно по-прежнему обрезается дропом
    other = build_overlay_jsx(extra="wave", extra_full=False, drop_time=4.2)
    assert "duration: (__f3_drop>0?__f3_drop:null)" in other
