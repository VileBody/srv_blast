from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import httpx

# state_store needs redis at import time; stub it for unit tests.
if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")

    class _RedisStub:  # pragma: no cover
        pass

    redis_asyncio.Redis = _RedisStub
    redis_module.asyncio = redis_asyncio
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio

from services.tg_bot_public import orchestrator_client as public_client
from services.tg_bot_public.state_store import (
    STAGE_WAIT_BG_COLOR,
    STAGE_WAIT_BG_MODE,
    ChatState,
)


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        self.payload = dict(json)
        return httpx.Response(200, json={"job_id": "job-1", "status": "QUEUED", "created": True})

    async def aclose(self) -> None:
        return None


def test_public_chat_state_has_bg_mode_fields_default_footage() -> None:
    st = ChatState(chat_id=1)
    assert st.bg_mode == "footage"
    assert st.bg_solid_color == ""


def test_public_state_store_exposes_bg_stages() -> None:
    assert STAGE_WAIT_BG_MODE == "WAIT_BG_MODE"
    assert STAGE_WAIT_BG_COLOR == "WAIT_BG_COLOR"


def test_public_orchestrator_payload_forwards_bg_mode(monkeypatch) -> None:
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)
    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            bg_mode="solid",
            bg_solid_color="green",
        )
    )

    assert fake.payload is not None
    assert fake.payload["bg_mode"] == "solid"
    assert fake.payload["bg_solid_color"] == "green"


def test_public_orchestrator_payload_supports_black(monkeypatch) -> None:
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)
    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            bg_mode="solid",
            bg_solid_color="black",
        )
    )

    assert fake.payload is not None
    assert fake.payload["bg_mode"] == "solid"
    assert fake.payload["bg_solid_color"] == "black"


def test_public_orchestrator_payload_defaults_bg_mode_footage(monkeypatch) -> None:
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)
    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
        )
    )

    assert fake.payload is not None
    assert fake.payload["bg_mode"] == "footage"
    assert fake.payload["bg_solid_color"] == ""
