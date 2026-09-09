from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from mlcore.models.stage1_asr import Stage1AsrPayload

from .contracts import (
    ERROR_MODEL_UNAVAILABLE,
    ERROR_PRONUNCIATION_UNAVAILABLE,
    ERROR_SEPARATOR_UNAVAILABLE,
    ERROR_SOURCE_SEPARATION_FAILED,
    ERROR_TIMEOUT,
)
from .core import AlignmentFailure, ERROR_INTERNAL
from .runtime import AlignmentRuntime, AlignmentSettings


log = logging.getLogger("alignment-api")

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def rejection_profile(details: dict[str, Any] | None) -> str:
    """Stable label for *why* a window search failed.

    The reason set is what distinguishes "the user's text does not fit" from
    "the boundaries are acoustically unprovable"; the counts are noise for
    alerting purposes, so only the sorted reason names form the label.
    """
    counts = (details or {}).get("rejection_counts")
    if not isinstance(counts, dict) or not counts:
        return "none"
    return "|".join(sorted(str(reason) for reason in counts))


class AlignmentMetrics:
    """Minimal Prometheus text exposition. Deliberately dependency-free: the
    alignment image ships a pinned scientific stack and is not worth a new
    runtime dependency for four counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._outcomes: dict[tuple[str, str, str], int] = {}

    def record(self, *, outcome: str, code: str, profile: str) -> None:
        key = (str(outcome), str(code), str(profile))
        with self._lock:
            self._outcomes[key] = self._outcomes.get(key, 0) + 1

    def snapshot(self) -> dict[tuple[str, str, str], int]:
        with self._lock:
            return dict(self._outcomes)

    def render(self) -> str:
        lines = [
            "# HELP blast_alignment_requests_total Alignment requests by outcome.",
            "# TYPE blast_alignment_requests_total counter",
        ]
        for (outcome, code, profile), value in sorted(self.snapshot().items()):
            labels = (
                f'outcome="{_escape_label(outcome)}",'
                f'error_code="{_escape_label(code)}",'
                f'rejection_profile="{_escape_label(profile)}"'
            )
            lines.append(f"blast_alignment_requests_total{{{labels}}} {value}")
        return "\n".join(lines) + "\n"


def _escape_label(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


class AlignRequest(BaseModel):
    audio_path: str = Field(min_length=1)
    target_fragment: str = Field(min_length=1)
    clip_start_abs: float = Field(ge=0.0)
    clip_end_abs: float = Field(gt=0.0)
    request_id: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _validate_window(self) -> "AlignRequest":
        if self.clip_end_abs <= self.clip_start_abs:
            raise ValueError("clip_end_abs must be > clip_start_abs")
        return self


class AlignResponse(BaseModel):
    stage1_asr: Stage1AsrPayload
    diagnostics: dict[str, Any]
    backend: dict[str, Any]


def _error_response(exc: AlignmentFailure) -> JSONResponse:
    status = (
        503
        if exc.code in {
            ERROR_MODEL_UNAVAILABLE,
            ERROR_PRONUNCIATION_UNAVAILABLE,
            ERROR_SEPARATOR_UNAVAILABLE,
        }
        else 422
    )
    if exc.code == ERROR_TIMEOUT:
        status = 504
    if exc.code in {ERROR_INTERNAL, ERROR_SOURCE_SEPARATION_FAILED}:
        status = 500
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details:
        error["details"] = exc.details
    return JSONResponse(
        status_code=status,
        content={"error": error},
    )


def create_app(runtime: AlignmentRuntime | None = None) -> FastAPI:
    selected_runtime = runtime or AlignmentRuntime(AlignmentSettings.from_env())
    metrics = AlignmentMetrics()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await selected_runtime.start()
        yield
        await selected_runtime.close()

    app = FastAPI(title="Blast alignment API", version="1", lifespan=lifespan)
    app.state.alignment_runtime = selected_runtime
    app.state.alignment_metrics = metrics

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(
            content=metrics.render(),
            media_type=PROMETHEUS_CONTENT_TYPE,
        )

    @app.get("/ready")
    async def ready() -> JSONResponse:
        status = selected_runtime.status()
        return JSONResponse(status_code=200 if status.get("ready") else 503, content=status)

    @app.post("/align", response_model=AlignResponse)
    async def align(req: AlignRequest) -> AlignResponse | JSONResponse:
        started = time.monotonic()
        try:
            result = await selected_runtime.align(
                audio_path=req.audio_path,
                target_fragment=req.target_fragment,
                clip_start_abs=req.clip_start_abs,
                clip_end_abs=req.clip_end_abs,
            )
        except AlignmentFailure as exc:
            profile = rejection_profile(exc.details)
            metrics.record(outcome="failure", code=exc.code, profile=profile)
            log.warning(
                "alignment_failed request_id=%s code=%s rejection_profile=%s "
                "elapsed_s=%.3f details=%s",
                req.request_id,
                exc.code,
                profile,
                time.monotonic() - started,
                exc.details,
            )
            return _error_response(exc)
        except Exception as exc:
            metrics.record(outcome="failure", code=ERROR_INTERNAL, profile="none")
            log.exception(
                "alignment_failed request_id=%s code=%s elapsed_s=%.3f",
                req.request_id,
                ERROR_INTERNAL,
                time.monotonic() - started,
            )
            return _error_response(
                AlignmentFailure(ERROR_INTERNAL, f"{type(exc).__name__}: {exc}")
            )
        dynamic_window = dict(result.diagnostics.get("dynamic_window") or {})
        overflow_applied = bool(dynamic_window.get("boundary_overflow_applied"))
        metrics.record(
            outcome="success",
            code="",
            profile=(
                "boundary_window_overflow_tolerated" if overflow_applied else "clean"
            ),
        )
        log.info(
            "alignment_succeeded request_id=%s words=%d elapsed_s=%.3f "
            "boundary_overflow_applied=%s selection_reason=%s",
            req.request_id,
            len(result.stage1_asr.transcript_words),
            time.monotonic() - started,
            overflow_applied,
            dynamic_window.get("selection_reason") or "",
        )
        return AlignResponse(
            stage1_asr=result.stage1_asr,
            diagnostics=result.diagnostics,
            backend=result.backend,
        )

    return app


app = create_app()
