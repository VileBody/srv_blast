"""OpenRouter transport used by the F5 text and voice stages."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import time
from typing import Any

import httpx

from mlcore.hooks.f5_cognition._gemini import pcm_to_wav_bytes
from mlcore.hooks.f5_cognition.errors import F5OpenRouterError
from mlcore.hooks.f5_cognition.models import VoiceSpec


logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise F5OpenRouterError(f"{name} is required for the F5 OpenRouter provider")
    return value


def _timeout_s() -> float:
    raw = (
        os.environ.get("F5_OPENROUTER_TIMEOUT_S")
        or os.environ.get("OPENROUTER_TIMEOUT_S")
        or "120"
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise F5OpenRouterError(f"invalid F5 OpenRouter timeout: {raw!r}") from exc
    if value <= 0:
        raise F5OpenRouterError(f"F5 OpenRouter timeout must be positive: {value}")
    return value


def _base_url() -> str:
    return (
        os.environ.get("OPENROUTER_BASE_URL")
        or "https://openrouter.ai/api/v1"
    ).strip().rstrip("/")


def _client() -> httpx.Client:
    kwargs: dict[str, Any] = {
        "timeout": _timeout_s(),
        "follow_redirects": True,
    }
    proxy = (os.environ.get("OUTBOUND_PROXY") or "").strip()
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def _headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_required_env('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json",
    }
    referer = (os.environ.get("OPENROUTER_HTTP_REFERER") or "").strip()
    title = (os.environ.get("OPENROUTER_APP_TITLE") or "Blast F5").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


def _provider() -> dict[str, bool]:
    return {
        "allow_fallbacks": False,
        "require_parameters": True,
    }


def _http_error(response: httpx.Response) -> F5OpenRouterError:
    try:
        body = response.text[:2000]
    except Exception:  # noqa: BLE001
        body = "<unreadable>"
    return F5OpenRouterError(
        f"OpenRouter HTTP error status={response.status_code} body={body!r}"
    )


def _extract_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise F5OpenRouterError(
            "OpenRouter F5 text response has no message content"
        ) from exc
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text = "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict)
        ).strip()
        if text:
            return text
    raise F5OpenRouterError("OpenRouter F5 text response is empty")


def _is_expected_audio_limit_error(error: Any) -> bool:
    if not isinstance(error, dict):
        return False
    message = str(error.get("message") or "").lower()
    return "max_tokens" in message or "model output limit was reached" in message


def generate_voice_spec_text(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    seed: int | None,
) -> str:
    """Return the raw structured Stage 1 response from OpenRouter."""
    schema = VoiceSpec.model_json_schema()
    # expected_duration_ms is an estimate. The shared Stage 1 parser clamps it
    # before VoiceSpec validation, so the provider schema must not reject it first.
    duration_schema = schema.get("properties", {}).get("expected_duration_ms", {})
    duration_schema.pop("minimum", None)
    duration_schema.pop("maximum", None)

    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 1.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "F5VoiceSpec",
                "strict": True,
                "schema": schema,
            },
        },
        "provider": _provider(),
    }
    if seed is not None:
        request["seed"] = int(seed)

    logger.info("f5.openrouter.text request model=%s", model)
    try:
        with _client() as client:
            response = client.post(
                f"{_base_url()}/chat/completions",
                headers=_headers(),
                json=request,
            )
    except httpx.TimeoutException as exc:
        raise F5OpenRouterError(f"OpenRouter F5 text timeout: {exc!r}") from exc
    except httpx.TransportError as exc:
        raise F5OpenRouterError(f"OpenRouter F5 text transport error: {exc!r}") from exc

    if response.status_code >= 400:
        raise _http_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise F5OpenRouterError("OpenRouter F5 text returned invalid JSON") from exc
    return _extract_text(payload)


def synthesize_pcm16(
    prompt: str,
    *,
    model: str,
    voice: str,
    sample_rate: int,
    max_tokens: int = 96,
    max_stream_seconds: float = 6.0,
) -> bytes:
    """Stream OpenRouter PCM16 audio and return a mono WAV container."""
    if sample_rate != 24000:
        raise F5OpenRouterError(
            f"OpenRouter pcm16 F5 contract requires sample_rate=24000, got {sample_rate}"
        )
    if max_tokens <= 0:
        raise F5OpenRouterError(f"OpenRouter F5 max_tokens must be positive: {max_tokens}")
    if max_stream_seconds <= 0:
        raise F5OpenRouterError(
            "OpenRouter F5 max_stream_seconds must be positive: "
            f"{max_stream_seconds}"
        )
    max_pcm_bytes = int(sample_rate * 2 * max_stream_seconds)
    deadline = time.monotonic() + _timeout_s()
    request = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "pcm16"},
        "temperature": 0.2,
        "max_tokens": int(max_tokens),
        "stream": True,
        "provider": _provider(),
    }

    chunks: list[bytes] = []
    pcm_size = 0
    logger.info("f5.openrouter.tts request model=%s voice=%s", model, voice)
    try:
        with _client() as client:
            with client.stream(
                "POST",
                f"{_base_url()}/chat/completions",
                headers=_headers(),
                json=request,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise _http_error(response)
                for line in response.iter_lines():
                    if time.monotonic() > deadline:
                        raise F5OpenRouterError(
                            "OpenRouter F5 TTS exceeded the total stream timeout"
                        )
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise F5OpenRouterError(
                            "OpenRouter F5 TTS returned invalid SSE JSON"
                        ) from exc
                    if isinstance(event, dict) and event.get("error"):
                        if pcm_size > 0 and _is_expected_audio_limit_error(event["error"]):
                            logger.info(
                                "f5.openrouter.tts completed at configured token limit "
                                "pcm_bytes=%d",
                                pcm_size,
                            )
                            break
                        raise F5OpenRouterError(
                            f"OpenRouter F5 TTS stream error: {event['error']!r}"
                        )
                    try:
                        encoded = event["choices"][0]["delta"]["audio"]["data"]
                    except (KeyError, IndexError, TypeError):
                        continue
                    if not isinstance(encoded, str) or not encoded:
                        continue
                    try:
                        chunk = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise F5OpenRouterError(
                            "OpenRouter F5 TTS returned invalid base64 audio"
                        ) from exc
                    remaining = max_pcm_bytes - pcm_size
                    if remaining <= 0:
                        break
                    accepted = chunk[:remaining]
                    chunks.append(accepted)
                    pcm_size += len(accepted)
                    if len(chunk) >= remaining:
                        logger.warning(
                            "f5.openrouter.tts capped stream at %.3f seconds",
                            max_stream_seconds,
                        )
                        break
    except F5OpenRouterError:
        raise
    except httpx.TimeoutException as exc:
        raise F5OpenRouterError(f"OpenRouter F5 TTS timeout: {exc!r}") from exc
    except httpx.TransportError as exc:
        raise F5OpenRouterError(f"OpenRouter F5 TTS transport error: {exc!r}") from exc

    pcm = b"".join(chunks)
    if not pcm:
        raise F5OpenRouterError("OpenRouter F5 TTS stream returned no audio")
    return pcm_to_wav_bytes(pcm, rate=sample_rate, width=2, channels=1)
