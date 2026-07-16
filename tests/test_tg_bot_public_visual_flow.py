from __future__ import annotations

import asyncio

import pytest

from services.orchestrator.schemas import SendAudioS3Request
from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_BG_INFO,
    STAGE_WAIT_VISUAL_STYLE,
    STAGE_WAIT_VISUAL_TRANSITION,
)


class _Store:
    async def set(self, st):
        self.state = st


class _Message:
    def __init__(self, text=""):
        self.text = text
        self.answers = []

    async def answer(self, text="", reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return self


class _App:
    store = _Store()


def test_transition_and_style_are_valid_without_drop():
    req = SendAudioS3Request(
        audio_s3_url="s3://bucket/audio.mp3",
        mode="with_gemini",
        lyrics_text="x",
        target_fragment="x",
        effect_transition="snap_wipe",
        effect_extra="xerox",
        effect_extra_full=True,
    )
    assert req.user_drop_t is None


def test_hook_still_requires_drop():
    with pytest.raises(ValueError, match="effect_hook requires user_drop_t"):
        SendAudioS3Request(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="x",
            target_fragment="x",
            effect_hook="hook_light",
        )


def test_background_info_screen_uses_approved_footage_copy():
    async def run():
        app = _App()
        st = ChatState(chat_id=1)
        msg = _Message()
        await public_app.BlastBotApp._ask_bg_info(app, msg, st, "footage")
        assert st.stage == STAGE_WAIT_BG_INFO
        assert st.pending_bg_mode == "footage"
        assert "чередовать их в роликах" in msg.answers[-1][0]
    asyncio.run(run())


def test_standalone_visual_picker_state_progression():
    async def run():
        app = _App()
        app._send_option_previews = lambda *args, **kwargs: _noop()
        st = ChatState(chat_id=1, bg_mode="footage")
        msg = _Message()
        await public_app.BlastBotApp._ask_visual_transition(app, msg, st)
        assert st.stage == STAGE_WAIT_VISUAL_TRANSITION
        await public_app.BlastBotApp._ask_visual_style(app, msg, st)
        assert st.stage == STAGE_WAIT_VISUAL_STYLE
    asyncio.run(run())


async def _noop():
    return None


def test_f3_hook_no_longer_routes_into_legacy_transition_picker():
    import inspect
    source = inspect.getsource(public_app.BlastBotApp._handle_wait_effect_hook)
    assert "_ask_effect_transition" not in source
    assert "_effect_summary_and_continue" in source


def test_new_style_previews_have_captions() -> None:
    import json
    from pathlib import Path

    store = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "hook_previews.json").read_text(encoding="utf-8")
    )["previews"]
    expected = {
        "effect_extra:blackwhite": "Ч/Б",
        "effect_extra:crystal_glow": "Crystal Glow",
        "effect_extra:night_vision": "Night Vision",
        "effect_extra:wave": "Wave",
    }
    for key, label in expected.items():
        assert store[key]["label"] == label
        assert store[key]["file_id"]
        assert store[key]["file_id_public"]

def test_no_hook_reopens_visual_pickers_after_stale_previous_run() -> None:
    import types

    async def run():
        app = _App()
        called = {"transition": 0}

        async def ask_visual_transition(_message, _state):
            called["transition"] += 1

        app._ask_visual_transition = ask_visual_transition
        app._proceed_to_versions_or_confirm = types.MethodType(
            public_app.BlastBotApp._proceed_to_versions_or_confirm, app
        )
        st = ChatState(
            chat_id=1,
            bg_mode="footage",
            visuals_done=True,
            visual_transition="snap_wipe",
            visual_style="night_vision",
            effect_hook="hook_light",
            effect_transition="minimax",
            effect_extra="wave",
        )
        msg = _Message(public_app.BTN_HOOK_NO)

        await public_app.BlastBotApp._handle_wait_hook_choice(app, msg, st)

        assert called["transition"] == 1
        assert st.visuals_done is False
        assert st.visual_transition == ""
        assert st.visual_style == ""
        assert st.effect_hook == ""
        assert st.effect_transition == ""
        assert st.effect_extra == ""

    asyncio.run(run())