from __future__ import annotations

import asyncio
import sys
import types

# Local test environments may not have full bot runtime deps installed.
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

from services.tg_bot_botapi import app as bot_app
from services.tg_bot_botapi.state_store import ChatState, STAGE_WAIT_FOOTAGE_ARTIST, STAGE_WAIT_FOOTAGE_GENRE


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


def _new_app() -> bot_app.BlastBotApp:
    app = object.__new__(bot_app.BlastBotApp)
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


def test_ask_footage_genre_shows_back_button(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setattr(bot_app, "_kb", lambda *rows: ("kb", rows))
        monkeypatch.setattr(
            bot_app,
            "get_genres",
            lambda: [{"key": "pop", "label": "Поп", "artists": []}],
        )
        app = _new_app()
        st = ChatState(chat_id=1001)
        msg = _FakeMessage()

        await bot_app.BlastBotApp._ask_footage_genre(app, msg, st)

        assert st.stage == STAGE_WAIT_FOOTAGE_GENRE
        assert msg.answers, "expected one prompt with genre keyboard"
        labels = _keyboard_labels(msg.answers[-1]["reply_markup"])
        assert bot_app.BTN_BACK in labels

    asyncio.run(_run())


def test_back_from_footage_genre_returns_to_timing_choice(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setattr(
            bot_app,
            "get_genres",
            lambda: [{"key": "pop", "label": "Поп", "artists": []}],
        )
        app = _new_app()
        st = ChatState(chat_id=1002, stage=STAGE_WAIT_FOOTAGE_GENRE)
        msg = _FakeMessage(text=bot_app.BTN_BACK)
        called = {"timing": 0}

        async def _ask_timing_choice(_message, _state) -> None:
            called["timing"] += 1

        app._ask_timing_choice = _ask_timing_choice  # type: ignore[method-assign]
        await bot_app.BlastBotApp._handle_wait_footage_genre(app, msg, st)

        assert called["timing"] == 1

    asyncio.run(_run())


def test_artist_keyboard_has_back_and_back_returns_to_genre(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setattr(bot_app, "_kb", lambda *rows: ("kb", rows))
        monkeypatch.setattr(
            bot_app,
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
            bot_app,
            "get_artists",
            lambda _genre_key: [{"key": "artist_a", "label": "Артист A", "description": ""}],
        )

        app = _new_app()
        st = ChatState(chat_id=1003, stage=STAGE_WAIT_FOOTAGE_GENRE)

        # Genre selection should open artist keyboard with a Back button.
        choose_genre_msg = _FakeMessage(text="Поп")
        await bot_app.BlastBotApp._handle_wait_footage_genre(app, choose_genre_msg, st)
        assert st.stage == STAGE_WAIT_FOOTAGE_ARTIST
        assert choose_genre_msg.answers, "expected artist selection prompt"
        labels = _keyboard_labels(choose_genre_msg.answers[0]["reply_markup"])
        assert bot_app.BTN_BACK in labels

        # Back on artist screen should return to genre screen.
        back_msg = _FakeMessage(text=bot_app.BTN_BACK)
        called = {"genre": 0}

        async def _ask_footage_genre(_message, _state) -> None:
            called["genre"] += 1

        app._ask_footage_genre = _ask_footage_genre  # type: ignore[method-assign]
        await bot_app.BlastBotApp._handle_wait_footage_artist(app, back_msg, st)
        assert called["genre"] == 1

    asyncio.run(_run())
