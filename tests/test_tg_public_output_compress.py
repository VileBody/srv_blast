from __future__ import annotations

import asyncio
from pathlib import Path
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

from services.tg_bot_public.app import BlastBotApp


def _settings(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        tg_output_compress_enabled=enabled,
        tg_output_compress_crf=24,
        tg_output_compress_preset="veryfast",
        ffmpeg_bin="ffmpeg",
    )


def test_choose_video_for_delivery_prefers_smaller_compressed_file(tmp_path: Path) -> None:
    original = tmp_path / "orig.mp4"
    original.write_bytes(b"a" * 1000)
    compressed = original.with_name("orig.tg.mp4")

    def _fake_compress(*, source: Path, destination: Path) -> None:
        assert source == original
        destination.write_bytes(b"b" * 200)

    app = SimpleNamespace(settings=_settings(enabled=True), _compress_video_for_telegram=_fake_compress)
    picked = asyncio.run(BlastBotApp._choose_video_for_delivery(app, original_video=original))
    assert picked == compressed


def test_choose_video_for_delivery_keeps_original_when_compressed_not_smaller(tmp_path: Path) -> None:
    original = tmp_path / "orig.mp4"
    original.write_bytes(b"a" * 1000)

    def _fake_compress(*, source: Path, destination: Path) -> None:
        destination.write_bytes(b"b" * 1400)

    app = SimpleNamespace(settings=_settings(enabled=True), _compress_video_for_telegram=_fake_compress)
    picked = asyncio.run(BlastBotApp._choose_video_for_delivery(app, original_video=original))
    assert picked == original


def test_choose_video_for_delivery_keeps_original_on_compress_error(tmp_path: Path) -> None:
    original = tmp_path / "orig.mp4"
    original.write_bytes(b"a" * 1000)

    def _fake_compress(*, source: Path, destination: Path) -> None:
        raise RuntimeError("boom")

    app = SimpleNamespace(settings=_settings(enabled=True), _compress_video_for_telegram=_fake_compress)
    picked = asyncio.run(BlastBotApp._choose_video_for_delivery(app, original_video=original))
    assert picked == original
