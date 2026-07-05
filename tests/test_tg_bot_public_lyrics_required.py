from __future__ import annotations

import asyncio
import sys
import types

# Local test environment may not have runtime deps available.
if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object  # type: ignore[attr-defined]
    sys.modules["asyncpg"] = asyncpg_stub

if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")

    class _RedisStub:  # pragma: no cover - import-time compatibility shim
        pass

    redis_asyncio.Redis = _RedisStub  # type: ignore[attr-defined]
    redis_module.asyncio = redis_asyncio
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
)


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list[ChatState] = []

    async def set(self, state: ChatState) -> None:
        self.saved.append(state.model_copy(deep=True))


class _FakeMessage:
    def __init__(self, text: str = "", *, chat_id: int = 1001) -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.from_user = types.SimpleNamespace(id=chat_id)
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **_kwargs) -> None:
        self.answers.append({"text": text, "reply_markup": reply_markup})


class _FailIfCalledCredits:
    async def get_balance(self, _user_id: int) -> int:
        raise AssertionError("credits should not be checked before lyrics are provided")


def _new_app() -> public_app.BlastBotApp:
    app = object.__new__(public_app.BlastBotApp)
    app.store = _FakeStore()
    app.credits_db = _FailIfCalledCredits()
    return app


def test_legacy_skip_lyrics_button_no_longer_advances_to_timing() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1001, stage=STAGE_WAIT_LYRICS_CHOICE)
        msg = _FakeMessage(text=public_app.BTN_SKIP_LYRICS)
        called = {"timing": 0}

        async def _ask_timing_choice(_message, _state) -> None:
            called["timing"] += 1

        app._ask_timing_choice = _ask_timing_choice  # type: ignore[method-assign]

        await public_app.BlastBotApp._handle_wait_lyrics_choice(app, msg, st)

        assert called["timing"] == 0
        assert st.stage == STAGE_WAIT_LYRICS_TEXT
        assert "текстом песни" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_legacy_lyrics_choice_accepts_plain_lyrics_text() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1002, stage=STAGE_WAIT_LYRICS_CHOICE)
        msg = _FakeMessage(text="я пришел сюда чтобы сиять")

        await public_app.BlastBotApp._handle_wait_lyrics_choice(app, msg, st)

        assert st.lyrics_text == "я пришел сюда чтобы сиять"
        # The fragment fork was removed — lyrics lead straight to the
        # "paste the lines" input step.
        assert st.stage == STAGE_WAIT_FRAGMENT_TEXT

    asyncio.run(_run())


def test_launch_without_reference_text_returns_to_lyrics_input_before_credit_check() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1003, stage=STAGE_WAIT_CONFIRM, lyrics_text="", target_fragment="")
        msg = _FakeMessage(text=public_app.BTN_LAUNCH, chat_id=1003)

        await public_app.BlastBotApp._handle_wait_confirm(app, msg, st)

        assert st.stage == STAGE_WAIT_LYRICS_TEXT
        assert "нужен текст песни" in str(msg.answers[-1]["text"])

    asyncio.run(_run())
