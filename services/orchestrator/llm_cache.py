# services/orchestrator/llm_cache.py
"""
Per-user S3 LLM output cache for the blast generation pipeline.

Caches LLM stages into S3, keyed by telegram_id + audio content hash +
all inputs that affect each stage's output. Footage selection (stage2_footage)
is NEVER cached — users expect clip variety on every run.

What IS cached (subset of _REUSE_RESUME_STATE_KEYS):
  stage1_asr block   — Stage 1A ASR word timings
  stage1_plan block  — Stage 1B scenario/blocks (legacy_blocks mode only)
  stage2_subs block  — Stage 2A subtitle layout
  stage2_timing block — Stage 2C switch timestamps

Env vars:
  LLM_CACHE_ENABLED      "true"/"false"  default: "false"
  LLM_CACHE_SAVE_ENABLED "true"/"false"  default: same as LLM_CACHE_ENABLED
  LLM_CACHE_S3_BUCKET    bucket name     required when enabled
  LLM_CACHE_S3_PREFIX    path prefix     default: "llm_cache"
  LLM_CACHE_LOCK_TTL_S   int seconds     default: 900

S3 Lifecycle TTL: configure an S3 lifecycle rule on the prefix to expire
objects after N days (e.g. 30). Example MinIO/AWS rule target:
  prefix: llm_cache/   expiry: 30 days
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

# Keys from resume_state that are safe to cache.
# stage2_footage is intentionally absent — footage selection is NEVER cached.
_STAGE1_ASR_KEYS = frozenset({
    "stage1_asr",
    "stage1_asr_mode",
    "stage1_asr_reference_text",
})
_STAGE1_PLAN_KEYS = frozenset({
    "stage1_plan",
    "stage1_plan_source",
})
_STAGE2_SUBS_KEYS = frozenset({
    "stage2_subtitles",
    "stage2_subtitles_mode",
})
_STAGE2_TIMING_KEYS = frozenset({
    "stage2_switch_timestamps",
    "stage2_timing_mode",
    "stage2_fast_start_seconds",
})

ALL_CACHEABLE_KEYS: frozenset[str] = (
    _STAGE1_ASR_KEYS | _STAGE1_PLAN_KEYS | _STAGE2_SUBS_KEYS | _STAGE2_TIMING_KEYS
)


# ---------------------------------------------------------------------------
# Prompt version helpers — read from prompt modules at import time.
# Returns "v0" if the attribute is missing so old deployments degrade safely.
# ---------------------------------------------------------------------------

def _prompt_version(module_path: str, attr: str = "PROMPT_VERSION") -> str:
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return str(getattr(mod, attr, "v0") or "v0")
    except Exception:
        return "v0"


def _subtitles_prompt_version(subtitles_mode: str) -> str:
    _mode_to_module = {
        "legacy_blocks": "mlcore.prompts.step2_subtitles_only",
        "impulse_2nd": "mlcore.prompts.stage2_subtitles_impulse_2nd",
        "scenes_3rd": "mlcore.prompts.stage2_subtitles_scenes_3rd",
        "scenes_3rd_single_step": "mlcore.prompts.stage2_subtitles_scenes_3rd_single_step",
        "template_4th": "mlcore.prompts.stage2_subtitles_template_4th",
    }
    mod = _mode_to_module.get(str(subtitles_mode or "").strip().lower())
    if not mod:
        return "v0"
    return _prompt_version(mod)


# ---------------------------------------------------------------------------
# Audio hashing
# ---------------------------------------------------------------------------

def compute_audio_hash(path: Path) -> str:
    """SHA-256 of the full audio file bytes. Same file = same hash regardless of URL/name."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Cache key dataclass and fingerprint helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CacheKey:
    telegram_id: str
    audio_hash: str
    clip_start: str   # "none" or f"{val:.3f}"
    clip_end: str     # "none" or f"{val:.3f}"
    asr_mode: str     # "asr" or "forced_alignment"
    ref_text_hash: str  # sha256[:16] of lyrics_text, or "none"
    subtitles_mode: str
    user_drop_t: str  # "none" or f"{val:.3f}"
    stage1_model: str
    subtitles_model: str
    asr_prompt_v: str
    forced_align_prompt_v: str
    stage1b_prompt_v: str
    subtitles_prompt_v: str
    timing_prompt_v: str


def _fingerprint(parts: list[str]) -> str:
    """Short SHA-256 hex of joined parts, used as S3 filename discriminator."""
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _stage1a_fp(k: CacheKey) -> str:
    asr_prompt = k.forced_align_prompt_v if k.asr_mode == "forced_alignment" else k.asr_prompt_v
    return _fingerprint([
        k.telegram_id, k.audio_hash, k.clip_start, k.clip_end,
        k.asr_mode, k.ref_text_hash,
        k.stage1_model, asr_prompt,
    ])


def _stage1b_fp(k: CacheKey) -> str:
    # Stage 1B builds on Stage 1A output, so includes all stage1a inputs.
    asr_prompt = k.forced_align_prompt_v if k.asr_mode == "forced_alignment" else k.asr_prompt_v
    return _fingerprint([
        k.telegram_id, k.audio_hash, k.clip_start, k.clip_end,
        k.asr_mode, k.ref_text_hash,
        k.stage1_model, asr_prompt, k.stage1b_prompt_v,
    ])


def _stage2_subs_fp(k: CacheKey) -> str:
    # Subtitles depend on stage1 outputs (deterministic from stage1 inputs).
    asr_prompt = k.forced_align_prompt_v if k.asr_mode == "forced_alignment" else k.asr_prompt_v
    return _fingerprint([
        k.telegram_id, k.audio_hash, k.clip_start, k.clip_end,
        k.asr_mode, k.ref_text_hash,
        k.stage1_model, asr_prompt,
        k.subtitles_mode, k.subtitles_model, k.subtitles_prompt_v,
    ])


def _stage2_timing_fp(k: CacheKey) -> str:
    # Timing depends on stage1 outputs + user_drop_t (affects hook-aware switch points).
    asr_prompt = k.forced_align_prompt_v if k.asr_mode == "forced_alignment" else k.asr_prompt_v
    return _fingerprint([
        k.telegram_id, k.audio_hash, k.clip_start, k.clip_end,
        k.asr_mode, k.ref_text_hash,
        k.stage1_model, asr_prompt,
        k.user_drop_t, k.timing_prompt_v,
    ])


def build_cache_key(
    *,
    telegram_id: str,
    audio_hash: str,
    clip_start_sec: Optional[float],
    clip_end_sec: Optional[float],
    asr_mode: str,
    lyrics_text: str,
    subtitles_mode: str,
    user_drop_t: Optional[float],
) -> CacheKey:
    """Build a CacheKey, reading model IDs and prompt versions from the environment."""
    def _fmt_sec(v: Optional[float]) -> str:
        return "none" if v is None else f"{float(v):.3f}"

    def _hash_text(t: str) -> str:
        if not t or not t.strip():
            return "none"
        return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]

    stage1_model = (os.environ.get("GEMINI_MODEL_STAGE1") or "").strip() or "unknown"
    stage1_asr_model = (os.environ.get("GEMINI_MODEL_STAGE1_ASR") or stage1_model).strip() or stage1_model
    subtitles_model = (os.environ.get("GEMINI_MODEL_SUBTITLES") or "").strip() or "unknown"

    return CacheKey(
        telegram_id=str(telegram_id or "anon").strip() or "anon",
        audio_hash=str(audio_hash or "").strip(),
        clip_start=_fmt_sec(clip_start_sec),
        clip_end=_fmt_sec(clip_end_sec),
        asr_mode=str(asr_mode or "asr").strip() or "asr",
        ref_text_hash=_hash_text(lyrics_text),
        subtitles_mode=str(subtitles_mode or "legacy_blocks").strip(),
        user_drop_t=_fmt_sec(user_drop_t),
        stage1_model=stage1_asr_model,
        subtitles_model=subtitles_model,
        asr_prompt_v=_prompt_version("mlcore.prompts.step1a_asr_only"),
        forced_align_prompt_v=_prompt_version("mlcore.prompts.step1a_forced_alignment"),
        stage1b_prompt_v=_prompt_version("mlcore.prompts.step1b_scenario_only"),
        subtitles_prompt_v=_subtitles_prompt_version(subtitles_mode),
        timing_prompt_v=_prompt_version("mlcore.prompts.stage2_timing_switches"),
    )


# ---------------------------------------------------------------------------
# S3 path helpers
# ---------------------------------------------------------------------------

def _s3_prefix() -> str:
    return (os.environ.get("LLM_CACHE_S3_PREFIX") or "llm_cache").strip().rstrip("/")


def _stage_s3_keys(k: CacheKey) -> Dict[str, str]:
    """Return {stage_name: s3_key} for each cacheable stage block."""
    prefix = _s3_prefix()
    tid = k.telegram_id
    ah = k.audio_hash
    base = f"{prefix}/{tid}/{ah}"
    return {
        "stage1_asr":    f"{base}/stage1_asr.{_stage1a_fp(k)}.json",
        "stage1_plan":   f"{base}/stage1_plan.{_stage1b_fp(k)}.json",
        "stage2_subs":   f"{base}/stage2_subs.{k.subtitles_mode}.{_stage2_subs_fp(k)}.json",
        "stage2_timing": f"{base}/stage2_timing.{_stage2_timing_fp(k)}.json",
    }


# ---------------------------------------------------------------------------
# S3 client
# ---------------------------------------------------------------------------

def _make_s3_client():
    import boto3
    from botocore.config import Config
    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"
    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        # proxies={}: S3 напрямую, мимо зарубежного OUTBOUND-прокси (до Timeweb 502).
        "config": Config(signature_version="s3v4", proxies={}),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def _cache_bucket() -> str:
    return (os.environ.get("LLM_CACHE_S3_BUCKET") or "").strip()


def _cache_enabled() -> bool:
    raw = (os.environ.get("LLM_CACHE_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cache_save_enabled() -> bool:
    raw = (os.environ.get("LLM_CACHE_SAVE_ENABLED") or "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return _cache_enabled()


# ---------------------------------------------------------------------------
# Stage key sets (what resume_state keys each stage "block" covers)
# ---------------------------------------------------------------------------

_STAGE_KEY_SETS: Dict[str, frozenset[str]] = {
    "stage1_asr":    _STAGE1_ASR_KEYS,
    "stage1_plan":   _STAGE1_PLAN_KEYS,
    "stage2_subs":   _STAGE2_SUBS_KEYS,
    "stage2_timing": _STAGE2_TIMING_KEYS,
}


# ---------------------------------------------------------------------------
# Redis lock
# ---------------------------------------------------------------------------

def _lock_ttl_s() -> int:
    raw = (os.environ.get("LLM_CACHE_LOCK_TTL_S") or "").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 900


def _lock_key(k: CacheKey) -> str:
    # Lock per (telegram_id, audio_hash) — coarse enough to prevent duplicates
    # without blocking unrelated requests.
    return f"llm_cache:lock:{k.telegram_id}:{k.audio_hash[:16]}"


def try_acquire_lock(redis_client: Any, cache_key: CacheKey) -> bool:
    """
    Attempt Redis SETNX lock. Returns True if acquired, False otherwise.
    On any Redis error: returns False (caller proceeds without lock, without saving).
    """
    key = _lock_key(cache_key)
    ttl = _lock_ttl_s()
    try:
        acquired = redis_client.set(key, "1", nx=True, ex=ttl)
        return bool(acquired)
    except Exception as exc:
        log.warning("llm_cache lock_acquire_failed key=%s err=%r", key, exc)
        return False


def release_lock(redis_client: Any, cache_key: CacheKey) -> None:
    key = _lock_key(cache_key)
    try:
        redis_client.delete(key)
    except Exception as exc:
        log.warning("llm_cache lock_release_failed key=%s err=%r", key, exc)


# ---------------------------------------------------------------------------
# Load from cache
# ---------------------------------------------------------------------------

def try_populate_resume_state(
    cache_key: CacheKey,
    resume_state_path: Path,
) -> Dict[str, bool]:
    """
    Try to load each cacheable stage from S3 and merge into resume_state_path.

    Returns a dict {stage_name: hit_bool} for observability.
    On any error (S3 unreachable, corrupt JSON, etc.) logs a warning and treats
    the stage as a miss — never raises.
    """
    if not _cache_enabled():
        return {}
    bucket = _cache_bucket()
    if not bucket:
        return {}

    hits: Dict[str, bool] = {}
    try:
        s3 = _make_s3_client()
    except Exception as exc:
        log.warning("llm_cache s3_client_failed err=%r", exc)
        return {}

    s3_keys = _stage_s3_keys(cache_key)

    # Load existing resume state (may be empty/missing on first run)
    existing: Dict[str, Any] = {}
    try:
        if resume_state_path.exists():
            existing = json.loads(resume_state_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
    except Exception:
        existing = {}

    merged = dict(existing)
    any_hit = False

    for stage_name, s3_key in s3_keys.items():
        stage_resume_keys = _STAGE_KEY_SETS[stage_name]
        # Skip if all keys for this stage already present in existing state
        if all(k in existing for k in stage_resume_keys):
            hits[stage_name] = True  # already populated (local resume state)
            continue
        try:
            resp = s3.get_object(Bucket=bucket, Key=s3_key)
            data: Dict[str, Any] = json.loads(resp["Body"].read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("cache blob is not a JSON object")
            # Only copy keys that belong to this stage; ignore any extras
            for resume_key in stage_resume_keys:
                if resume_key in data:
                    merged[resume_key] = data[resume_key]
            hits[stage_name] = True
            any_hit = True
            log.info(
                "llm_cache hit stage=%s tid=%s audio=%.8s",
                stage_name, cache_key.telegram_id, cache_key.audio_hash,
            )
        except Exception as exc:
            # NoSuchKey is normal (cache miss); log at debug to reduce noise
            code = ""
            resp_meta = getattr(exc, "response", None)
            if isinstance(resp_meta, dict):
                code = str((resp_meta.get("Error") or {}).get("Code") or "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                log.debug("llm_cache miss stage=%s", stage_name)
            else:
                log.warning("llm_cache load_failed stage=%s err=%r", stage_name, exc)
            hits[stage_name] = False

    if any_hit:
        try:
            resume_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = resume_state_path.with_suffix(resume_state_path.suffix + ".cache_tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(resume_state_path)
        except Exception as exc:
            log.warning("llm_cache resume_state_write_failed err=%r", exc)

    return hits


# ---------------------------------------------------------------------------
# Save to cache
# ---------------------------------------------------------------------------

def save_resume_state_to_cache(
    cache_key: CacheKey,
    resume_state_path: Path,
) -> None:
    """
    Read resume_state_path and upload each stage block to its S3 key.
    Never raises — on any error logs a warning and returns.
    Footage keys (stage2_footage) are NEVER included.
    """
    if not _cache_save_enabled():
        return
    bucket = _cache_bucket()
    if not bucket:
        return
    if not resume_state_path.exists():
        return

    try:
        state: Any = json.loads(resume_state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            log.warning("llm_cache save_skip: resume_state is not a dict")
            return
    except Exception as exc:
        log.warning("llm_cache save_skip: resume_state unreadable err=%r", exc)
        return

    try:
        s3 = _make_s3_client()
    except Exception as exc:
        log.warning("llm_cache s3_client_failed err=%r", exc)
        return

    s3_keys = _stage_s3_keys(cache_key)

    for stage_name, s3_key in s3_keys.items():
        stage_resume_keys = _STAGE_KEY_SETS[stage_name]
        # Only save if ALL keys for this stage are present — partial saves are useless
        payload = {k: state[k] for k in stage_resume_keys if k in state}
        if not payload:
            continue
        if len(payload) < len(stage_resume_keys):
            # Some keys missing — stage not fully computed, skip
            log.debug("llm_cache save_partial_skip stage=%s present=%d/%d",
                      stage_name, len(payload), len(stage_resume_keys))
            continue
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            s3.put_object(Bucket=bucket, Key=s3_key, Body=body, ContentType="application/json")
            log.info(
                "llm_cache saved stage=%s tid=%s audio=%.8s",
                stage_name, cache_key.telegram_id, cache_key.audio_hash,
            )
        except Exception as exc:
            log.warning("llm_cache save_failed stage=%s err=%r", stage_name, exc)
