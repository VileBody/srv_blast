from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from mlcore.models.stage1_asr import Stage1AsrPayload

from .contracts import ERROR_INTERNAL


class AlignmentServiceError(RuntimeError):
    job_stage = "alignment"

    def __init__(self, code: str, message: str):
        self.code = str(code or ERROR_INTERNAL)
        self.message = str(message)
        # Keep both constructor arguments in BaseException.args so Celery can
        # pickle/unpickle the concrete exception without replacing it.
        super().__init__(self.code, self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class LocalAlignmentResponse:
    stage1_asr: Stage1AsrPayload
    diagnostics: dict[str, Any]
    backend: dict[str, Any]


def request_local_alignment(
    *,
    service_url: str,
    timeout_s: float,
    audio_path: Path,
    target_fragment: str,
    clip_start_abs: float,
    clip_end_abs: float,
    request_id: str = "",
) -> LocalAlignmentResponse:
    base_url = str(service_url or "").strip().rstrip("/")
    if not base_url:
        raise AlignmentServiceError(
            "ALIGNMENT_MODEL_UNAVAILABLE",
            "ALIGNMENT_SERVICE_URL is empty",
        )
    try:
        # This is a Docker-internal service call. Proxy environment variables
        # are for external egress and must never intercept the private hostname.
        with httpx.Client(timeout=float(timeout_s), trust_env=False) as client:
            response = client.post(
                f"{base_url}/align",
                json={
                    "audio_path": str(Path(audio_path).resolve()),
                    "target_fragment": str(target_fragment),
                    "clip_start_abs": float(clip_start_abs),
                    "clip_end_abs": float(clip_end_abs),
                    "request_id": str(request_id or ""),
                },
            )
    except httpx.TimeoutException as exc:
        raise AlignmentServiceError(
            "ALIGNMENT_TIMEOUT",
            f"alignment service request exceeded {float(timeout_s):.1f}s",
        ) from exc
    except httpx.HTTPError as exc:
        raise AlignmentServiceError(
            "ALIGNMENT_MODEL_UNAVAILABLE",
            f"alignment service request failed: {type(exc).__name__}",
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AlignmentServiceError(
            ERROR_INTERNAL,
            f"alignment service returned non-JSON status={response.status_code}",
        ) from exc
    if response.status_code >= 300:
        error = payload.get("error") if isinstance(payload, dict) else None
        code = str(error.get("code") or ERROR_INTERNAL) if isinstance(error, dict) else ERROR_INTERNAL
        message = (
            str(error.get("message") or f"HTTP {response.status_code}")
            if isinstance(error, dict)
            else f"HTTP {response.status_code}"
        )
        raise AlignmentServiceError(code, message)
    if not isinstance(payload, dict):
        raise AlignmentServiceError(ERROR_INTERNAL, "alignment response is not an object")
    try:
        return LocalAlignmentResponse(
            stage1_asr=Stage1AsrPayload.model_validate(payload.get("stage1_asr")),
            diagnostics=dict(payload.get("diagnostics") or {}),
            backend=dict(payload.get("backend") or {}),
        )
    except Exception as exc:
        raise AlignmentServiceError(
            ERROR_INTERNAL,
            f"invalid alignment response: {type(exc).__name__}: {exc}",
        ) from exc
