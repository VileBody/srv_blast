from __future__ import annotations

import asyncio
from typing import Any

import httpx

from services.tg_bot_public import orchestrator_client as public_client


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.url = ""

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        self.url = url
        self.payload = dict(json)
        return httpx.Response(200, json={"job_id": "job-1", "status": "QUEUED", "created": True})

    async def aclose(self) -> None:
        return None


def test_public_orchestrator_payload_does_not_send_source_bot(monkeypatch) -> None:
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)

    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    out = asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
        )
    )

    assert out["job_id"] == "job-1"
    assert fake.url == "http://orchestrator/send_audio_s3"
    assert fake.payload is not None
    assert "source_bot" not in fake.payload
    # hook_device is part of the F5 («Мысль») mirror; default is None.
    assert fake.payload.get("hook_device") is None


def test_public_orchestrator_payload_forwards_hook_device(monkeypatch) -> None:
    """F5 («Мысль») device must be mirrored into the public payload."""
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)

    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            hook_enabled=True,
            hook_device="punchline",
        )
    )

    assert fake.payload is not None
    assert fake.payload.get("hook_device") == "punchline"


def test_public_chat_state_has_hook_category_and_device_defaults() -> None:
    """Mirror parity: public ChatState carries the new hook fields."""
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.hook_category == ""
    assert st.hook_device == ""
