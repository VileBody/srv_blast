# services/orchestrator/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

from core.llm_worker_types import LLM_WORKER_TYPE_SDK
from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS, SubtitlesMode


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
    # Use the canonical SubtitlesMode (core) so new modes never drift from the
    # API contract (trendy_5th/brat_5th were added there).
    subtitles_mode: SubtitlesMode = SUBTITLES_MODE_LEGACY_BLOCKS
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
    # F5 Cognition («Мысль») device. When the user picks the "Мысль" hook
    # category, the bot sends the chosen F5 device here (one of:
    # punchline / missing_word / lyric_echo / question_to_track / inverse_lyric).
    # Propagated to the build env as F5_HOOK_DEVICE, which switches on the F5
    # pipeline in mlcore.hooks.f5_cognition.orchestrator_hook. None => no F5 hook.
    hook_device: Optional[
        Literal[
            "punchline",
            "missing_word",
            "lyric_echo",
            "question_to_track",
            "inverse_lyric",
        ]
    ] = None
    # F4 «Движение» motion-hook device. When the user picks the "Движение" hook
    # category, the bot sends the chosen device here (swipe / tap / pinch /
    # holdfinger / head). Propagated to the build env as F4_HOOK_DEVICE, which
    # makes the orchestrator emit full_edit_config["f4"] for the AE overlay.
    # The bot pre-reframes the clip window so clip_start == drop - LEAD[device].
    # None => no F4 hook.
    f4_device: Optional[
        Literal["swipe", "tap", "pinch", "holdfinger", "head"]
    ] = None
    # BPM the bot used to reframe the clip window for F4 (clip_start =
    # drop − LEAD·refBpm/bpm). The orchestrator must build the overlay with the
    # SAME bpm, else cover-end (t(LEAD) = LEAD·refBpm/bpm) misses the drop. The
    # bot measures bpm on the ORIGINAL clip; re-measuring on the reframed clip
    # downstream diverges. None → orchestrator falls back to its own measure.
    f4_bpm: Optional[float] = Field(default=None, gt=0.0)
    # F3 «Эффект» visual-FX selection (3-step: hook / transition / extra). When
    # the user picks the "Эффект" hook category, the bot sends the chosen effect
    # ids here. Propagated to the build env as F3_HOOK / F3_TRANSITION / F3_EXTRA
    # (+ F3_HOOK_EXTEND); the orchestrator emits full_edit_config["f3"] and
    # project_builder injects the AE overlay. Requires user_drop_t (drop anchor).
    # None on all => no F3 fx.
    effect_hook: Optional[
        Literal["hook_light", "shutter_effect", "flash_slow_shutter"]
    ] = None
    effect_transition: Optional[
        Literal[
            "snap_wipe", "minimax", "invert_flash",
            "extract_flash", "flash_on_cuts", "layer_shake",
        ]
    ] = None
    effect_extra: Optional[
        Literal[
            "xerox", "analog_glitch", "neon_extract", "old_camera",
        ]
    ] = None
    # Stretch effect_extra (grade) over the whole video instead of pre-drop only.
    effect_extra_full: bool = False
    # Slow-shutter trail extension (only for extendable hooks): "to_end" or
    # "after_drop:N" (N = footages after the drop). None => default duration.
    effect_hook_extend: Optional[str] = Field(default=None, max_length=24)
    # F2 «Объект» packaged-combo selection. When the user picks the "Объект"
    # hook category, the bot sends the chosen shape id here. Propagated to the
    # build env as F2_SHAPE; the orchestrator emits full_edit_config["f2"] and
    # project_builder injects the AE overlay (shape on pre-drop cuts +
    # hook_light at drop + seeded-random F3 transition on post-drop cuts).
    # Requires user_drop_t (drop anchor). None => no F2 combo.
    f2_shape: Optional[
        Literal["rhomb", "square", "star1", "star2", "elipse"]
    ] = None
    # F1 «Звук» packaged-combo: S3/HTTP URL of the user-uploaded sound that plays
    # in the pre-drop window [0.5, drop−0.5]. When set, the bot threads it here;
    # the orchestrator emits full_edit_config["f1"] (audio layer + F2-style visual
    # combo: hook_light at drop + seeded-random F3 transition post-drop).
    # Requires user_drop_t (drop anchor). None => no F1 combo.
    f1_sound_url: Optional[str] = Field(default=None, max_length=2048)
    # Optional subtitle text for the F1 sound (the user types what their sound
    # "says"). When present, the orchestrator renders it as a track-type subtitle
    # over the sound window (same machinery as F5). Empty/None => no subtitle.
    f1_sound_text: Optional[str] = Field(default=None, max_length=2000)
    # Customization colors (hex '#RRGGBB'). subtitle = text fill (all modes);
    # accent = F2 shape + focus/accent word. None/empty => script default.
    subtitle_color_hex: Optional[str] = Field(default=None, pattern=r"^#?[0-9a-fA-F]{6}$")
    accent_color_hex: Optional[str] = Field(default=None, pattern=r"^#?[0-9a-fA-F]{6}$")
    # Optional internal batch controls for multi-version generation.
    reuse_text_job_id: Optional[str] = None
    # When True (bigtest only): seed stage2_style + stage2_style_rotation from
    # reuse_text_job_id so the footage genre/style is identical across all cases.
    reuse_stage2_footage: bool = False
    # When set (bigtest only): override STAGE2_SELECTION_SEED so the footage
    # picker uses the same random seed as the source job → identical clips.
    stage2_selection_seed_override: Optional[str] = None
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
        # F3 effect-hook extend must be "to_end" or "after_drop:N" (N >= 1).
        ext = (self.effect_hook_extend or "").strip().lower()
        if ext:
            ok = ext == "to_end"
            if not ok and ext.startswith("after_drop:"):
                tail = ext.split(":", 1)[1]
                ok = tail.isdigit() and int(tail) >= 1
            if not ok:
                raise ValueError(
                    f"effect_hook_extend must be 'to_end' or 'after_drop:N' (N>=1), got {self.effect_hook_extend!r}"
                )
        # F3 fx needs a drop anchor (the hook lands on the drop).
        if (self.effect_hook or self.effect_transition or self.effect_extra) and self.user_drop_t is None:
            raise ValueError("effect_* requires user_drop_t (drop anchor) to be set")
        # F2 combo also pivots on the drop (pre/post split + hook_light on drop).
        if self.f2_shape and self.user_drop_t is None:
            raise ValueError("f2_shape requires user_drop_t (drop anchor) to be set")
        # F1 combo pivots on the drop too (audio window [0.5, drop−0.5] + combo).
        if self.f1_sound_url and self.user_drop_t is None:
            raise ValueError("f1_sound_url requires user_drop_t (drop anchor) to be set")
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


# ---- Hook focus-clip analysis (F4 «Движение» picker) ----
# The bots are slim (no librosa). They call this so the orchestrator (runtime
# image, has librosa) runs analyze_focus_clip and returns just the picker data:
# top drop candidates + measured bpm. Keeps the heavy ML dep out of the bots.
class HookAnalyzeRequest(BaseModel):
    audio_s3_url: str = Field(min_length=1)
    clip_start_sec: float = Field(ge=0.0)
    clip_end_sec: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _validate_window(self) -> "HookAnalyzeRequest":
        if float(self.clip_end_sec) <= float(self.clip_start_sec):
            raise ValueError("clip_end_sec must be > clip_start_sec")
        return self


class HookDropCandidate(BaseModel):
    t: float
    confidence: float
    snapped_to_beat: bool = False
    source: str = ""


class HookAnalyzeResponse(BaseModel):
    bpm: float
    drop_candidates: List[HookDropCandidate] = Field(default_factory=list)


class RankBucketsRequest(BaseModel):
    lyrics: str = ""
    mood: str = ""  # "minor" | "major" | "" (no filter)
    top: int = Field(default=0, ge=0)  # 0 = full ranked list


class RankedBucket(BaseModel):
    bucket_id: str
    theme: str
    tags_group: str
    mood: str
    label: str


class RankBucketsResponse(BaseModel):
    buckets: List[RankedBucket] = Field(default_factory=list)
    used_llm: bool = False
