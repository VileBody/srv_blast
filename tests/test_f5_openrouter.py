from __future__ import annotations

import base64
import io
import json
import wave

import pytest

import mlcore.hooks.f5_cognition._openrouter as f5_openrouter
import mlcore.hooks.f5_cognition.stage1_text as stage1
import mlcore.hooks.f5_cognition.stage2_audio as stage2
from mlcore.hooks.f5_cognition.errors import F5OpenRouterError
from mlcore.hooks.f5_cognition.models import F5Device, F5Request, VoiceSpec


def _voice_spec_payload() -> dict:
    return {
        "tts_text": "А ты точно готов к этому",
        "voice_persona": "young warm confident female voice",
        "voice_emotion": "hype",
        "voice_pacing": "normal",
        "expected_duration_ms": 3000,
        "rationale": "test",
    }


def _spec() -> VoiceSpec:
    return VoiceSpec.model_validate(_voice_spec_payload())


def _request() -> F5Request:
    return F5Request(
        track_path="/tmp/track.wav",
        lyrics="один два три четыре пять шесть",
        focal_start_ms=0,
        device=F5Device.PUNCHLINE,
    )


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _TextClient:
    def __init__(self, response: _Response, captured: dict):
        self.response = response
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, headers, json):
        self.captured.update(url=url, headers=headers, payload=json)
        return self.response


class _StreamResponse:
    def __init__(self, lines: list[str], *, status_code: int = 200):
        self.lines = lines
        self.status_code = status_code
        self.text = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self):
        yield from self.lines

    def read(self):
        return b""


class _StreamClient:
    def __init__(self, response: _StreamResponse, captured: dict):
        self.response = response
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url, *, headers, json):
        self.captured.update(method=method, url=url, headers=headers, payload=json)
        return self.response


def test_stage1_selects_openrouter_without_calling_gemini(monkeypatch):
    monkeypatch.setenv("F5_TEXT_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL_F5_TEXT", "example/text-model")
    captured = {}

    def fake_openrouter(system_prompt, user_prompt, *, model, seed):
        captured.update(model=model, seed=seed)
        return json.dumps(_voice_spec_payload(), ensure_ascii=False)

    monkeypatch.setattr(stage1, "_call_openrouter_text", fake_openrouter)
    monkeypatch.setattr(
        stage1,
        "_call_gemini_text",
        lambda *args, **kwargs: pytest.fail("Gemini must not be called"),
    )

    spec = stage1.run_stage1(_request())

    assert spec.tts_text == _voice_spec_payload()["tts_text"]
    assert captured == {"model": "example/text-model", "seed": None}


def test_stage1_openrouter_model_is_explicit(monkeypatch):
    monkeypatch.setenv("F5_TEXT_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_MODEL_F5_TEXT", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_MODEL_F5_TEXT is required"):
        stage1.run_stage1(_request())


def test_openrouter_text_request_disables_provider_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OUTBOUND_PROXY", raising=False)
    captured = {}
    response = _Response(
        {"choices": [{"message": {"content": json.dumps(_voice_spec_payload())}}]}
    )
    monkeypatch.setattr(
        f5_openrouter,
        "_client",
        lambda: _TextClient(response, captured),
    )

    raw = f5_openrouter.generate_voice_spec_text(
        "system",
        "user",
        model="example/text-model",
        seed=42,
    )

    assert json.loads(raw)["voice_emotion"] == "hype"
    assert captured["payload"]["provider"] == {
        "allow_fallbacks": False,
        "require_parameters": True,
    }
    assert captured["payload"]["seed"] == 42
    duration_schema = captured["payload"]["response_format"]["json_schema"][
        "schema"
    ]["properties"]["expected_duration_ms"]
    assert "minimum" not in duration_schema
    assert "maximum" not in duration_schema


def test_openrouter_tts_stream_is_wrapped_as_wav(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OUTBOUND_PROXY", raising=False)
    pcm_parts = [b"\x01\x02" * 120, b"\x03\x04" * 80]
    lines = []
    for pcm in pcm_parts:
        event = {
            "choices": [{
                "delta": {
                    "audio": {"data": base64.b64encode(pcm).decode("ascii")}
                }
            }]
        }
        lines.append(f"data: {json.dumps(event)}")
    lines.append("data: [DONE]")
    captured = {}
    monkeypatch.setattr(
        f5_openrouter,
        "_client",
        lambda: _StreamClient(_StreamResponse(lines), captured),
    )

    wav = f5_openrouter.synthesize_pcm16(
        "speak",
        model="openai/gpt-audio-mini",
        voice="alloy",
        sample_rate=24000,
    )

    with wave.open(io.BytesIO(wav), "rb") as reader:
        assert reader.getframerate() == 24000
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.readframes(reader.getnframes()) == b"".join(pcm_parts)
    assert captured["payload"]["audio"] == {"voice": "alloy", "format": "pcm16"}
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["max_tokens"] == 96
    assert captured["payload"]["provider"]["allow_fallbacks"] is False


def test_openrouter_tts_empty_stream_fails_explicitly(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        f5_openrouter,
        "_client",
        lambda: _StreamClient(_StreamResponse(["data: [DONE]"]), {}),
    )
    with pytest.raises(F5OpenRouterError, match="returned no audio"):
        f5_openrouter.synthesize_pcm16(
            "speak",
            model="openai/gpt-audio-mini",
            voice="alloy",
            sample_rate=24000,
        )


def test_openrouter_tts_stream_is_hard_capped(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    pcm = b"\x01\x02" * 1000
    event = {
        "choices": [{
            "delta": {"audio": {"data": base64.b64encode(pcm).decode("ascii")}}
        }]
    }
    lines = [f"data: {json.dumps(event)}", "data: [DONE]"]
    monkeypatch.setattr(
        f5_openrouter,
        "_client",
        lambda: _StreamClient(_StreamResponse(lines), {}),
    )

    wav = f5_openrouter.synthesize_pcm16(
        "speak",
        model="openai/gpt-audio-mini",
        voice="alloy",
        sample_rate=24000,
        max_stream_seconds=0.01,
    )

    with wave.open(io.BytesIO(wav), "rb") as reader:
        assert reader.getnframes() == 240


def test_openrouter_tts_accepts_token_limit_after_audio(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    pcm = b"\x01\x02" * 120
    audio_event = {
        "choices": [{
            "delta": {"audio": {"data": base64.b64encode(pcm).decode("ascii")}}
        }]
    }
    limit_event = {
        "error": {
            "code": 502,
            "message": "Could not finish because max_tokens or model output limit was reached",
        }
    }
    lines = [
        f"data: {json.dumps(audio_event)}",
        f"data: {json.dumps(limit_event)}",
    ]
    monkeypatch.setattr(
        f5_openrouter,
        "_client",
        lambda: _StreamClient(_StreamResponse(lines), {}),
    )

    wav = f5_openrouter.synthesize_pcm16(
        "speak",
        model="openai/gpt-audio-mini",
        voice="alloy",
        sample_rate=24000,
    )

    with wave.open(io.BytesIO(wav), "rb") as reader:
        assert reader.readframes(reader.getnframes()) == pcm


def test_openrouter_tts_rejects_unexpected_stream_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    error_event = {"error": {"code": 503, "message": "provider unavailable"}}
    monkeypatch.setattr(
        f5_openrouter,
        "_client",
        lambda: _StreamClient(
            _StreamResponse([f"data: {json.dumps(error_event)}"]),
            {},
        ),
    )

    with pytest.raises(F5OpenRouterError, match="provider unavailable"):
        f5_openrouter.synthesize_pcm16(
            "speak",
            model="openai/gpt-audio-mini",
            voice="alloy",
            sample_rate=24000,
        )


def test_stage2_selects_openrouter_without_calling_gemini(monkeypatch):
    monkeypatch.setenv("F5_TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL_F5_TTS", "openai/gpt-audio-mini")
    monkeypatch.setenv("OPENROUTER_F5_TTS_VOICE", "alloy")
    captured = {}

    def fake_synthesize(
        prompt,
        *,
        model,
        voice,
        sample_rate,
        max_tokens,
        max_stream_seconds,
    ):
        captured.update(
            model=model,
            voice=voice,
            sample_rate=sample_rate,
            max_tokens=max_tokens,
            max_stream_seconds=max_stream_seconds,
        )
        return b"WAV"

    monkeypatch.setattr(stage2, "synthesize_pcm16", fake_synthesize)
    monkeypatch.setattr(
        stage2,
        "_call_gemini_tts",
        lambda *args, **kwargs: pytest.fail("Gemini must not be called"),
    )
    monkeypatch.setattr(stage2, "_measure_duration_ms", lambda _: 3000)

    audio, duration = stage2.synthesize_voice(_spec())

    assert audio == b"WAV"
    assert duration == 3000
    assert captured == {
        "model": "openai/gpt-audio-mini",
        "voice": "alloy",
        "sample_rate": 24000,
        "max_tokens": 96,
        "max_stream_seconds": 6.0,
    }


def test_openrouter_http_failure_is_not_routed_to_gemini(monkeypatch):
    monkeypatch.setenv("F5_TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL_F5_TTS", "openai/gpt-audio-mini")
    monkeypatch.setattr(
        stage2,
        "synthesize_pcm16",
        lambda *args, **kwargs: (_ for _ in ()).throw(F5OpenRouterError("HTTP 503")),
    )
    monkeypatch.setattr(
        stage2,
        "_call_gemini_tts",
        lambda *args, **kwargs: pytest.fail("Gemini fallback is forbidden"),
    )

    with pytest.raises(F5OpenRouterError, match="HTTP 503"):
        stage2.synthesize_voice(_spec())
