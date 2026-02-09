from __future__ import annotations

import urllib.error

from services.orchestrator.tasks import (
    _is_transient_windows_error,
    _looks_like_gemini_overloaded_503,
    _looks_like_gemini_rate_limited_429,
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


def test_transient_windows_error_urlerror() -> None:
    e = urllib.error.URLError("broken pipe")
    assert _is_transient_windows_error(e) is True

