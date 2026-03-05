from __future__ import annotations

import urllib.error

from services.orchestrator.tasks import (
    _is_transient_windows_error,
    _looks_like_build_preflight_validation_error,
    _looks_like_gemini_overloaded_503,
    _looks_like_gemini_rate_limited_429,
    _looks_like_llm_schema_validation_error,
    _looks_like_openrouter_overloaded_503,
    _looks_like_openrouter_rate_limited_429,
    _looks_like_openrouter_timeout,
)


def test_detects_gemini_503_overload() -> None:
    s = (
        "google.genai.errors.ServerError: 503 UNAVAILABLE. "
        "{'error': {'code': 503, 'message': 'This model is currently experiencing high demand.'}}"
    )
    assert _looks_like_gemini_overloaded_503(s) is True


def test_detects_gemini_429_rate_limit() -> None:
    s = (
        "google.genai.errors.ClientError: 429 RESOURCE_EXHAUSTED. "
        "{'error': {'code': 429, 'message': 'Too Many Requests'}}"
    )
    assert _looks_like_gemini_rate_limited_429(s) is True


def test_detects_openrouter_503_overload() -> None:
    s = "RuntimeError: openrouter_http_error status=503 body='Service Unavailable'"
    assert _looks_like_openrouter_overloaded_503(s) is True


def test_detects_openrouter_429_rate_limit() -> None:
    s = "RuntimeError: openrouter_http_error status=429 body='Too Many Requests'"
    assert _looks_like_openrouter_rate_limited_429(s) is True


def test_detects_openrouter_timeout() -> None:
    s = "RuntimeError: openrouter_timeout: ReadTimeout('timed out')"
    assert _looks_like_openrouter_timeout(s) is True


def test_detects_llm_schema_validation_error_from_openrouter_marker() -> None:
    s = "RuntimeError: openrouter_schema_validation_failed err=ValidationError(...)"
    assert _looks_like_llm_schema_validation_error(s) is True


def test_detects_llm_schema_validation_error_from_hedged_summary() -> None:
    s = "RuntimeError: llm_hedged_all_failed stage=stage1_asr errors=[gemini:ValidationError(...)]"
    assert _looks_like_llm_schema_validation_error(s) is True


def test_detects_llm_schema_validation_error_from_stage1_marker() -> None:
    s = "RuntimeError: Stage1 scenario validation failed: ValidationError(...)"
    assert _looks_like_llm_schema_validation_error(s) is True


def test_detects_llm_schema_validation_error_from_stage2_marker() -> None:
    s = (
        "RuntimeError: Stage2 failed: "
        "stage2_subtitles=ValueError: subtitles.clip.start must equal stage1.audio.clip_start_abs"
    )
    assert _looks_like_llm_schema_validation_error(s) is True


def test_detects_build_preflight_validation_error() -> None:
    s = "ValueError: Preflight: out<=in in layer 'Adjustment Layer 10': 15.01..5.6"
    assert _looks_like_build_preflight_validation_error(s) is True


def test_transient_windows_error_urlerror() -> None:
    e = urllib.error.URLError("broken pipe")
    assert _is_transient_windows_error(e) is True
