from __future__ import annotations

import asyncio
import types

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_STYLE_SKIP_CONFIRM,
    STAGE_WAIT_VISUAL_STYLE,
)


class _Store:
    async def set(self, st):
        self.state = st


class _Message:
    def __init__(self, text: str = ""):
        self.text = text
        self.answers = []

    async def answer(self, text="", reply_markup=None, **_kwargs):
        self.answers.append((text, reply_markup))
        return self


class _App:
    def __init__(self):
        self.store = _Store()


def _button_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def _bind(app, *names: str) -> None:
    for name in names:
        setattr(
            app,
            name,
            types.MethodType(getattr(public_app.BlastBotApp, name), app),
        )


def test_visual_style_restores_skip_button_and_opens_warning() -> None:
    async def run():
        app = _App()

        async def previews(*_args, **_kwargs):
            return None

        app._send_option_previews = previews
        _bind(app, "_ask_visual_style", "_handle_wait_visual_style", "_ask_style_skip_confirm")
        st = ChatState(chat_id=1, bg_mode="footage")
        message = _Message()

        await app._ask_visual_style(message, st)
        assert st.stage == STAGE_WAIT_VISUAL_STYLE
        assert public_app.BTN_FX_SKIP in _button_texts(message.answers[-1][1])

        message.text = public_app.BTN_FX_SKIP
        await app._handle_wait_visual_style(message, st)
        warning, keyboard = message.answers[-1]
        assert st.stage == STAGE_WAIT_STYLE_SKIP_CONFIRM
        assert st.style_skip_origin == public_app.STYLE_SKIP_ORIGIN_VISUAL
        assert "менее уникальным" in warning
        assert "блокировки именно этого ролика" in warning
        assert "субтитры перекрывают по времени все использованные фрагменты" in warning
        assert "CapCut" in warning
        assert _button_texts(keyboard) == [public_app.BTN_CONFIRM, public_app.BTN_BACK]

    asyncio.run(run())


def test_back_returns_to_the_same_style_step_without_losing_selection() -> None:
    async def run():
        app = _App()
        seen = {"visual": 0}

        async def ask_visual(_message, state):
            seen["visual"] += 1
            state.stage = STAGE_WAIT_VISUAL_STYLE

        app._ask_visual_style = ask_visual
        _bind(app, "_handle_wait_style_skip_confirm")
        st = ChatState(
            chat_id=1,
            stage=STAGE_WAIT_STYLE_SKIP_CONFIRM,
            style_skip_origin=public_app.STYLE_SKIP_ORIGIN_VISUAL,
            visual_transition="snap_wipe",
        )

        await app._handle_wait_style_skip_confirm(_Message(public_app.BTN_BACK), st)
        assert seen == {"visual": 1}
        assert st.stage == STAGE_WAIT_VISUAL_STYLE
        assert st.style_skip_origin == ""
        assert st.visual_transition == "snap_wipe"

    asyncio.run(run())


def test_confirm_skip_finishes_standalone_visual_style() -> None:
    async def run():
        app = _App()
        seen = {"next": 0}

        async def proceed(_message, _state):
            seen["next"] += 1

        app._proceed_to_versions_or_confirm = proceed
        _bind(app, "_handle_wait_style_skip_confirm")
        st = ChatState(
            chat_id=1,
            stage=STAGE_WAIT_STYLE_SKIP_CONFIRM,
            style_skip_origin=public_app.STYLE_SKIP_ORIGIN_VISUAL,
            visual_transition="minimax",
            visual_style="wave",
        )

        await app._handle_wait_style_skip_confirm(_Message(public_app.BTN_CONFIRM), st)
        assert seen == {"next": 1}
        assert st.visual_style == ""
        assert st.visuals_done is True
        assert st.visual_transition == "minimax"
        assert st.style_skip_origin == ""

    asyncio.run(run())


def test_photo_and_effect_style_skips_use_the_same_confirmation() -> None:
    async def run_photo():
        app = _App()
        seen = {"next": 0}

        async def proceed(_message, _state):
            seen["next"] += 1

        app._proceed_to_versions_or_confirm = proceed
        _bind(app, "_handle_wait_style_skip_confirm")
        st = ChatState(
            chat_id=1,
            stage=STAGE_WAIT_STYLE_SKIP_CONFIRM,
            style_skip_origin=public_app.STYLE_SKIP_ORIGIN_PHOTO,
            photo_transition="zoom",
        )
        await app._handle_wait_style_skip_confirm(_Message(public_app.BTN_CONFIRM), st)
        assert seen == {"next": 1}
        assert st.photo_style == "none"
        assert st.photo_transition == "zoom"
        assert st.visuals_done is True

    async def run_effect():
        app = _App()
        seen = {"next": 0}

        async def after(_message, _state):
            seen["next"] += 1

        app._after_effect_extra = after
        _bind(app, "_handle_wait_style_skip_confirm")
        st = ChatState(
            chat_id=1,
            stage=STAGE_WAIT_STYLE_SKIP_CONFIRM,
            style_skip_origin=public_app.STYLE_SKIP_ORIGIN_EFFECT_EXTRA,
            effect_transition="extract_flash",
            effect_extra="xerox",
            effect_extra_full=True,
        )
        await app._handle_wait_style_skip_confirm(_Message(public_app.BTN_CONFIRM), st)
        assert seen == {"next": 1}
        assert st.effect_transition == "extract_flash"
        assert st.effect_extra == ""
        assert st.effect_extra_full is False

    asyncio.run(run_photo())
    asyncio.run(run_effect())
