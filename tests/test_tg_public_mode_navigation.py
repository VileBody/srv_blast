from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")

    class _DummyConnection: ...
    class _DummyPool: ...

    async def _dummy_create_pool(*args, **kwargs):
        raise RuntimeError("stub asyncpg.create_pool")

    asyncpg_stub.Connection = _DummyConnection
    asyncpg_stub.Pool = _DummyPool
    asyncpg_stub.create_pool = _dummy_create_pool
    sys.modules["asyncpg"] = asyncpg_stub

if "aiogram" not in sys.modules:
    aiogram_stub = types.ModuleType("aiogram")
    aiogram_ex = types.ModuleType("aiogram.exceptions")
    aiogram_filters = types.ModuleType("aiogram.filters")
    aiogram_types = types.ModuleType("aiogram.types")

    class _Dummy:
        def __init__(self, *args, **kwargs): ...

    class _DummyRouter:
        def message(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator

    class _DummyDispatcher(_Dummy):
        def __init__(self, *args, **kwargs):
            self.startup = SimpleNamespace(register=lambda fn: None)
            self.shutdown = SimpleNamespace(register=lambda fn: None)

        def include_router(self, router): ...

    aiogram_stub.Bot = _Dummy
    aiogram_stub.Dispatcher = _DummyDispatcher
    aiogram_stub.Router = _DummyRouter
    aiogram_ex.TelegramBadRequest = Exception
    aiogram_filters.CommandStart = _Dummy
    aiogram_filters.Command = _Dummy
    aiogram_types.FSInputFile = _Dummy
    aiogram_types.KeyboardButton = _Dummy
    aiogram_types.Message = _Dummy
    aiogram_types.ReplyKeyboardMarkup = _Dummy
    aiogram_types.ReplyKeyboardRemove = _Dummy
    sys.modules["aiogram"] = aiogram_stub
    sys.modules["aiogram.exceptions"] = aiogram_ex
    sys.modules["aiogram.filters"] = aiogram_filters
    sys.modules["aiogram.types"] = aiogram_types

from core.subtitles_mode import SUBTITLES_MODE_IMPULSE_2ND
from services.tg_bot_public.app import (
    BTN_SUB_MODE_BACK,
    BTN_SUB_MODE_IMPULSE,
    BlastBotApp,
)
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_CONFIRM_MODE,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_SUBTITLES_MODE,
)


class _FakeStore:
    async def set(self, state: ChatState) -> None:
        self.last_state = state


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


def test_subtitles_mode_back_returns_to_lyrics_choice() -> None:
    app = SimpleNamespace(store=_FakeStore())
    st = ChatState(
        chat_id=100,
        stage=STAGE_WAIT_SUBTITLES_MODE,
        lyrics_text="line 1",
        target_fragment="line 2",
    )
    msg = _FakeMessage(BTN_SUB_MODE_BACK)

    asyncio.run(BlastBotApp._handle_wait_subtitles_mode(app, msg, st))

    assert st.stage == STAGE_WAIT_LYRICS_CHOICE
    assert st.lyrics_text == ""
    assert st.target_fragment == ""
    assert msg.answers
    assert "Хочешь прислать текст песни" in msg.answers[-1][0]


def test_subtitles_mode_choice_goes_to_confirm() -> None:
    app = SimpleNamespace(store=_FakeStore())
    st = ChatState(chat_id=101, stage=STAGE_WAIT_SUBTITLES_MODE)
    msg = _FakeMessage(BTN_SUB_MODE_IMPULSE)

    asyncio.run(BlastBotApp._handle_wait_subtitles_mode(app, msg, st))

    assert st.stage == STAGE_WAIT_CONFIRM_MODE
    assert st.subtitles_mode == SUBTITLES_MODE_IMPULSE_2ND
    assert msg.answers
    assert "Подтвердить режим субтитров?" in msg.answers[-1][0]
