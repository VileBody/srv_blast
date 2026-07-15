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
    assert fake.payload.get("render_engine") == "ae"


def test_public_orchestrator_payload_forwards_native_render_engine(monkeypatch) -> None:
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)
    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            render_engine="rust-gen",
        )
    )

    assert fake.payload is not None
    assert fake.payload["render_engine"] == "rust-gen"


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


def test_public_orchestrator_payload_defaults_bigtest_footage_fields(monkeypatch) -> None:
    """Schema parity: the bigtest footage-reuse fields exist in the public
    payload and default to off (public bot never enables /bigtest)."""
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
    assert fake.payload.get("reuse_stage2_footage") is False
    assert fake.payload.get("stage2_selection_seed_override") is None


def test_public_orchestrator_payload_forwards_bigtest_footage_fields(monkeypatch) -> None:
    """When set, the bigtest footage-reuse fields must reach the payload
    (mechanical mirror of tg_bot_botapi; values are coerced like the team bot)."""
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)

    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            reuse_stage2_footage=True,
            stage2_selection_seed_override="  bigtest-1-2-abc:v1  ",
        )
    )

    assert fake.payload is not None
    assert fake.payload.get("reuse_stage2_footage") is True
    assert fake.payload.get("stage2_selection_seed_override") == "bigtest-1-2-abc:v1"


def test_public_orchestrator_payload_defaults_f2_shape_and_f1_sound(monkeypatch) -> None:
    """Mirror parity: F2 («Объект») shape and F1 («Звук») url default to None
    in the public payload when the caller does not set them."""
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
    assert fake.payload.get("f2_shape") is None
    assert fake.payload.get("f1_sound_url") is None


def test_public_orchestrator_payload_forwards_f2_shape(monkeypatch) -> None:
    """F2 («Объект») shape must reach the public payload (stripped), mirroring
    tg_bot_botapi. Value is one of the schema Literal shapes."""
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)

    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            f2_shape="  rhomb  ",
            user_drop_t=12.5,
        )
    )

    assert fake.payload is not None
    assert fake.payload.get("f2_shape") == "rhomb"
    # F2 combo is anchored on the drop, so the drop must travel alongside it.
    assert fake.payload.get("user_drop_t") == 12.5


def test_public_orchestrator_payload_forwards_f1_sound_url(monkeypatch) -> None:
    """F1 («Звук») uploaded-sound url must reach the public payload (stripped)."""
    fake = _FakeAsyncClient()
    monkeypatch.setattr(public_client.httpx, "AsyncClient", lambda **_: fake)

    client = public_client.OrchestratorClient(base_url="http://orchestrator")

    asyncio.run(
        client.send_audio_s3(
            audio_s3_url="s3://bucket/audio.mp3",
            mode="with_gemini",
            lyrics_text="hello",
            target_fragment="hello",
            f1_sound_url="  https://s3/raw/sound.mp3  ",
            user_drop_t=8.0,
        )
    )

    assert fake.payload is not None
    assert fake.payload.get("f1_sound_url") == "https://s3/raw/sound.mp3"
    assert fake.payload.get("user_drop_t") == 8.0


def test_public_chat_state_has_hook_category_and_device_defaults() -> None:
    """Mirror parity: public ChatState carries the new hook fields."""
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.hook_category == ""
    assert st.hook_device == ""
    assert st.render_engine == ""
