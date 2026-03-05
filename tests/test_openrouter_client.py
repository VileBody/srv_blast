from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict

import httpx

from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.openrouter_client import OpenRouterClient, OpenRouterSettings


def _mk_client(captured: Dict[str, Any]) -> OpenRouterClient:
    def _request(url: str, *, headers: Dict[str, str], json: Dict[str, Any], timeout: float) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"transcript_words":[{"text":"a","t_start":0.0,"t_end":0.5}],'
                            '"srt_items":[]}'
                        )
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    return OpenRouterClient(
        OpenRouterSettings(
            api_key="k",
            model="google/gemini-2.5-pro",
            temperature=0.1,
            timeout_s=33.0,
        ),
        request_func=_request,
    )


def test_openrouter_payload_includes_provider_and_response_format(tmp_path: Path) -> None:
    captured: Dict[str, Any] = {}
    client = _mk_client(captured)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"\x01\x02test-audio")

    out = client.generate_structured(
        schema_model=Stage1AsrPayload,
        prompt="prompt",
        system_instruction="sys",
        audio_paths=[audio],
    )
    assert isinstance(out, Stage1AsrPayload)

    payload = captured["json"]
    assert payload["provider"]["allow_fallbacks"] is False
    assert payload["provider"]["require_parameters"] is True
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "Stage1AsrPayload"


def test_openrouter_asr_audio_is_input_audio_base64(tmp_path: Path) -> None:
    captured: Dict[str, Any] = {}
    client = _mk_client(captured)
    audio = tmp_path / "track.mp3"
    source = b"hello-audio"
    audio.write_bytes(source)

    client.generate_structured(
        schema_model=Stage1AsrPayload,
        prompt="prompt",
        system_instruction="sys",
        audio_paths=[audio],
    )

    messages = captured["json"]["messages"]
    user = next(m for m in messages if m.get("role") == "user")
    parts = user["content"]
    audio_parts = [p for p in parts if p.get("type") == "input_audio"]
    assert len(audio_parts) == 1
    input_audio = audio_parts[0]["input_audio"]
    assert input_audio["format"] == "mp3"
    decoded = base64.b64decode(input_audio["data"])
    assert decoded == source
