from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services.tg_bot_botapi.app import (
    BTN_ALIGN_LOCAL,
    BlastBotApp,
)
from services.tg_bot_botapi.state_store import (
    ChatState,
    STAGE_WAIT_ALIGNMENT_BACKEND,
    STAGE_WAIT_BG_MODE,
)


class _Store:
    def __init__(self):
        self.saved: list[ChatState] = []

    async def set(self, state: ChatState) -> None:
        self.saved.append(state.model_copy(deep=True))


class _Message:
    def __init__(self, text: str = ""):
        self.text = text
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(message_id=1)


def _app(*, enabled: bool) -> BlastBotApp:
    app = object.__new__(BlastBotApp)
    app.settings = SimpleNamespace(team_local_alignment_enabled=enabled)
    app.store = _Store()
    return app


def test_selector_is_shown_only_for_fragment_and_timing() -> None:
    app = _app(enabled=True)
    state = ChatState(
        chat_id=1,
        target_fragment="точный текст",
        user_clip_start_sec=10.0,
        user_clip_end_sec=20.0,
    )
    message = _Message()

    asyncio.run(app._ask_alignment_backend(message, state))

    assert state.stage == STAGE_WAIT_ALIGNMENT_BACKEND
    assert state.stage1_alignment_backend == "gemini"
    assert "Тайминг текста:" in message.answers[-1][0]


def test_disabled_selector_forces_gemini() -> None:
    app = _app(enabled=False)
    state = ChatState(
        chat_id=1,
        target_fragment="точный текст",
        user_clip_start_sec=10.0,
        user_clip_end_sec=20.0,
        stage1_alignment_backend="local_ctc",
    )

    asyncio.run(app._ask_alignment_backend(_Message(), state))

    assert state.stage == STAGE_WAIT_BG_MODE
    assert state.stage1_alignment_backend == "gemini"


def test_local_button_sets_explicit_backend() -> None:
    app = _app(enabled=True)
    state = ChatState(
        chat_id=1,
        stage=STAGE_WAIT_ALIGNMENT_BACKEND,
        target_fragment="точный текст",
        user_clip_start_sec=10.0,
        user_clip_end_sec=20.0,
    )

    asyncio.run(app._handle_wait_alignment_backend(_Message(BTN_ALIGN_LOCAL), state))

    assert state.stage == STAGE_WAIT_BG_MODE
    assert state.stage1_alignment_backend == "local_ctc"
