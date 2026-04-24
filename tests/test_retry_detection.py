from __future__ import annotations

import urllib.error

from services.orchestrator.tasks import (
    _extract_preflight_out_le_in_issue,
    _is_transient_windows_error,
    _looks_like_build_preflight_validation_error,
    _looks_like_gemini_internal_500,
    _looks_like_gemini_overloaded_503,
    _looks_like_gemini_rate_limited_429,
    _looks_like_gemini_transport_disconnect,
    _looks_like_llm_schema_validation_error,
    _looks_like_openrouter_bad_request_400,
    _looks_like_openrouter_gateway_timeout_524,
    _looks_like_openrouter_internal_500,
    _looks_like_openrouter_provider_unavailable_502,
    _looks_like_openrouter_overloaded_503,
    _looks_like_openrouter_rate_limited_429,
    _looks_like_stage1a_selected_fragment_missing,
    _looks_like_openrouter_timeout,
    _overloaded_retry_backoff_s,
    _provider_mode_for_worker_type,
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


def test_detects_gemini_transport_disconnect_from_google_genai_traceback() -> None:
    s = (
        "File \"/app/mlcore/gemini_client.py\", line 543, in _generate_content_with_optional_fallback\n"
        "return self._client.models.generate_content(model=self._model, contents=contents, config=config)\n"
        "File \"/usr/local/lib/python3.13/site-packages/google/genai/_api_client.py\", line 1308\n"
        "response = self._httpx_client.send(httpx_request, stream=stream)\n"
        "httpx.RemoteProtocolError: Server disconnected without sending a response."
    )
    assert _looks_like_gemini_transport_disconnect(s) is True


def test_gemini_transport_disconnect_detector_does_not_match_plain_openrouter_text() -> None:
    s = "RuntimeError: openrouter_transport_error: RemoteProtocolError('Server disconnected without sending a response.')"
    assert _looks_like_gemini_transport_disconnect(s) is False


def test_detects_openrouter_503_overload() -> None:
    s = "RuntimeError: openrouter_http_error status=503 body='Service Unavailable'"
    assert _looks_like_openrouter_overloaded_503(s) is True


def test_detects_openrouter_429_rate_limit() -> None:
    s = "RuntimeError: openrouter_http_error status=429 body='Too Many Requests'"
    assert _looks_like_openrouter_rate_limited_429(s) is True


def test_detects_openrouter_429_rate_limit_bad_response_no_choices() -> None:
    s = (
        "RuntimeError: openrouter_bad_response_no_choices: "
        "{'error': {'message': 'Provider returned error', 'code': 429}}"
    )
    assert _looks_like_openrouter_rate_limited_429(s) is True


def test_detects_openrouter_timeout() -> None:
    s = "RuntimeError: openrouter_timeout: ReadTimeout('timed out')"
    assert _looks_like_openrouter_timeout(s) is True


def test_detects_openrouter_gateway_timeout_524_bad_response_no_choices() -> None:
    s = (
        "RuntimeError: Stage2 failed: stage2_subtitles=RuntimeError: "
        "openrouter_bad_response_no_choices: {'error': {'message': 'Provider returned error', 'code': 524}}"
    )
    assert _looks_like_openrouter_gateway_timeout_524(s) is True


def test_detects_openrouter_bad_request_400() -> None:
    s = "RuntimeError: openrouter_http_error status=400 body='Provider returned error'"
    assert _looks_like_openrouter_bad_request_400(s) is True


def test_detects_stage1a_selected_fragment_missing() -> None:
    s = "ValueError: subtitles_mode='impulse_2nd' requires Stage1A.selected_fragment, got null"
    assert _looks_like_stage1a_selected_fragment_missing(s) is True


def test_detects_openrouter_internal_500_http_error() -> None:
    s = "RuntimeError: openrouter_http_error status=500 body='Internal Server Error'"
    assert _looks_like_openrouter_internal_500(s) is True


def test_detects_openrouter_internal_500_bad_response_shape() -> None:
    s = (
        "RuntimeError: Stage2 failed: stage2_subtitles=RuntimeError: "
        "openrouter_bad_response_no_choices: {'error': {'message': 'Internal Server Error', 'code': 500}}"
    )
    assert _looks_like_openrouter_internal_500(s) is True


def test_detects_openrouter_provider_unavailable_502_bad_response_shape() -> None:
    s = (
        "RuntimeError: Stage2 failed: stage2_subtitles=RuntimeError: "
        "openrouter_bad_response_no_text_content: {'choices': [{'error': {'code': 502, "
        "'message': 'Network connection lost.', 'metadata': {'error_type': 'provider_unavailable'}}}]}"
    )
    assert _looks_like_openrouter_provider_unavailable_502(s) is True


def test_detects_openrouter_provider_unavailable_502_http_error() -> None:
    s = "RuntimeError: openrouter_http_error status=502 body='provider_unavailable: network connection lost'"
    assert _looks_like_openrouter_provider_unavailable_502(s) is True


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


def test_schema_validation_marker_is_not_transient_retriable() -> None:
    s = "RuntimeError: openrouter_schema_validation_failed err=ValidationError(...)"
    assert _looks_like_llm_schema_validation_error(s) is True
    assert _looks_like_gemini_internal_500(s) is False
    assert _looks_like_gemini_overloaded_503(s) is False
    assert _looks_like_gemini_rate_limited_429(s) is False
    assert _looks_like_openrouter_timeout(s) is False
    assert _looks_like_openrouter_bad_request_400(s) is False
    assert _looks_like_openrouter_gateway_timeout_524(s) is False
    assert _looks_like_openrouter_internal_500(s) is False
    assert _looks_like_openrouter_provider_unavailable_502(s) is False
    assert _looks_like_openrouter_overloaded_503(s) is False
    assert _looks_like_openrouter_rate_limited_429(s) is False


def test_detects_build_preflight_validation_error() -> None:
    s = "ValueError: Preflight: out<=in in layer 'Adjustment Layer 10': 15.01..5.6"
    assert _looks_like_build_preflight_validation_error(s) is True


def test_extracts_preflight_out_le_in_issue_details() -> None:
    s = "ValueError: Preflight: out<=in in layer 'Adjustment Layer 10': 15.01..5.6"
    issue = _extract_preflight_out_le_in_issue(s)
    assert isinstance(issue, dict)
    assert issue["layer_name"] == "Adjustment Layer 10"
    assert abs(float(issue["in_point"]) - 15.01) <= 1e-9
    assert abs(float(issue["out_point"]) - 5.6) <= 1e-9


def test_extract_preflight_out_le_in_issue_returns_none_when_missing() -> None:
    assert _extract_preflight_out_le_in_issue("RuntimeError: something else") is None


def test_transient_windows_error_urlerror() -> None:
    e = urllib.error.URLError("broken pipe")
    assert _is_transient_windows_error(e) is True


def test_overloaded_retry_backoff_is_powers_of_two_capped_at_64s() -> None:
    seq = [_overloaded_retry_backoff_s(attempt=i) for i in range(1, 10)]
    assert seq == [2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 64.0, 64.0, 64.0]


def test_provider_mode_mapping_by_worker_type() -> None:
    assert _provider_mode_for_worker_type("sdk") == "gemini"
    assert _provider_mode_for_worker_type("openrouter") == "openrouter"
    assert _provider_mode_for_worker_type("hybrid") == "hedged"


def test_provider_mode_mapping_rejects_unknown_worker_type() -> None:
    try:
        _provider_mode_for_worker_type("unknown")
        raise AssertionError("expected RuntimeError for unknown worker type")
    except RuntimeError as e:
        assert "LLM worker type must be one of" in str(e)
