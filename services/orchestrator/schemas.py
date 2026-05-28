# services/orchestrator/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

from core.llm_worker_types import LLM_WORKER_TYPE_SDK
from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS


LLMWorkerTypeLiteral = Literal["sdk", "openrouter", "hybrid", "vertex_sdk_mix"]


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
    llm_worker_type: Optional[LLMWorkerTypeLiteral] = None
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
    user_clip_start_sec: Optional[float] = Field(default=None, ge=0.0)
    user_clip_end_sec: Optional[float] = Field(default=None, ge=0.0)
    # Hook feature (Phase A-UX). When `hook_enabled` is true the orchestrator
    # is told the job wants hook-aware Stage2 timing AND any AE-FX downstream.
    # `user_drop_t` is the user-confirmed audio drop moment inside the focus
    # clip; when set, it overrides the algorithmic top-1 drop candidate. None
    # means either "user picked no-drop" or "hook off entirely" — they are
    # disambiguated by `hook_enabled`.
    hook_enabled: bool = False
    user_drop_t: Optional[float] = Field(default=None, ge=0.0)
    # Optional internal batch controls for multi-version generation.
    reuse_text_job_id: Optional[str] = None
    exclude_file_names: List[str] = Field(default_factory=list)
    variant_index: Optional[int] = None
    variants_total: Optional[int] = None
    maintenance_bypass_token: Optional[str] = Field(default=None, min_length=1)
    # Per-user Stage 2B rotation cursor override. When both are non-empty, the
    # orchestrator forces Gemini to emit exactly one subgroup at this
    # (theme, tags_group) pair instead of picking from the artist profile.
    rotation_theme: str = ""
    rotation_tags_group: str = ""
    # Background mode: "footage" (default) or "solid". When "solid", the AE
    # composition replaces the footage stack with a single solid color layer.
    # Stage 2 footage planning still runs (its picks are simply ignored at
    # composition time), so footage_artist_id must still be a valid id.
    bg_mode: Literal["footage", "solid"] = "footage"
    # Solid color key when bg_mode == "solid": "white" or "green".
    bg_solid_color: str = ""
    # Internal routing pinning metadata.
    # Public callers should not set these fields directly.
    origin_node: Optional[str] = None
    build_queue: Optional[str] = None
    render_queue: Optional[str] = None
    render_poll_queue: Optional[str] = None

    @model_validator(mode="after")
    def _validate_user_clip_window(self) -> "SendAudioS3Request":
        start = self.user_clip_start_sec
        end = self.user_clip_end_sec
        if start is None and end is None:
            pass
        elif start is None or end is None:
            raise ValueError("user_clip_start_sec and user_clip_end_sec must be provided together")
        elif float(end) <= float(start):
            raise ValueError("user_clip_end_sec must be > user_clip_start_sec")
        # If user picked a drop, it must lie inside the focus clip window.
        if self.user_drop_t is not None and start is not None and end is not None:
            if not (float(start) <= float(self.user_drop_t) <= float(end)):
                raise ValueError(
                    f"user_drop_t must be inside user clip window "
                    f"[{start}, {end}], got {self.user_drop_t}"
                )
        return self


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


class JobsBatchRequest(BaseModel):
    job_ids: List[str] = Field(default_factory=list, min_length=1, max_length=500)


class JobsBatchResponse(BaseModel):
    jobs: List[JobState] = Field(default_factory=list)
    total: int = 0


class LLMWorkerControl(BaseModel):
    enabled: bool = True
    weight: int = Field(default=1, ge=0, le=1000)
    max_inflight: int = Field(default=4, ge=1, le=1000)


class LLMWorkersConfigRequest(BaseModel):
    workers: Dict[LLMWorkerTypeLiteral, LLMWorkerControl]


class LLMWorkerRuntimeStatus(BaseModel):
    enabled: bool
    weight: int
    max_inflight: int
    inflight: int
    available_slots: int


class LLMWorkersStatusResponse(BaseModel):
    workers: Dict[LLMWorkerTypeLiteral, LLMWorkerRuntimeStatus]
    default_worker_type: LLMWorkerTypeLiteral = LLM_WORKER_TYPE_SDK


class ActiveJobSummary(BaseModel):
    job_id: str
    status: JobStatus
    stage: Optional[str] = None
    project_id: str = ""
    llm_worker_type: str = ""
    idempotency_key: str = ""
    created_at: float
    updated_at: float
    age_seconds: int = 0


class ActiveJobsResponse(BaseModel):
    jobs: List[ActiveJobSummary] = Field(default_factory=list)
    total_active: int = 0
    min_age_seconds: int = 0
    limit: int = 100


class QueueEstimateResponse(BaseModel):
    job_id: str
    status: JobStatus
    active: bool = False
    queue_position: int = Field(default=0, ge=0)
    active_jobs_total: int = Field(default=0, ge=0)
    window_size: int = Field(default=50, ge=1, le=500)
    sample_size: int = Field(default=0, ge=0)
    avg_duration_seconds: Optional[float] = None
    eta_seconds: Optional[float] = None


class KillJobRequest(BaseModel):
    reason: str = Field(default="admin_kill_stuck", min_length=1, max_length=500)


class KillJobResponse(BaseModel):
    job_id: str
    previous_status: JobStatus
    new_status: JobStatus
    stage: str
    reason: str
    revoked_task_ids: List[str] = Field(default_factory=list)
    project_id: str = ""


class RequeueJobRequest(BaseModel):
    reason: str = Field(default="admin_requeue_stuck", min_length=1, max_length=500)
    llm_worker_type: str = Field(default="", max_length=50)


class RequeueJobResponse(BaseModel):
    job_id: str
    previous_status: JobStatus
    new_status: JobStatus
    stage: str
    reason: str
    llm_worker_type: str
    revoked_task_ids: List[str] = Field(default_factory=list)
    project_id: str = ""


class WindowsNodeState(BaseModel):
    url: str = Field(min_length=1)
    enabled: bool = True
    disabled_reason: Optional[str] = None
    disabled_at: Optional[float] = None


class WindowsNodesUpdateRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)
    nodes: List[WindowsNodeState] = Field(default_factory=list)


class WindowsNodesStatusResponse(BaseModel):
    source: Literal["runtime", "env"]
    default_urls: List[str] = Field(default_factory=list)
    runtime_urls: List[str] = Field(default_factory=list)
    effective_urls: List[str] = Field(default_factory=list)
    nodes: List[WindowsNodeState] = Field(default_factory=list)
    inflight: Dict[str, int] = Field(default_factory=dict)


# ---- Backward-compatible aliases (so old clients don't break) ----
# If you want to remove old names later — delete these aliases and the /send_video route in app.py.
SendVideoRequest = SendAudioS3Request
SendVideoResponse = EnqueueJobResponse
