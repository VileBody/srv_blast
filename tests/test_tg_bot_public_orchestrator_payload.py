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
