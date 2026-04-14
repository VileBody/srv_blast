from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RecoveryAction = Literal[
    "wait",
    "send_only",
    "requeue_retriable_failed",
    "skip_failed_non_retriable",
    "skip_unknown",
]


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


def is_retriable_failed_job(*, stage: str, error_text: str) -> bool:
    """
    Policy for failed jobs:
    - requeue only transient/provider failures
    - do not requeue schema/business-validation failures
    """
    del stage  # reserved for future stage-specific policy
    lo = _normalize(error_text)
    if not lo:
        return False

    # Explicitly non-retriable markers.
    non_retriable_markers = (
        "openrouter_schema_validation_failed",
        "openrouter_tokens_schema_validation_failed",
        "stage1 scenario validation failed",
        "build_preflight_validation_error_after_immediate_retry",
        "requires stage1a.selected_fragment",
        "stage1a_selected_fragment_missing",
    )
    if any(marker in lo for marker in non_retriable_markers):
        return False

    # Transient/retriable markers.
    retriable_markers = (
        "gemini_internal_500",
        "gemini_overloaded_503",
        "gemini_rate_limited_429",
        "openrouter_timeout",
        "openrouter_internal_500",
        "openrouter_provider_unavailable_502",
        "openrouter_overloaded_503",
        "openrouter_rate_limited_429",
        "openrouter_bad_request_400",
        "openrouter_gateway_timeout_524",
    )
    if any(marker in lo for marker in retriable_markers):
        return True

    # OpenRouter 524 often arrives in "bad_response_no_choices/no_text_content" body.
    if "openrouter_bad_response_no_choices" in lo and ("'code': 524" in lo or '"code": 524' in lo):
        return True
    if "openrouter_bad_response_no_text_content" in lo and ("'code': 524" in lo or '"code": 524' in lo):
        return True

    # Generic fallback for clear transient network/provider outages.
    if "provider returned error" in lo and "524" in lo:
        return True
    if "network connection lost" in lo and ("openrouter" in lo or "provider_unavailable" in lo):
        return True
    return False


def decide_job_recovery(*, status: str, stage: str, error_text: str) -> RecoveryDecision:
    st = _normalize(status).upper()
    if st in {"RUNNING", "QUEUED", "NEW"}:
        return RecoveryDecision(action="wait", reason=f"job_status_{st.lower()}")
    if st == "SUCCEEDED":
        return RecoveryDecision(action="send_only", reason="job_succeeded")
    if st == "FAILED":
        if is_retriable_failed_job(stage=stage, error_text=error_text):
            return RecoveryDecision(action="requeue_retriable_failed", reason="failed_retriable")
        return RecoveryDecision(action="skip_failed_non_retriable", reason="failed_non_retriable")
    return RecoveryDecision(action="skip_unknown", reason=f"job_status_{st.lower() or 'empty'}")


def is_forbidden_delivery_error(error_text: str) -> bool:
    lo = _normalize(error_text)
    if not lo:
        return False
    markers = (
        "forbidden",
        "bot was blocked by the user",
        "chat not found",
        "user is deactivated",
    )
    return any(m in lo for m in markers)

