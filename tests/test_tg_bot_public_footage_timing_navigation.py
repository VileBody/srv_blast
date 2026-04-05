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
from services.tg_bot_public.state_store import ChatState, STAGE_WAIT_FOOTAGE_ARTIST, STAGE_WAIT_FOOTAGE_GENRE, STAGE_WAIT_TIMING_CHOICE


class _FakeStore:
    async def set(self, _state: ChatState) -> None:
        return None


class _FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.answers: list[dict[str, object]] = []
        self.videos: list[dict[str, str]] = []

    async def answer(self, text: str, reply_markup=None, **_kwargs) -> None:
        self.answers.append({"text": text, "reply_markup": reply_markup})

    async def answer_video(self, *, video: str, caption: str = "", **_kwargs) -> None:
        self.videos.append({"video": video, "caption": caption})


def _new_app() -> public_app.BlastBotApp:
    app = object.__new__(public_app.BlastBotApp)
    app.store = _FakeStore()
    return app


def _keyboard_labels(reply_markup) -> list[str]:
    if isinstance(reply_markup, tuple) and len(reply_markup) == 2 and reply_markup[0] == "kb":
        rows = reply_markup[1]
        return [str(item) for row in rows for item in list(row)]

    labels: list[str] = []
    if reply_markup is None:
        return labels
    for row in list(getattr(reply_markup, "keyboard", []) or []):
        for btn in list(row or []):
            text = str(getattr(btn, "text", "")).strip()
            if text:
                labels.append(text)
    return labels


def test_timing_choice_keyboard_has_set_and_skip(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setattr(public_app, "_kb", lambda *rows: ("kb", rows))
        app = _new_app()
        st = ChatState(chat_id=2001)
        msg = _FakeMessage()

        await public_app.BlastBotApp._ask_timing_choice(app, msg, st)

        assert st.stage == STAGE_WAIT_TIMING_CHOICE
        labels = _keyboard_labels(msg.answers[-1]["reply_markup"])
        assert public_app.BTN_SET_TIMING in labels
        assert public_app.BTN_SKIP_TIMING in labels

    asyncio.run(_run())


def test_skip_timing_goes_to_footage_genre(monkeypatch) -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=2002, stage=STAGE_WAIT_TIMING_CHOICE)
        msg = _FakeMessage(text=public_app.BTN_SKIP_TIMING)
        called = {"genre": 0}

        async def _ask_footage_genre(_message, _state) -> None:
            called["genre"] += 1

        app._ask_footage_genre = _ask_footage_genre  # type: ignore[method-assign]
        await public_app.BlastBotApp._handle_wait_timing_choice(app, msg, st)
        assert called["genre"] == 1

    asyncio.run(_run())


def test_footage_flow_has_back_navigation(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setattr(public_app, "_kb", lambda *rows: ("kb", rows))
        monkeypatch.setattr(
            public_app,
            "get_genres",
            lambda: [
                {
                    "key": "pop",
                    "label": "Поп",
                    "artists": [{"key": "artist_a", "label": "Артист A", "description": ""}],
                }
            ],
        )
        monkeypatch.setattr(
            public_app,
            "get_artists",
            lambda _genre_key: [{"key": "artist_a", "label": "Артист A", "description": ""}],
        )

        app = _new_app()
        st = ChatState(chat_id=2003, stage=STAGE_WAIT_FOOTAGE_GENRE)

        # Genre screen contains Back button.
        ask_msg = _FakeMessage()
        await public_app.BlastBotApp._ask_footage_genre(app, ask_msg, st)
        labels = _keyboard_labels(ask_msg.answers[-1]["reply_markup"])
        assert public_app.BTN_BACK in labels

        # Back from genre -> timing choice.
        back_genre_msg = _FakeMessage(text=public_app.BTN_BACK)
        called = {"timing": 0}

        async def _ask_timing_choice(_message, _state) -> None:
            called["timing"] += 1

        app._ask_timing_choice = _ask_timing_choice  # type: ignore[method-assign]
        await public_app.BlastBotApp._handle_wait_footage_genre(app, back_genre_msg, st)
        assert called["timing"] == 1

        # Select genre -> artist screen with Back.
        st2 = ChatState(chat_id=2004, stage=STAGE_WAIT_FOOTAGE_GENRE)
        choose_genre_msg = _FakeMessage(text="Поп")
        await public_app.BlastBotApp._handle_wait_footage_genre(app, choose_genre_msg, st2)
        assert st2.stage == STAGE_WAIT_FOOTAGE_ARTIST
        labels2 = _keyboard_labels(choose_genre_msg.answers[0]["reply_markup"])
        assert public_app.BTN_BACK in labels2

        # Back from artist -> genre screen.
        back_artist_msg = _FakeMessage(text=public_app.BTN_BACK)
        called2 = {"genre": 0}

        async def _ask_footage_genre(_message, _state) -> None:
            called2["genre"] += 1

        app._ask_footage_genre = _ask_footage_genre  # type: ignore[method-assign]
        await public_app.BlastBotApp._handle_wait_footage_artist(app, back_artist_msg, st2)
        assert called2["genre"] == 1

    asyncio.run(_run())
