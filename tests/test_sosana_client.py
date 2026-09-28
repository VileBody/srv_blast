from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict

import httpx
from pydantic import create_model
import pytest

from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.sosana_client import SosanaClient, SosanaSettings


def _client(captured: Dict[str, Any]) -> SosanaClient:
    def _request(
        url: str,
        *,
        headers: Dict[str, str],
        json: Dict[str, Any],
        timeout: float,
    ) -> httpx.Response:
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"transcript_words":[{"text":"a","t_start":0.0,'
                                '"t_end":0.5}],"srt_items":[]}'
                            )
                        }
                    }
                ]
            },
        )

    return SosanaClient(
        SosanaSettings(api_key="secret", model="gemini-pro", timeout_s=42),
        request_func=_request,
    )


def test_sosana_uses_its_chat_endpoint_without_openrouter_provider_payload(
    tmp_path: Path,
) -> None:
    captured: Dict[str, Any] = {}
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"test-audio")

    result = _client(captured).generate_structured(
        schema_model=Stage1AsrPayload,
        prompt="prompt",
        system_instruction="system",
        audio_paths=[audio],
    )

    assert isinstance(result, Stage1AsrPayload)
    assert captured["url"] == "https://api.sosana.art/api/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "gemini-pro"
    assert "provider" not in captured["json"]
    assert captured["json"]["response_format"]["type"] == "json_schema"

    user = next(row for row in captured["json"]["messages"] if row["role"] == "user")
    audio_part = next(part for part in user["content"] if part["type"] == "input_audio")
    assert base64.b64decode(audio_part["input_audio"]["data"]) == b"test-audio"


def test_sosana_errors_use_provider_specific_prefix() -> None:
    def _request(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = SosanaClient(
        SosanaSettings(api_key="secret", model="gemini-flash"),
        request_func=_request,
    )

    try:
        client.generate_structured(
            schema_model=Stage1AsrPayload,
            prompt="prompt",
        )
        assert False, "expected Sosana HTTP error"
    except RuntimeError as exc:
        assert "sosana_http_error status=503" in str(exc)


def test_sosana_ignores_environment_proxy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    def _request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update(method=method, url=url, **kwargs)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok":true}'}}]},
        )

    monkeypatch.setattr("mlcore.sosana_client.httpx.request", _request)
    schema = create_model("Probe", ok=(bool, ...))

    result = SosanaClient(
        SosanaSettings(api_key="secret", model="gemini-flash")
    ).generate_structured(schema_model=schema, prompt="prompt")

    assert result.ok is True
    assert captured["method"] == "POST"
    assert captured["trust_env"] is False


def test_sosana_can_explicitly_use_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    def _post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok":true}'}}]},
        )

    monkeypatch.setattr("mlcore.sosana_client.httpx.post", _post)
    schema = create_model("ProxyProbe", ok=(bool, ...))

    result = SosanaClient(
        SosanaSettings(api_key="secret", model="gemini-flash", trust_env=True)
    ).generate_structured(schema_model=schema, prompt="prompt")

    assert result.ok is True
    assert "trust_env" not in captured
