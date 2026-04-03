# services/orchestrator/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional, Literal, List
from pydantic import BaseModel, Field

from core.llm_worker_types import LLM_WORKER_TYPE_SDK
from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS


JobStatus = Literal["NEW", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]


class SendAudioS3Request(BaseModel):
    """
    Minimal payload:
      - audio_s3_url: where raw audio is stored (http/s3/etc)
      - mode: with_gemini | no_gemini
      - llm_worker_type: optional explicit worker type pin
      - idempotency_key: optional dedupe key
    """
    audio_s3_url: str = Field(min_length=1)
    project_id: Optional[str] = None
    mode: Literal["with_gemini", "no_gemini"] = "with_gemini"
    llm_worker_type: Optional[Literal["sdk", "openrouter", "hybrid"]] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1)
    lyrics_text: str = ""
    target_fragment: str = ""
    subtitles_mode: Literal[
        "legacy_blocks",
        "impulse_2nd",
        "scenes_3rd",
        "scenes_3rd_single_step",
        "template_4th",
    ] = SUBTITLES_MODE_LEGACY_BLOCKS
    footage_artist_id: Optional[str] = None
    # Optional internal batch controls for multi-version generation.
    reuse_text_job_id: Optional[str] = None
    exclude_file_names: List[str] = Field(default_factory=list)
    variant_index: Optional[int] = None
    variants_total: Optional[int] = None


class EnqueueJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created: bool = True


class JobState(BaseModel):
    job_id: str
    status: JobStatus
    version: int = Field(default=0, ge=0)

    created_at: float
    updated_at: float
    queued_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    stage: Optional[str] = None  # "build" | "dispatch" | "render" | "poll"
    idempotency_key: Optional[str] = None

    request: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class LLMWorkerControl(BaseModel):
    enabled: bool = True
    weight: int = Field(default=1, ge=0, le=1000)
    max_inflight: int = Field(default=4, ge=1, le=1000)


class LLMWorkersConfigRequest(BaseModel):
    workers: Dict[Literal["sdk", "openrouter", "hybrid"], LLMWorkerControl]


class LLMWorkerRuntimeStatus(BaseModel):
    enabled: bool
    weight: int
    max_inflight: int
    inflight: int
    available_slots: int


class LLMWorkersStatusResponse(BaseModel):
    workers: Dict[Literal["sdk", "openrouter", "hybrid"], LLMWorkerRuntimeStatus]
    default_worker_type: Literal["sdk", "openrouter", "hybrid"] = LLM_WORKER_TYPE_SDK


# ---- Backward-compatible aliases (so old clients don't break) ----
# If you want to remove old names later — delete these aliases and the /send_video route in app.py.
SendVideoRequest = SendAudioS3Request
SendVideoResponse = EnqueueJobResponse
