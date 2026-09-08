from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

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
    STAGE_WAIT_CONFIRM_TEXT,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
    STAGE_WAIT_TIMING_INPUT,
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
    app.settings = SimpleNamespace(public_stage1_alignment_backend="local_ctc")
    return app


def test_legacy_lyrics_stage_restarts_at_timing() -> None:
    """The full-lyrics step is gone. Sessions parked on it when the new flow
    shipped must land on the first step of the current flow, not on a stage no
    handler serves any more."""

    async def _run() -> None:
        for stage in (STAGE_WAIT_LYRICS_CHOICE, STAGE_WAIT_LYRICS_TEXT):
            app = _new_app()
            st = ChatState(chat_id=1001, stage=stage, lyrics_text="старый текст")
            msg = _FakeMessage(text="я пришел сюда чтобы сиять")

            await public_app.BlastBotApp._handle_legacy_lyrics_stage(app, msg, st)

            assert st.stage == STAGE_WAIT_TIMING_INPUT
            assert "Шаги обновились" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_launch_without_timing_returns_to_timing_first() -> None:
    """Empty state falls back to the window, not the lines: that is the order
    the flow asks them in."""

    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1003, stage=STAGE_WAIT_CONFIRM, lyrics_text="", target_fragment="")
        msg = _FakeMessage(text=public_app.BTN_LAUNCH, chat_id=1003)

        await public_app.BlastBotApp._handle_wait_confirm(app, msg, st)

        assert st.stage == STAGE_WAIT_TIMING_INPUT
        assert "укажи тайминг" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_launch_without_target_fragment_returns_to_fragment_before_credit_check() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1004,
            stage=STAGE_WAIT_CONFIRM,
            lyrics_text="полный текст песни",
            target_fragment="",
            user_clip_start_sec=10.0,
            user_clip_end_sec=20.0,
        )
        msg = _FakeMessage(text=public_app.BTN_LAUNCH, chat_id=1004)

        await public_app.BlastBotApp._handle_wait_confirm(app, msg, st)

        assert st.stage == STAGE_WAIT_FRAGMENT_TEXT
        assert "пришли строки" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_launch_without_timing_returns_to_timing_before_credit_check() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1005,
            stage=STAGE_WAIT_CONFIRM,
            lyrics_text="полный текст песни",
            target_fragment="точные строки",
            target_fragment_explicit=True,
            user_clip_start_sec=0.0,
            user_clip_end_sec=0.0,
        )
        msg = _FakeMessage(text=public_app.BTN_LAUNCH, chat_id=1005)

        await public_app.BlastBotApp._handle_wait_confirm(app, msg, st)

        assert st.stage == STAGE_WAIT_TIMING_INPUT
        assert "укажи тайминг" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_launch_with_legacy_fragment_requests_exact_lines_before_credit_check() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1006,
            stage=STAGE_WAIT_CONFIRM,
            lyrics_text="полный старый текст",
            target_fragment="полный старый текст",
            target_fragment_explicit=False,
            user_clip_start_sec=44.0,
            user_clip_end_sec=62.0,
        )
        msg = _FakeMessage(text=public_app.BTN_LAUNCH, chat_id=1006)

        await public_app.BlastBotApp._handle_wait_confirm(app, msg, st)

        assert st.stage == STAGE_WAIT_FRAGMENT_TEXT
        assert st.target_fragment == ""
        assert st.pending_audio_file_id == ""
        assert "точные строки" in str(msg.answers[-1]["text"])
        assert "0:44-1:02" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_timing_step_leads_to_the_lines_step() -> None:
    """New order: the window is picked first, then the lines that sound in it —
    so the prompt can name the window instead of promising it later."""

    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1007, stage=STAGE_WAIT_TIMING_INPUT)
        msg = _FakeMessage(text="1:20.5-1:33.2", chat_id=1007)

        async def _noop(*_args, **_kwargs) -> None:
            return None

        app._trigger_hook_analysis_task = _noop  # type: ignore[method-assign]

        await public_app.BlastBotApp._handle_wait_timing_input(app, msg, st)

        assert st.stage == STAGE_WAIT_FRAGMENT_TEXT
        assert st.user_clip_start_sec == 80.5
        assert st.user_clip_end_sec == 93.2
        assert "1:20-1:33" in str(msg.answers[-1]["text"])

    asyncio.run(_run())


def test_lines_step_fills_lyrics_text_and_confirms_window() -> None:
    """The removed lyrics step used to feed lyrics_text (aligner reference and
    footage-ranker input). The lines now carry it, or those readers go empty."""

    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1008,
            stage=STAGE_WAIT_FRAGMENT_TEXT,
            user_clip_start_sec=80.0,
            user_clip_end_sec=92.0,
        )
        msg = _FakeMessage(text="я пришел сюда чтобы сиять", chat_id=1008)
        ranked = {"n": 0}

        async def _rank(_state) -> None:
            ranked["n"] += 1

        app._trigger_vibe_ranker_task = _rank  # type: ignore[method-assign]

        await public_app.BlastBotApp._handle_wait_fragment_text(app, msg, st)

        assert st.stage == STAGE_WAIT_CONFIRM_TEXT
        assert st.target_fragment == "я пришел сюда чтобы сиять"
        assert st.target_fragment_explicit is True
        assert st.lyrics_text == "я пришел сюда чтобы сиять"
        assert ranked["n"] == 1
        text = str(msg.answers[-1]["text"])
        assert "1:20-1:32" in text
        assert "Текст песни" not in text

    asyncio.run(_run())


def test_confirmed_lines_go_straight_to_the_background_step() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1009, stage=STAGE_WAIT_CONFIRM_TEXT)
        msg = _FakeMessage(text=public_app.BTN_CONFIRM_YES, chat_id=1009)
        called = {"bg": 0}

        async def _ask_bg_mode(_message, _state) -> None:
            called["bg"] += 1

        app._ask_bg_mode = _ask_bg_mode  # type: ignore[method-assign]

        await public_app.BlastBotApp._handle_wait_confirm_text(app, msg, st)

        assert called["bg"] == 1

    asyncio.run(_run())
