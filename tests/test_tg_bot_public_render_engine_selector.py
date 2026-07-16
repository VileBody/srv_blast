from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_RENDER_ENGINE,
)


class _Store:
    def __init__(self) -> None:
        self.saved: list[ChatState] = []

    async def set(self, st: ChatState) -> None:
        self.saved.append(st.model_copy(deep=True))


class _Credits:
    def __init__(self, *, paid: bool = False) -> None:
        self.paid = bool(paid)

    async def has_paid(self, _chat_id: int) -> bool:
        return self.paid


class _Message:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str = "", **_kwargs) -> None:
        self.answers.append(str(text))


def _new_app(*, rust_enabled: bool, rust_default: bool = False, paid: bool = False):
    app = object.__new__(public_app.BlastBotApp)
    app.store = _Store()
    app.credits_db = _Credits(paid=paid)
    app.settings = SimpleNamespace(
        rust_gen_enabled=rust_enabled,
        rust_gen_bot_default_enabled=rust_default,
    )
    return app


def test_render_engine_selector_is_visible_before_final_confirm() -> None:
    async def _run() -> None:
        app = _new_app(rust_enabled=True)
        st = ChatState(chat_id=7, bg_mode="footage", visuals_done=True)
        msg = _Message()

        await public_app.BlastBotApp._proceed_to_versions_or_confirm(app, msg, st)

        assert st.stage == STAGE_WAIT_RENDER_ENGINE
        assert "Выбери движок рендера" in msg.answers[-1]
        assert "AE" in msg.answers[-1]
        assert "Rust" in msg.answers[-1]

    asyncio.run(_run())


def test_render_engine_rust_choice_reaches_confirm_summary() -> None:
    async def _run() -> None:
        app = _new_app(rust_enabled=True)
        st = ChatState(
            chat_id=7,
            stage=STAGE_WAIT_RENDER_ENGINE,
            bg_mode="footage",
            visuals_done=True,
            lyrics_text="hello",
            target_fragment="hello",
        )
        msg = _Message(public_app.BTN_RENDER_RUST)

        await public_app.BlastBotApp._handle_wait_render_engine(app, msg, st)

        assert st.render_engine == "rust-gen"
        assert st.stage == STAGE_WAIT_CONFIRM
        assert "*Рендер:* «Rust»" in msg.answers[-1]

    asyncio.run(_run())


def test_render_engine_ae_choice_overrides_rust_default() -> None:
    app = _new_app(rust_enabled=True, rust_default=True)
    st = ChatState(chat_id=7, render_engine="ae")

    assert public_app.BlastBotApp._render_engine_for_state(app, st) == "ae"


def test_render_engine_selector_skips_to_ae_when_rust_disabled() -> None:
    async def _run() -> None:
        app = _new_app(rust_enabled=False)
        st = ChatState(chat_id=7, bg_mode="footage", visuals_done=True)
        msg = _Message()

        await public_app.BlastBotApp._proceed_to_versions_or_confirm(app, msg, st)

        assert st.render_engine == "ae"
        assert st.stage == STAGE_WAIT_CONFIRM
        assert "Запустить генерацию" in msg.answers[-1]
        assert "Выбери движок рендера" not in msg.answers[-1]

    asyncio.run(_run())
