from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# Local test environments may miss runtime deps; only symbols are required at import time.
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


def _new_app(settings: SimpleNamespace) -> public_app.BlastBotApp:
    app = object.__new__(public_app.BlastBotApp)
    app.settings = settings
    return app


def test_send_result_video_with_retry_uses_timeout_and_retries(tmp_path: Path) -> None:
    class _FakeBot:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def send_document(self, **kwargs):
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                raise RuntimeError("transient send timeout")
            return None

    async def _run() -> None:
        app = _new_app(
            SimpleNamespace(
                tg_video_send_retries=2,
                tg_video_send_timeout_s=120.0,
                tg_video_send_backoff_base_s=0.0,
            )
        )
        bot = _FakeBot()
        video_path = tmp_path / "result.mp4"
        video_path.write_bytes(b"video")

        await public_app.BlastBotApp._send_result_video_with_retry(
            app,
            bot=bot,
            chat_id=123,
            job_id="job-1",
            video_path=video_path,
            caption="ready",
        )

        assert len(bot.calls) == 2
        assert int(bot.calls[0]["request_timeout"]) == 120
        assert int(bot.calls[1]["request_timeout"]) == 120

    asyncio.run(_run())


def test_prepare_result_video_for_tg_skips_compress_under_limit(tmp_path: Path) -> None:
    async def _run() -> None:
        app = _new_app(SimpleNamespace(bot_max_video_mb=2, tg_video_compress_enabled=True))
        source = tmp_path / "small.mp4"
        source.write_bytes(b"x" * (256 * 1024))

        async def _unexpected(**_kwargs):  # pragma: no cover - should not be called
            raise AssertionError("compress should not be called")

        app._compress_video_to_fit_tg = _unexpected
        resolved = await public_app.BlastBotApp._prepare_result_video_for_tg(
            app,
            source_path=source,
            chat_id=1,
            job_id="job-small",
        )
        assert resolved == source

    asyncio.run(_run())


def test_prepare_result_video_for_tg_compresses_when_over_limit(tmp_path: Path) -> None:
    async def _run() -> None:
        app = _new_app(SimpleNamespace(bot_max_video_mb=1, tg_video_compress_enabled=True))
        source = tmp_path / "large.mp4"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        calls: list[dict[str, object]] = []

        async def _fake_compress(*, source_path: Path, output_path: Path, max_bytes: int) -> None:
            calls.append(
                {
                    "source_path": source_path,
                    "output_path": output_path,
                    "max_bytes": max_bytes,
                }
            )
            output_path.write_bytes(b"y" * (512 * 1024))

        app._compress_video_to_fit_tg = _fake_compress

        resolved = await public_app.BlastBotApp._prepare_result_video_for_tg(
            app,
            source_path=source,
            chat_id=1,
            job_id="job-large",
        )
        assert resolved != source
        assert resolved.name.endswith(".tg.mp4")
        assert resolved.exists()
        assert calls and int(calls[0]["max_bytes"]) == 1024 * 1024

    asyncio.run(_run())


def test_prepare_result_video_for_tg_fails_when_over_limit_and_compress_disabled(tmp_path: Path) -> None:
    async def _run() -> None:
        app = _new_app(SimpleNamespace(bot_max_video_mb=1, tg_video_compress_enabled=False))
        source = tmp_path / "large.mp4"
        source.write_bytes(b"x" * (2 * 1024 * 1024))

        with pytest.raises(RuntimeError, match="compression is disabled"):
            await public_app.BlastBotApp._prepare_result_video_for_tg(
                app,
                source_path=source,
                chat_id=1,
                job_id="job-large",
            )

    asyncio.run(_run())
