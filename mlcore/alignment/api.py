from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
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
    return JSONResponse(
        status_code=status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def create_app(runtime: AlignmentRuntime | None = None) -> FastAPI:
    selected_runtime = runtime or AlignmentRuntime(AlignmentSettings.from_env())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await selected_runtime.start()
        yield
        await selected_runtime.close()

    app = FastAPI(title="Blast alignment API", version="1", lifespan=lifespan)
    app.state.alignment_runtime = selected_runtime

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

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
            log.warning(
                "alignment_failed request_id=%s code=%s elapsed_s=%.3f",
                req.request_id,
                exc.code,
                time.monotonic() - started,
            )
            return _error_response(exc)
        except Exception as exc:
            log.exception(
                "alignment_failed request_id=%s code=%s elapsed_s=%.3f",
                req.request_id,
                ERROR_INTERNAL,
                time.monotonic() - started,
            )
            return _error_response(
                AlignmentFailure(ERROR_INTERNAL, f"{type(exc).__name__}: {exc}")
            )
        log.info(
            "alignment_succeeded request_id=%s words=%d elapsed_s=%.3f",
            req.request_id,
            len(result.stage1_asr.transcript_words),
            time.monotonic() - started,
        )
        return AlignResponse(
            stage1_asr=result.stage1_asr,
            diagnostics=result.diagnostics,
            backend=result.backend,
        )

    return app


app = create_app()
