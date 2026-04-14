from __future__ import annotations

from services.tg_bot_public.job_recovery_policy import (
    decide_job_recovery,
    is_forbidden_delivery_error,
    is_retriable_failed_job,
)


def test_decide_wait_for_running_job() -> None:
    d = decide_job_recovery(status="RUNNING", stage="llm_stage2_parallel", error_text="")
    assert d.action == "wait"


def test_decide_send_for_succeeded_job() -> None:
    d = decide_job_recovery(status="SUCCEEDED", stage="render", error_text="")
    assert d.action == "send_only"


def test_retriable_failed_openrouter_524() -> None:
    err = (
        "celery_failed stage=build exc=RuntimeError(\"Stage2 failed: "
        "openrouter_bad_response_no_choices: {'error': {'message': 'Provider returned error', 'code': 524}}\")"
    )
    assert is_retriable_failed_job(stage="build", error_text=err) is True
    d = decide_job_recovery(status="FAILED", stage="build", error_text=err)
    assert d.action == "requeue_retriable_failed"


def test_non_retriable_failed_schema_validation() -> None:
    err = "RuntimeError: openrouter_schema_validation_failed err=ValidationError(...)"
    assert is_retriable_failed_job(stage="build", error_text=err) is False
    d = decide_job_recovery(status="FAILED", stage="build", error_text=err)
    assert d.action == "skip_failed_non_retriable"


def test_non_retriable_failed_selected_fragment_missing() -> None:
    err = "ValueError: subtitles_mode='impulse_2nd' requires Stage1A.selected_fragment, got null"
    assert is_retriable_failed_job(stage="build", error_text=err) is False
    d = decide_job_recovery(status="FAILED", stage="build", error_text=err)
    assert d.action == "skip_failed_non_retriable"


def test_forbidden_delivery_error_detection() -> None:
    assert is_forbidden_delivery_error("Forbidden: bot was blocked by the user") is True
    assert is_forbidden_delivery_error("Forbidden: chat not found") is True
    assert is_forbidden_delivery_error("Network is unreachable") is False

