# mlcore/gemini_orchestrator.py
from __future__ import annotations

import json
from json import JSONDecodeError
import os
import re
import subprocess
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
import logging
from pydantic import ValidationError

from mlcore.gemini_call import (
    call_footage_style_once,
    call_stage1_asr_once,
    call_stage1_forced_alignment_once,
    call_subtitles_plan_model_once,
    call_stage1_scenario_once,
    call_timing_analysis_once,
    call_timing_cuts_once,
    call_subtitles_plan_once,
    pick_audio_files,
)
from mlcore.audio_bpm import detect_bpm_librosa
from mlcore.gemini_client import GeminiClient, GeminiSettings
from mlcore.llm_router import (
    PROVIDER_MODE_GEMINI,
    PROVIDER_MODE_HEDGED,
    PROVIDER_MODE_OPENROUTER,
    normalize_provider_mode,
)
from mlcore.openrouter_client import OpenRouterClient, OpenRouterSettings
from mlcore.footage_picker import (
    FootageIntervalPickerDiagnostics,
    FootageStyleRawAdapterDiagnostics,
    build_style_groups_from_assets,
    build_intervals_from_switch_points,
    load_picker_assets_from_inventory,
    load_footage_style_metadata_rows,
    map_inventory_assets_with_style_metadata,
    merge_footage_style_metadata_rows,
    pick_footage_clips_by_intervals_deterministic,
    resolve_style_pick_from_raw_filters,
    validate_style_pick_in_groups,
)
from mlcore.gemini_postprocess import render_all_steps
from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload, FootageStyleRawPayload, FootageStyleRotation
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.stage1_asr import Stage1AsrPayload, Stage1AsrSelectedFragment
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload, parse_forced_timecode_mmss_mmm
from mlcore.models.stage1_plan import FragmentAnalytics, PauseSpan, Stage1PlanPayload
from mlcore.models.stage1_plan import TranscriptWord
from mlcore.models.stage1_scenario import Stage1ScenarioPayload
from mlcore.models.subtitles_spans import BlocksTokenSpansPayload, TokenSpan
from mlcore.models.subtitles_flow import SubtitleFlowPlan
from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.subtitles_flow import SubtitlesPlannerFactory
from mlcore.models.switch_timing import (
    Stage2TimingAnalysisPayload,
    Stage2TimingCutsPayload,
    SwitchTimingPayload,
    normalize_switch_points,
)
from mlcore.prompts import (
    build_stage1a_asr_system_instruction,
    build_stage1a_asr_user_prompt,
    build_stage1a_forced_alignment_system_instruction,
    build_stage1a_forced_alignment_user_prompt,
    build_stage1b_scenario_system_instruction,
    build_stage1b_scenario_user_prompt,
    build_stage2_footage_system_instruction,
    build_stage2_footage_user_prompt,
    build_stage2_subtitles_system_instruction,
    build_stage2_subtitles_user_prompt,
    build_stage2_timing_analysis_system_instruction,
    build_stage2_timing_analysis_user_prompt,
    build_stage2_timing_cuts_system_instruction,
    build_stage2_timing_cuts_user_prompt,
)
from mlcore.stage1_tools import align_stage1_draft_to_transcript, build_stage1_report
from core.subtitles_mode import (
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    normalize_subtitles_mode,
)
from core.llm_worker_types import (
    LLM_WORKER_TYPE_SDK,
    LLM_WORKER_TYPE_VERTEX_SDK_MIX,
    normalize_llm_worker_type,
)
from core.runtime_mode import get_runtime_mode, MODE_DEV, MODE_PROD
from core.clip_window import (
    CLIP_WINDOW_MAX_LABEL,
    CLIP_WINDOW_MAX_SECONDS,
    CLIP_WINDOW_MIN_LABEL,
    CLIP_WINDOW_MIN_SECONDS,
)


ROOT = Path(__file__).resolve().parent.parent
MODEL_VALIDATION_IMMEDIATE_RETRIES = 2
_STRUCTURAL_TAG_TOKEN_RE = re.compile(r"^\[[a-zа-яё0-9_\-:+./]+\]$", flags=re.IGNORECASE)
_SCENES_3RD_SINGLE_STEP_MODEL = "gemini-2.5-pro"

# Stage2 timing mode. This is a code-level switch we control via git, NOT a
# secret and NOT read from env — flipping it is a deploy, not an .env edit.
# Allowed: "prompts" | "hybrid" | "hook_aware". See `mlcore.audio_analysis`
# and the SYSTEM_HOOK_AWARE prompt block for the hook_aware contract.
STAGE2_TIMING_MODE = "hook_aware"


class _Stage1AUserClipEmptyError(RuntimeError):
    """Forced alignment missed the explicit user clip window; safe to retry Stage1A."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_resume_state(path: Optional[Path], logger: logging.Logger) -> Dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("llm_resume_state_read_failed path=%s err=%s", str(path), str(e))
        return {}
    if not isinstance(obj, dict):
        logger.warning("llm_resume_state_invalid_root path=%s", str(path))
        return {}
    return obj


def _save_resume_state(path: Optional[Path], logger: logging.Logger, state: Dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info("llm_resume_state_saved path=%s keys=%s", str(path), sorted(state.keys()))


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("mlcore.gemini_orchestrator")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        logger.propagate = False
        fmt = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")

        log_dir = ROOT / "ml_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        file_path = log_dir / f"orchestrator_staged_{_stamp()}.log"
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        logger.info("logger_ready file=%s", str(file_path))

    return logger


def _load_footage_inventory(inv_path: Path) -> Dict[str, Any]:
    d = json.loads(inv_path.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError(f"Invalid inventory JSON: {inv_path}")
    if not isinstance(d.get("assets"), list):
        raise ValueError(f"Inventory must contain 'assets': {inv_path}")
    return d


def _require_model(key: str) -> str:
    v = (os.environ.get(key) or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _seed_from_key_material(seed_key: str) -> int:
    d = hashlib.sha256(seed_key.encode("utf-8")).digest()
    return int.from_bytes(d[:8], byteorder="big", signed=False)


def _resolve_footage_seed_key(*, out_dir: Path, logger: logging.Logger) -> str:
    """
    Deterministic seed priority:
      1) STAGE2_SELECTION_SEED (explicit override)
      2) JOB_ID
      3) OUT_DIR absolute path
    """
    key = (os.environ.get("STAGE2_SELECTION_SEED") or "").strip()
    source = "STAGE2_SELECTION_SEED"
    if not key:
        key = (os.environ.get("JOB_ID") or "").strip()
        source = "JOB_ID"
    if not key:
        key = str(out_dir.resolve())
        source = "OUT_DIR"
    seed_value = _seed_from_key_material(key)
    logger.info(
        "footage_seed_resolved source=%s key=%s seed=%d",
        source,
        key,
        seed_value,
    )
    return key


def _resolve_style_metadata_db_paths(*, root: Path) -> List[Path]:
    raw = (os.environ.get("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"Invalid FOOTAGE_STYLE_METADATA_DB_PATHS_JSON: {e!r}") from e
        if not isinstance(parsed, list) or not parsed:
            raise RuntimeError("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON must be a non-empty JSON list")
        paths = [Path(str(p)).expanduser().resolve() for p in parsed]
    else:
        paths = [
            (root / "2nd_footage_selection_prompt" / "video_database (2).json").resolve(),
            (root / "2nd_footage_selection_prompt" / "video_database2.json").resolve(),
        ]

    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Style metadata db files missing: {missing}")
    return paths


def _make_client(
    *,
    api_key: str,
    model: str,
    fallback_model: Optional[str],
    proxy: str,
    temperature: float,
    timeout_s: float,
    logger: logging.Logger,
    max_output_tokens: Optional[int] = None,
    max_thinking_tokens: Optional[int] = None,
    vertexai: bool = False,
    vertex_project: Optional[str] = None,
    vertex_location: Optional[str] = None,
) -> GeminiClient:
    return GeminiClient(
        GeminiSettings(
            api_key=api_key,
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
            proxy=proxy,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            max_attempts=1,
            vertexai=bool(vertexai),
            vertex_project=vertex_project,
            vertex_location=vertex_location,
        ),
        logger=logger,
    )


def _make_openrouter_client(
    *,
    api_key: str,
    model: str,
    temperature: float,
    timeout_s: float,
    logger: logging.Logger,
) -> OpenRouterClient:
    return OpenRouterClient(
        OpenRouterSettings(
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout_s=timeout_s,
        ),
        logger=logger,
    )


def _float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid {name}: {raw!r}") from e


def _optional_int_env(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid {name}: {raw!r}") from e
    if v <= 0:
        raise RuntimeError(f"{name} must be > 0, got {v!r}")
    return v


def _require_float_env(name: str) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        raise RuntimeError(f"Missing required env var: {name}")
    try:
        return float(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid {name}: {raw!r}") from e


def _require_choice_env(name: str, *, allowed: List[str]) -> str:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        raise RuntimeError(f"Missing required env var: {name}")
    if raw not in allowed:
        raise RuntimeError(f"Invalid {name}: {raw!r}; allowed={allowed}")
    return raw


def _optional_user_clip_window_from_env(*, logger: logging.Logger) -> Optional[Tuple[float, float]]:
    start_raw = (os.environ.get("USER_CLIP_START_SEC") or "").strip()
    end_raw = (os.environ.get("USER_CLIP_END_SEC") or "").strip()
    if not start_raw and not end_raw:
        return None
    if not start_raw or not end_raw:
        raise RuntimeError("USER_CLIP_START_SEC and USER_CLIP_END_SEC must be set together")
    try:
        start = float(start_raw)
        end = float(end_raw)
    except Exception as e:
        raise RuntimeError(
            f"Invalid USER_CLIP_START_SEC/USER_CLIP_END_SEC: {start_raw!r}/{end_raw!r}"
        ) from e
    if start < 0.0:
        raise RuntimeError(f"USER_CLIP_START_SEC must be >= 0 (got {start})")
    if end <= start:
        raise RuntimeError(f"USER_CLIP_END_SEC must be > USER_CLIP_START_SEC (got {start}..{end})")
    dur = float(end - start)
    logger.info(
        "user_clip_window_input start=%.3f end=%.3f dur=%.3f",
        float(start),
        float(end),
        float(dur),
    )
    return float(start), float(end)


def _openrouter_model_from_gemini(gemini_model: str) -> str:
    model = (gemini_model or "").strip()
    if not model:
        raise RuntimeError("Gemini model is empty")
    if "/" in model:
        raise RuntimeError(
            "OpenRouter auto-mapping requires bare Gemini model id without '/': "
            f"got {model!r}"
        )
    return f"google/{model}"


def _emit(progress_cb: Optional[Callable[[str], None]], stage: str) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(stage)
    except Exception:
        pass


def _hybrid_fast_start_switch_points(
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    fast_start_seconds: float,
    bpm: float,
) -> List[float]:
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    if ce <= cs + 1e-6:
        return []

    fast_end = min(ce, cs + max(0.0, float(fast_start_seconds)))
    if fast_end <= cs + 1e-6:
        return []

    # Keep dense but stable cadence in [0.5 .. 1.5] sec.
    period = 60.0 / float(bpm)
    period = min(1.5, max(0.5, period))

    out: List[float] = []
    t = cs + period
    while t < fast_end - 1e-6:
        out.append(float(t))
        t += period
    return out


def _norm_compact(s: str) -> str:
    return " ".join(str(s or "").split())


def _normalize_forced_word_compare(token: str) -> str:
    t = str(token or "").lower().replace("ё", "е")
    t = re.sub(r"^[^\w]+|[^\w]+$", "", t, flags=re.UNICODE)
    return t.strip()


def _token_probe_for_structural_tag(raw_token: str) -> str:
    return str(raw_token or "").strip().strip(",.!?;:\"'«»(){}")


def _is_structural_tag_token(raw_token: str) -> bool:
    probe = _token_probe_for_structural_tag(raw_token)
    if not probe:
        return False
    return bool(_STRUCTURAL_TAG_TOKEN_RE.fullmatch(probe))


def _strip_structural_tags_from_text(text: str) -> tuple[str, int]:
    kept: List[str] = []
    dropped = 0
    for raw in str(text or "").replace("\r", " ").split():
        if _is_structural_tag_token(raw):
            dropped += 1
            continue
        kept.append(raw)
    return " ".join(kept), dropped


def _reference_words_from_user_text(text: str) -> List[str]:
    out: List[str] = []
    for raw in str(text or "").replace("\r", " ").split():
        if _is_structural_tag_token(raw):
            continue
        w = re.sub(r"^[^\w]+|[^\w]+$", "", raw, flags=re.UNICODE).strip()
        if w:
            out.append(w)
    return out


def _stage1a_pause_min_gap_sec() -> float:
    try:
        v = float(os.environ.get("STAGE1A_PAUSE_MIN_GAP_S", "1.0"))
    except Exception:
        v = 1.0
    return max(0.1, float(v))


def _derive_pause_spans_from_aligned_words(
    *,
    aligned_words: List[Any],
    min_gap_sec: float,
) -> List[Dict[str, float | str]]:
    out: List[Dict[str, float | str]] = []
    if len(aligned_words) < 2:
        return out

    for i in range(len(aligned_words) - 1):
        cur = aligned_words[i]
        nxt = aligned_words[i + 1]
        try:
            cur_end = float(getattr(cur, "t_end_sec"))
            next_start = float(getattr(nxt, "t_start_sec"))
        except Exception:
            try:
                cur_end = parse_forced_timecode_mmss_mmm(str(getattr(cur, "t_end")))
                next_start = parse_forced_timecode_mmss_mmm(str(getattr(nxt, "t_start")))
            except Exception:
                continue
        gap = next_start - cur_end
        if gap > float(min_gap_sec) + 1e-6:
            out.append(
                {
                    "text": "[pause]",
                    "t_start": float(cur_end),
                    "t_end": float(next_start),
                }
            )
    return out


def _pause_spans_in_window(
    *,
    pause_spans: List[PauseSpan],
    start_abs: float,
    end_abs: float,
) -> List[PauseSpan]:
    out: List[PauseSpan] = []
    for p in pause_spans:
        ps = float(p.t_start)
        pe = float(p.t_end)
        if ps >= float(start_abs) - 1e-6 and pe <= float(end_abs) + 1e-6:
            out.append(p)
    return out


def _validate_forced_alignment_payload(
    *,
    payload: Stage1ForcedAlignmentPayload,
    reference_words: List[str],
    logger: logging.Logger,
) -> None:
    if len(payload.aligned_words) != len(reference_words):
        logger.warning(
            "stage1a_forced_word_count_mismatch got=%d expected=%d (continuing)",
            len(payload.aligned_words),
            len(reference_words),
        )
    mismatch_count = 0
    non_monotonic_count = 0
    prev_start = -1.0
    prev_end = -1.0

    for idx, got in enumerate(payload.aligned_words):
        expected = reference_words[idx] if idx < len(reference_words) else ""
        got_norm = _normalize_forced_word_compare(got.text)
        exp_norm = _normalize_forced_word_compare(expected) if expected else ""
        if not got_norm:
            logger.warning("stage1a_forced_empty_word idx=%d got=%r (continuing)", idx, got.text)
            mismatch_count += 1
        elif exp_norm and got_norm != exp_norm:
            logger.warning(
                "stage1a_forced_text_mismatch idx=%d got=%r expected=%r (continuing)",
                idx,
                got.text,
                expected,
            )
            mismatch_count += 1

        ts = float(got.t_start_sec)
        te = float(got.t_end_sec)
        if idx > 0 and ts < prev_start:
            logger.warning(
                "stage1a_forced_non_monotonic_t_start idx=%d ts=%s prev_start=%s (continuing)",
                idx,
                ts,
                prev_start,
            )
            non_monotonic_count += 1
        if idx > 0 and te < prev_end:
            logger.warning(
                "stage1a_forced_non_monotonic_t_end idx=%d te=%s prev_end=%s (continuing)",
                idx,
                te,
                prev_end,
            )
            non_monotonic_count += 1
        prev_start = ts
        prev_end = te

    if mismatch_count > 0:
        logger.warning("stage1a_forced_text_mismatch_total count=%d (continuing)", mismatch_count)
    if non_monotonic_count > 0:
        logger.warning("stage1a_forced_non_monotonic_total count=%d (continuing)", non_monotonic_count)

    min_gap_sec = _stage1a_pause_min_gap_sec()
    if payload.pause_spans:
        short_count = 0
        for idx, p in enumerate(payload.pause_spans):
            dur = float(p.t_end_sec) - float(p.t_start_sec)
            if dur + 1e-6 < min_gap_sec:
                logger.warning(
                    "stage1a_forced_pause_short idx=%d dur=%.3f threshold=%.3f (continuing)",
                    idx,
                    dur,
                    min_gap_sec,
                )
                short_count += 1
        if short_count > 0:
            logger.warning("stage1a_forced_pause_short_total count=%d (continuing)", short_count)

    derived = _derive_pause_spans_from_aligned_words(
        aligned_words=list(payload.aligned_words),
        min_gap_sec=min_gap_sec,
    )
    if derived and not payload.pause_spans:
        logger.warning(
            "stage1a_forced_pause_spans_missing expected=%d threshold=%.3f action=derive_postprocess",
            len(derived),
            min_gap_sec,
        )
    logger.info(
        "stage1a_forced_pause_spans counts payload=%d derived=%d threshold=%.3f",
        len(payload.pause_spans),
        len(derived),
        min_gap_sec,
    )


def _stage1_asr_from_forced_alignment(
    payload: Stage1ForcedAlignmentPayload,
    *,
    logger: Optional[logging.Logger] = None,
) -> Stage1AsrPayload:
    logger = logger or logging.getLogger(__name__)
    min_gap_sec = _stage1a_pause_min_gap_sec()
    transcript_words = [
        {
            "text": str(w.text),
            "t_start": float(w.t_start_sec),
            "t_end": float(w.t_end_sec),
        }
        for w in payload.aligned_words
    ]
    pause_spans = [
        {
            "text": "[pause]",
            "t_start": float(p.t_start_sec),
            "t_end": float(p.t_end_sec),
        }
        for p in payload.pause_spans
    ]
    if not pause_spans:
        pause_spans = _derive_pause_spans_from_aligned_words(
            aligned_words=list(payload.aligned_words),
            min_gap_sec=min_gap_sec,
        )
    out: Dict[str, Any] = {
        "transcript_words": transcript_words,
        "pause_spans": pause_spans,
        "srt_items": [],
    }
    if payload.selected_fragment is not None:
        sf = payload.selected_fragment
        selected_audio = sf.audio
        selected_obj: Dict[str, Any] = {
            "audio": {
                "clip_start_abs": float(selected_audio.clip_start_abs_sec),
                "clip_end_abs": float(selected_audio.clip_end_abs_sec),
                "moment_of_interest_sec": (
                    float(selected_audio.moment_of_interest_sec_value)
                    if selected_audio.moment_of_interest_sec_value is not None
                    else None
                ),
            },
            # Filter out boundary items that start before clip_start or end
            # after clip_end — they may pass the overlap-based validator but
            # must not appear in subtitles.
            "transcript_words": [
                {
                    "text": str(w.text),
                    "t_start": float(w.t_start_sec),
                    "t_end": float(w.t_end_sec),
                }
                for w in sf.transcript_words
                if float(w.t_start_sec) >= float(selected_audio.clip_start_abs_sec) - 1e-6
                and float(w.t_end_sec) <= float(selected_audio.clip_end_abs_sec) + 1e-6
            ],
            "pause_spans": [
                {
                    "text": "[pause]",
                    "t_start": float(p.t_start_sec),
                    "t_end": float(p.t_end_sec),
                }
                for p in sf.pause_spans
                if float(p.t_start_sec) >= float(selected_audio.clip_start_abs_sec) - 1e-6
                and float(p.t_end_sec) <= float(selected_audio.clip_end_abs_sec) + 1e-6
            ],
            "srt_items": [
                {
                    "start": float(it.start_sec),
                    "end": float(it.end_sec),
                    "text": str(it.text),
                }
                for it in sf.srt_items
                if float(it.start_sec) >= float(selected_audio.clip_start_abs_sec) - 1e-6
                and float(it.end_sec) <= float(selected_audio.clip_end_abs_sec) + 1e-6
            ],
            "fragment_analytics": (
                sf.fragment_analytics.model_dump(mode="json")
                if sf.fragment_analytics is not None
                else None
            ),
        }
        if not selected_obj.get("pause_spans"):
            selected_obj["pause_spans"] = [
                {
                    "text": "[pause]",
                    "t_start": float(p.t_start),
                    "t_end": float(p.t_end),
                }
                for p in _pause_spans_in_window(
                    pause_spans=[
                        PauseSpan.model_validate(p)
                        for p in pause_spans
                    ],
                    start_abs=float(selected_audio.clip_start_abs_sec),
                    end_abs=float(selected_audio.clip_end_abs_sec),
                )
            ]

        # Expand short clip to meet minimum duration requirement.
        audio_d = selected_obj["audio"]
        cs = float(audio_d["clip_start_abs"])
        ce = float(audio_d["clip_end_abs"])
        clip_dur = ce - cs
        if clip_dur < CLIP_WINDOW_MIN_SECONDS - 1e-6:
            need = CLIP_WINDOW_MIN_SECONDS - clip_dur
            # left_room is bounded by 0; right side is unbounded because the
            # audio file extends past the last transcribed word.
            left_room = max(0.0, cs)
            add_left = min(left_room, need / 2.0)
            add_right = need - add_left
            new_start = cs - add_left
            new_end = ce + add_right
            audio_d["clip_start_abs"] = float(new_start)
            audio_d["clip_end_abs"] = float(new_end)
            moi = audio_d.get("moment_of_interest_sec")
            if moi is not None and (float(moi) < new_start or float(moi) > new_end):
                audio_d["moment_of_interest_sec"] = float(new_start + (new_end - new_start) / 2.0)
            # Re-derive fragment content from full transcript for expanded window.
            full_words = [TranscriptWord.model_validate(w) for w in transcript_words]
            full_pauses = [PauseSpan.model_validate(p) for p in pause_spans]
            expanded_words = _words_in_window(words=full_words, start_abs=new_start, end_abs=new_end)
            expanded_pauses = _pause_spans_in_window(pause_spans=full_pauses, start_abs=new_start, end_abs=new_end)
            if expanded_words:
                selected_obj["transcript_words"] = [
                    {"text": str(w.text), "t_start": float(w.t_start), "t_end": float(w.t_end)}
                    for w in expanded_words
                ]
            if expanded_pauses:
                selected_obj["pause_spans"] = [
                    {"text": "[pause]", "t_start": float(p.t_start), "t_end": float(p.t_end)}
                    for p in expanded_pauses
                ]
            logger.warning(
                "stage1a_selected_fragment_short_clip_expanded "
                "original=%.3f..%.3f dur=%.3f expanded=%.3f..%.3f dur=%.3f",
                cs, ce, clip_dur, new_start, new_end, float(new_end - new_start),
            )

        out["selected_fragment"] = selected_obj
    return Stage1AsrPayload.model_validate(out)


def _ffprobe_duration_sec(*, media_path: Path, ffprobe_bin: str = "ffprobe") -> Optional[float]:
    if not media_path.exists():
        return None
    try:
        cmd = [
            str(ffprobe_bin),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None
        raw = str(proc.stdout or "").strip()
        if not raw:
            return None
        v = float(raw)
        if v <= 0.0:
            return None
        return float(v)
    except Exception:
        return None


def _transcribed_duration_sec(stage1_asr: Stage1AsrPayload) -> Optional[float]:
    if not stage1_asr.transcript_words:
        return None
    max_end = 0.0
    for w in stage1_asr.transcript_words:
        te = float(w.t_end)
        if te > max_end:
            max_end = te
    if max_end <= 0.0:
        return None
    return float(max_end)


def _should_retry_stage1a_duration_drift(
    *,
    reference_words_count: int,
    fact_dur: float,
    transcribed_dur: float,
) -> bool:
    # Enabled only for long forced-reference texts to avoid false positives on short quotes.
    try:
        min_words = int(os.environ.get("STAGE1A_DURATION_DRIFT_MIN_WORDS", "80"))
    except Exception:
        min_words = 80
    if reference_words_count < max(1, min_words):
        return False

    try:
        abs_threshold = float(os.environ.get("STAGE1A_DURATION_DRIFT_ABS_SEC", "12.0"))
    except Exception:
        abs_threshold = 12.0
    try:
        rel_threshold = float(os.environ.get("STAGE1A_DURATION_DRIFT_REL", "0.20"))
    except Exception:
        rel_threshold = 0.20

    drift = abs(float(fact_dur) - float(transcribed_dur))
    limit = max(float(abs_threshold), float(fact_dur) * max(0.0, float(rel_threshold)))
    return drift > limit


def _build_stage1a_duration_rework_hint(
    *,
    fact_dur: float,
    transcribed_dur: float,
    transcribe_attempt_1: Dict[str, Any],
) -> str:
    return (
        "\n\nTRANSCRIBE_REWORK=ON\n"
        "You must correct timing drift from TRANSCRIBE_ATTEMPT_1.\n"
        "audio: attached source track (same file)\n"
        "comment:\n"
        + f"- fact_dur={float(fact_dur):.3f}\n"
        + f"- transcribed_dur={float(transcribed_dur):.3f}\n"
        + f"- delta={abs(float(fact_dur) - float(transcribed_dur)):.3f}\n"
        "Hard correction rules:\n"
        "- Keep lexical content and order identical to REFERENCE_TEXT constraints.\n"
        "- Re-align timings to full-track timeline consistency.\n"
        "- Preserve all schema requirements.\n"
        "TRANSCRIBE_ATTEMPT_1_JSON:\n"
        + json.dumps(transcribe_attempt_1, ensure_ascii=False)
        + "\n"
    )


def _timecode_ms_part(value: str) -> int:
    raw = str(value or "").strip()
    if "." not in raw:
        return -1
    try:
        ms_raw = raw.rsplit(".", 1)[1]
        if len(ms_raw) != 3:
            return -1
        return int(ms_raw)
    except Exception:
        return -1


def _analyze_stage1a_timecode_precision(
    *,
    payload: Stage1ForcedAlignmentPayload,
) -> Dict[str, Any]:
    words = list(payload.aligned_words or [])
    if not words:
        return {
            "words": 0,
            "points": 0,
            "unique_ms": 0,
            "quantized_50_ratio": 0.0,
            "zero_ms_ratio": 0.0,
            "mode_duration_ms": 0,
            "mode_duration_share": 0.0,
            "unique_durations": 0,
            "suspicious": False,
            "reasons": [],
        }

    ms_parts: List[int] = []
    dur_ms: List[int] = []
    for w in words:
        s_ms = _timecode_ms_part(str(w.t_start))
        e_ms = _timecode_ms_part(str(w.t_end))
        if s_ms >= 0:
            ms_parts.append(s_ms)
        if e_ms >= 0:
            ms_parts.append(e_ms)
        try:
            d = max(1, int(round((float(w.t_end_sec) - float(w.t_start_sec)) * 1000.0)))
            dur_ms.append(d)
        except Exception:
            continue

    points = len(ms_parts)
    if points <= 0 or not dur_ms:
        return {
            "words": len(words),
            "points": points,
            "unique_ms": 0,
            "quantized_50_ratio": 0.0,
            "zero_ms_ratio": 0.0,
            "mode_duration_ms": 0,
            "mode_duration_share": 0.0,
            "unique_durations": 0,
            "suspicious": False,
            "reasons": [],
        }

    quantized_50 = sum(1 for x in ms_parts if x % 50 == 0)
    zero_ms = sum(1 for x in ms_parts if x == 0)
    unique_ms = len(set(ms_parts))

    dur_hist = Counter(dur_ms)
    mode_duration_ms, mode_duration_count = dur_hist.most_common(1)[0]
    mode_duration_share = float(mode_duration_count) / float(max(1, len(dur_ms)))
    unique_durations = len(dur_hist)

    reasons: List[str] = []
    if (float(quantized_50) / float(max(1, points))) >= 0.90 and unique_ms <= 8:
        reasons.append("coarse_50ms_grid")
    if (float(zero_ms) / float(max(1, points))) >= 0.55:
        reasons.append("too_many_xxx_000")
    if mode_duration_share >= 0.70 and unique_durations <= 6:
        reasons.append("duration_histogram_too_peaked")

    return {
        "words": len(words),
        "points": points,
        "unique_ms": unique_ms,
        "quantized_50_ratio": float(quantized_50) / float(max(1, points)),
        "zero_ms_ratio": float(zero_ms) / float(max(1, points)),
        "mode_duration_ms": int(mode_duration_ms),
        "mode_duration_share": float(mode_duration_share),
        "unique_durations": int(unique_durations),
        "suspicious": bool(reasons),
        "reasons": reasons,
    }


def _should_retry_stage1a_suspicious_precision(
    *,
    reference_words_count: int,
    precision_diag: Dict[str, Any],
) -> bool:
    try:
        min_words = int(os.environ.get("STAGE1A_PRECISION_REWORK_MIN_WORDS", "40"))
    except Exception:
        min_words = 40
    if reference_words_count < max(1, min_words):
        return False
    return bool(precision_diag.get("suspicious"))


def _build_stage1a_precision_rework_hint(
    *,
    precision_diag: Dict[str, Any],
    transcribe_attempt_1: Dict[str, Any],
    target_fragment: str,
) -> str:
    target = str(target_fragment or "").strip()
    target_block = ""
    if target:
        target_block = (
            "TARGET_FRAGMENT_EXAMPLE:\n"
            + target
            + "\n"
            "Use fine-grained per-word boundaries around this fragment. "
            "Example format: 01:43.127 -> 01:43.386 (not coarse 01:43.000 -> 01:43.250).\n"
        )
    return (
        "\n\nTIMECODE_PRECISION_REWORK=ON\n"
        "PREVIOUS_ATTEMPT_WARNING:\n"
        "- Timing looks suspiciously rounded/coarse.\n"
        "- НЕ ЛЕНИСЬ: align every timestamp to real spoken boundaries in audio.\n"
        "- Keep mm:ss.mmm with EXACTLY 3 digits after dot in every time field.\n"
        "- Do not quantize timestamps to coarse buckets (.000/.050/.100/.250/etc.) unless acoustically exact.\n"
        "PRECISION_DIAGNOSTICS:\n"
        + json.dumps(precision_diag, ensure_ascii=False)
        + "\n"
        + target_block
        + "TRANSCRIBE_ATTEMPT_1_JSON:\n"
        + json.dumps(transcribe_attempt_1, ensure_ascii=False)
        + "\n"
    )


def _words_in_window(
    *,
    words: List[TranscriptWord],
    start_abs: float,
    end_abs: float,
) -> List[TranscriptWord]:
    out: List[TranscriptWord] = []
    for w in words:
        ts = float(w.t_start)
        te = float(w.t_end)
        if ts >= float(start_abs) - 1e-6 and te <= float(end_abs) + 1e-6:
            out.append(w)
    return out


def _ensure_stage1a_user_clip_has_words(
    *,
    stage1_asr: Stage1AsrPayload,
    user_clip_window: Optional[Tuple[float, float]],
) -> None:
    if user_clip_window is None:
        return
    user_start, user_end = user_clip_window
    frag_words = _words_in_window(
        words=list(stage1_asr.transcript_words),
        start_abs=float(user_start),
        end_abs=float(user_end),
    )
    if frag_words:
        return
    last_end = _transcribed_duration_sec(stage1_asr)
    raise _Stage1AUserClipEmptyError(
        "stage1a_user_clip_empty: user clip window has no transcript words "
        f"in range {float(user_start):.3f}..{float(user_end):.3f}; "
        f"asr_words={len(stage1_asr.transcript_words)} "
        f"asr_last_end={last_end if last_end is not None else 'none'}"
    )


def _fallback_draft_blocks_from_words(words: List[str]) -> Dict[str, Any]:
    clean = [str(w).strip() for w in words if str(w).strip()]
    if not clean:
        clean = ["word"]

    # Keep deterministic placeholders for non-legacy flow where draft_blocks are not consumed.
    # We still need a valid Stage1PlanPayload contract for shared pipeline interfaces.
    total = 13
    phrases: List[str] = []
    cursor = 0
    for i in range(total):
        remaining_parts = total - i
        remaining_words = max(0, len(clean) - cursor)
        take = max(1, remaining_words // remaining_parts) if remaining_parts > 0 else 1
        chunk = clean[cursor: cursor + take]
        if not chunk:
            chunk = [clean[-1]]
        phrases.append(" ".join(chunk))
        cursor = min(len(clean), cursor + take)

    mine_word = phrases[9].split(" ")[0]
    return {
        "block_1": {"phrases": [phrases[0]]},
        "block_2": {"p1": {"phrases": [phrases[1]]}, "p2": {"phrases": [phrases[2]]}},
        "block_3": {"phrases": [phrases[3]]},
        "block_4": {"p1": {"phrases": [phrases[4]]}, "p2": {"phrases": [phrases[5]]}},
        "block_5": {
            "slowly_in": {"phrases": [phrases[6]]},
            "fast_reveal": {"phrases": [phrases[7]]},
            "glitch_peak": {"phrases": [phrases[8]]},
            "mine": {"phrases": [mine_word]},
        },
        "block_6": {"phrases": [phrases[10]]},
        "block_7": {"part1": {"phrases": [phrases[11]]}, "part2": {"phrases": [phrases[12]]}},
    }


def _warn_stage1_clip_over_max(
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    logger: logging.Logger,
    source: str,
) -> None:
    dur = float(clip_end_abs) - float(clip_start_abs)
    if dur <= CLIP_WINDOW_MAX_SECONDS + 1e-6:
        return
    logger.warning(
        "stage1_clip_duration_over_max source=%s clip=%.3f..%.3f dur=%.3f max=%s action=kept_no_narrowing",
        source,
        float(clip_start_abs),
        float(clip_end_abs),
        float(dur),
        CLIP_WINDOW_MAX_LABEL,
    )


def _maybe_shift_clip_window_for_leading_silence(
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    selected_words: List[TranscriptWord],
    logger: logging.Logger,
) -> Tuple[float, float]:
    """
    If selected fragment starts with a long silence/music lead-in,
    shift the whole clip window so first word starts ~0.5s after clip start.

    Rule (explicit, deterministic):
      - apply only when lead-in is in [1.0s .. 2.0s]
      - keep shorter lead-ins unchanged
      - shift both start/end by the same delta
    """
    if not selected_words:
        return float(clip_start_abs), float(clip_end_abs)

    first_word_start = min(float(w.t_start) for w in selected_words)
    lead_s = float(first_word_start) - float(clip_start_abs)

    lead_min_s = 1.0
    lead_max_s = 2.0
    target_preroll_s = 0.5

    if lead_s < lead_min_s - 1e-6 or lead_s > lead_max_s + 1e-6:
        return float(clip_start_abs), float(clip_end_abs)

    target_start = max(0.0, float(first_word_start) - target_preroll_s)
    shift_s = float(target_start) - float(clip_start_abs)
    if shift_s <= 1e-6:
        return float(clip_start_abs), float(clip_end_abs)

    new_start = float(clip_start_abs) + shift_s
    new_end = float(clip_end_abs) + shift_s
    logger.info(
        "stage1_leading_silence_trim_applied clip=%.3f..%.3f lead=%.3f first_word=%.3f "
        "target_preroll=%.3f shift=%.3f result=%.3f..%.3f",
        float(clip_start_abs),
        float(clip_end_abs),
        float(lead_s),
        float(first_word_start),
        float(target_preroll_s),
        float(shift_s),
        float(new_start),
        float(new_end),
    )
    return float(new_start), float(new_end)


def _build_stage1_plan_from_selected_fragment(
    *,
    stage1_asr: Stage1AsrPayload,
    selected: Stage1AsrSelectedFragment,
    target_fragment: str,
    logger: logging.Logger,
) -> Stage1PlanPayload:
    audio_obj = selected.audio.model_dump(mode="json")
    fragment_analytics = selected.fragment_analytics

    if target_fragment:
        forced_start, forced_end = _validate_fragment_analytics_for_target(
            target_fragment=target_fragment,
            audio_start_abs=float(selected.audio.clip_start_abs),
            audio_end_abs=float(selected.audio.clip_end_abs),
            analytics=fragment_analytics,
            logger=logger,
        )
        # Keep clip deterministic in target-fragment branch.
        audio_obj["clip_start_abs"] = float(forced_start)
        audio_obj["clip_end_abs"] = float(forced_end)

    selected_words = list(selected.transcript_words)
    selected_pauses = list(selected.pause_spans)
    if not selected_words:
        logger.warning(
            "stage1a_selected_fragment_empty_words fallback=full_transcript_window clip=%s..%s full_words=%d",
            audio_obj["clip_start_abs"],
            audio_obj["clip_end_abs"],
            len(stage1_asr.transcript_words),
        )
        selected_words = _words_in_window(
            words=list(stage1_asr.transcript_words),
            start_abs=float(audio_obj["clip_start_abs"]),
            end_abs=float(audio_obj["clip_end_abs"]),
        )
    if not selected_words:
        raise ValueError("selected_fragment produced empty transcript_words")
    if not selected_pauses and stage1_asr.pause_spans:
        selected_pauses = _pause_spans_in_window(
            pause_spans=list(stage1_asr.pause_spans),
            start_abs=float(audio_obj["clip_start_abs"]),
            end_abs=float(audio_obj["clip_end_abs"]),
        )

    # Guardrail: in target-fragment branch we sometimes get an oversized clip window
    # while selected_fragment transcript words/pause spans only cover a much shorter range
    # (e.g. 68s clip with ~28s words due model time formatting drift).
    # Keep deterministic "no hidden fallback": clamp only when the mismatch is clearly inconsistent
    # with selected content and words actually fit in <= max clip span.
    if target_fragment and selected_words:
        clip_start = float(audio_obj["clip_start_abs"])
        clip_end = float(audio_obj["clip_end_abs"])
        clip_dur = clip_end - clip_start

        words_start = min(float(w.t_start) for w in selected_words)
        words_end = max(float(w.t_end) for w in selected_words)
        content_start = words_start
        content_end = words_end
        if selected_pauses:
            content_start = min(content_start, min(float(p.t_start) for p in selected_pauses))
            content_end = max(content_end, max(float(p.t_end) for p in selected_pauses))
        content_dur = content_end - content_start

        # Clamp only if clip is oversized and selected content itself fits within max window.
        if clip_dur > CLIP_WINDOW_MAX_SECONDS + 1e-6 and content_dur <= CLIP_WINDOW_MAX_SECONDS + 1e-6:
            new_start = float(content_start)
            new_end = float(content_end)

            # Keep Stage1 min duration invariant by expanding within original selected clip when needed.
            if (new_end - new_start) < CLIP_WINDOW_MIN_SECONDS - 1e-6:
                need = CLIP_WINDOW_MIN_SECONDS - (new_end - new_start)
                left_cap = max(0.0, new_start - clip_start)
                right_cap = max(0.0, clip_end - new_end)
                add_left = min(left_cap, need / 2.0)
                add_right = min(right_cap, need - add_left)
                rem = need - (add_left + add_right)
                if rem > 1e-9:
                    extra_left = min(max(0.0, left_cap - add_left), rem)
                    add_left += extra_left
                    rem -= extra_left
                if rem > 1e-9:
                    extra_right = min(max(0.0, right_cap - add_right), rem)
                    add_right += extra_right
                new_start -= add_left
                new_end += add_right

            logger.warning(
                "stage1a_selected_fragment_clip_content_mismatch clip=%.3f..%.3f dur=%.3f "
                "content=%.3f..%.3f dur=%.3f action=clamp_to_content result=%.3f..%.3f dur=%.3f",
                clip_start,
                clip_end,
                clip_dur,
                content_start,
                content_end,
                content_dur,
                new_start,
                new_end,
                float(new_end - new_start),
            )
            audio_obj["clip_start_abs"] = float(new_start)
            audio_obj["clip_end_abs"] = float(new_end)

    clip_start_after_shift, clip_end_after_shift = _maybe_shift_clip_window_for_leading_silence(
        clip_start_abs=float(audio_obj["clip_start_abs"]),
        clip_end_abs=float(audio_obj["clip_end_abs"]),
        selected_words=selected_words,
        logger=logger,
    )
    audio_obj["clip_start_abs"] = float(clip_start_after_shift)
    audio_obj["clip_end_abs"] = float(clip_end_after_shift)
    if selected_pauses:
        selected_pauses = _pause_spans_in_window(
            pause_spans=list(selected_pauses),
            start_abs=float(audio_obj["clip_start_abs"]),
            end_abs=float(audio_obj["clip_end_abs"]),
        )

    _warn_stage1_clip_over_max(
        clip_start_abs=float(audio_obj["clip_start_abs"]),
        clip_end_abs=float(audio_obj["clip_end_abs"]),
        logger=logger,
        source="stage1a_selected_fragment",
    )

    fallback_blocks = _fallback_draft_blocks_from_words([str(w.text) for w in selected_words])
    return Stage1PlanPayload.model_validate(
        {
            "audio": audio_obj,
            # Non-legacy Stage2 consumes the selected fragment, so stage1 plan should
            # carry fragment-local transcript words rather than full-track words.
            "transcript_words": selected_words,
            "pause_spans": selected_pauses,
            "draft_blocks": fallback_blocks,
            "fragment_analytics": (
                fragment_analytics.model_dump(mode="json")
                if fragment_analytics is not None
                else None
            ),
        }
    )


def _apply_user_clip_window_to_stage1(
    *,
    stage1: Stage1PlanPayload,
    stage1_asr: Stage1AsrPayload,
    start_abs: float,
    end_abs: float,
    logger: logging.Logger,
) -> Stage1PlanPayload:
    start = float(start_abs)
    end = float(end_abs)
    selected_words = _words_in_window(
        words=list(stage1_asr.transcript_words),
        start_abs=start,
        end_abs=end,
    )
    if not selected_words:
        raise ValueError(
            f"user clip window has no transcript words in range {start:.3f}..{end:.3f}"
        )
    selected_pauses = _pause_spans_in_window(
        pause_spans=list(stage1_asr.pause_spans or []),
        start_abs=start,
        end_abs=end,
    )

    payload = stage1.model_dump(mode="json")
    audio_obj = dict(payload.get("audio") or {})
    audio_obj["clip_start_abs"] = float(start)
    audio_obj["clip_end_abs"] = float(end)
    moi = audio_obj.get("moment_of_interest_sec")
    if moi is None or float(moi) < start or float(moi) > end:
        audio_obj["moment_of_interest_sec"] = float(start + (end - start) / 2.0)
    payload["audio"] = audio_obj
    payload["transcript_words"] = [w.model_dump(mode="json") for w in selected_words]
    payload["pause_spans"] = [p.model_dump(mode="json") for p in selected_pauses]

    # fragment_analytics is tied to LLM-selected window and becomes stale
    # after explicit user override.
    payload["fragment_analytics"] = None
    updated = Stage1PlanPayload.model_validate(payload)
    logger.info(
        "user_clip_window_applied stage1_clip=%.3f..%.3f words=%d pauses=%d",
        float(start),
        float(end),
        len(selected_words),
        len(selected_pauses),
    )
    return updated


def _is_fragment_target_exact_mismatch(
    *,
    target_fragment: str,
    analytics: FragmentAnalytics | None,
) -> bool:
    tf = _norm_compact(target_fragment)
    if not tf or analytics is None:
        return False
    af = _norm_compact(analytics.target_fragment)
    if not af:
        return False
    return af != tf


def _build_stage1b_fragment_exact_retry_hint(
    *,
    target_fragment: str,
    got_fragment: str,
) -> str:
    return (
        "\n\nTARGET_FRAGMENT_TEXT_CORRECTION=ON\n"
        "PREVIOUS_ATTEMPT_WARNING:\n"
        "- You used different words in fragment_analytics.target_fragment.\n"
        "- Editor note: you picked wrong words, edit them.\n"
        "- Copy USER_TARGET_FRAGMENT words exactly; no paraphrase/rewrite.\n"
        "EXPECTED_USER_TARGET_FRAGMENT:\n"
        + str(target_fragment or "")
        + "\n"
        "PREVIOUS_FRAGMENT_ANALYTICS_TARGET:\n"
        + str(got_fragment or "")
        + "\n"
        "Keep all other constraints unchanged.\n"
    )


def _validate_fragment_analytics_for_target(
    *,
    target_fragment: str,
    audio_start_abs: float,
    audio_end_abs: float,
    analytics: FragmentAnalytics | None,
    logger: logging.Logger,
) -> Tuple[float, float]:
    tf = _norm_compact(target_fragment)
    if not tf:
        return float(audio_start_abs), float(audio_end_abs)

    if analytics is None:
        raise ValueError("target_fragment branch requires fragment_analytics in Stage1ScenarioPayload")

    af = _norm_compact(analytics.target_fragment)
    if not af:
        raise ValueError("fragment_analytics.target_fragment is empty")
    if af != tf:
        logger.warning(
            "stage1b_fragment_target_mismatch got=%r expected=%r (continuing)",
            analytics.target_fragment,
            target_fragment,
        )

    audio_start = float(audio_start_abs)
    audio_end = float(audio_end_abs)
    forced_start = float(analytics.working_start_abs)
    forced_end = float(analytics.working_end_abs)
    forced_dur = float(forced_end) - float(forced_start)
    if forced_dur < CLIP_WINDOW_MIN_SECONDS - 1e-6:
        # Do not allow fragment_analytics to shrink a valid selected window below min clip size.
        logger.warning(
            "stage1b_fragment_analytics_window_too_short analytics=%.3f..%.3f dur=%.3f min=%s action=use_audio_window",
            forced_start,
            forced_end,
            forced_dur,
            CLIP_WINDOW_MIN_LABEL,
        )
        forced_start = audio_start
        forced_end = audio_end
    elif abs(audio_start - forced_start) > 1e-6 or abs(audio_end - forced_end) > 1e-6:
        # Keep overlap-safe deterministic behavior: never narrow by analytics mismatch.
        # If analytics is wider, we preserve it by taking union with the audio window.
        union_start = min(audio_start, forced_start)
        union_end = max(audio_end, forced_end)
        logger.warning(
            "stage1b_fragment_window_mismatch audio=%.3f..%.3f analytics=%.3f..%.3f action=use_union result=%.3f..%.3f",
            audio_start,
            audio_end,
            forced_start,
            forced_end,
            union_start,
            union_end,
        )
        forced_start = union_start
        forced_end = union_end

    relation = str(analytics.relation_to_target)
    action = str(analytics.chosen_action)
    if relation == "narrower":
        logger.warning(
            "stage1_fragment_narrowing_detected relation=%r action=%r clip=%.3f..%.3f (continuing)",
            relation,
            action,
            forced_start,
            forced_end,
        )
    expected = {
        "inside_13_18": "none",
        "inside_13_30": "none",
        "wider": "expand",
        "narrower": "select_subfragment",
    }
    exp = expected.get(relation)
    if exp is None:
        logger.warning(
            "stage1b_fragment_analytics_unknown_relation relation=%r action=%r (continuing)",
            relation,
            action,
        )
    elif action != exp:
        # Keep fragment_analytics descriptive (non-blocking): selected segment timing is the source of truth.
        logger.warning(
            "stage1b_fragment_analytics_noncanonical_action relation=%r action=%r expected=%r (continuing)",
            relation,
            action,
            exp,
        )

    logger.info(
        "stage1b_fragment_analytics relation=%s action=%s start=%.3f end=%.3f start_text=%r end_text=%r",
        relation,
        action,
        forced_start,
        forced_end,
        analytics.working_start_text,
        analytics.working_end_text,
    )
    return forced_start, forced_end


T = TypeVar("T")
U = TypeVar("U")


def _exc_blob(exc: BaseException) -> str:
    parts: List[str] = [type(exc).__name__]
    try:
        parts.append(str(exc))
    except Exception:
        pass
    try:
        parts.append(repr(exc))
    except Exception:
        pass
    return "\n".join(p for p in parts if p)


def _looks_like_model_validation_error_text(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    if "stage1a_user_clip_empty" in lo:
        return True
    if "fragment_analytics" in lo and "target_fragment" in lo:
        return True
    if "openrouter_schema_validation_failed" in lo:
        return True
    if "openrouter_tokens_schema_validation_failed" in lo:
        return True
    if "failed to validate gemini json against blockstokenspayload" in lo:
        return True
    if "llm_hedged_all_failed" in lo and "validationerror" in lo:
        return True
    if "gemini style pick is not present in style pool" in lo:
        return True
    if "jsondecodeerror" in lo:
        return True
    if "stage1a_selected_fragment_missing" in lo:
        return True
    return False


def _is_model_validation_error(exc: BaseException) -> bool:
    if isinstance(exc, _Stage1AUserClipEmptyError):
        return True
    if isinstance(exc, (ValidationError, JSONDecodeError)):
        return True
    return _looks_like_model_validation_error_text(_exc_blob(exc))


def _run_stage_with_model_validation_retries(
    *,
    stage_name: str,
    logger: logging.Logger,
    fn: Callable[[], T],
) -> T:
    max_retries = int(MODEL_VALIDATION_IMMEDIATE_RETRIES)
    total_attempts = 1 + max(0, max_retries)

    for attempt in range(1, total_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if (not _is_model_validation_error(e)) or attempt >= total_attempts:
                raise
            retry_no = attempt
            logger.warning(
                "stage_model_validation_retry stage=%s retry=%d/%d err=%s",
                stage_name,
                retry_no,
                max_retries,
                str(e),
            )

    # unreachable
    raise RuntimeError(f"stage_model_validation_retry_unreachable stage={stage_name}")


def _run_stage2_parallel(
    subtitles_fn: Callable[[], T],
    footage_fn: Callable[[], U],
) -> Tuple[T, U]:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gemini_stage2") as ex:
        f_sub: Future[T] = ex.submit(subtitles_fn)
        f_foot: Future[U] = ex.submit(footage_fn)

        done, _pending = wait({f_sub, f_foot}, return_when=FIRST_EXCEPTION)
        for f in done:
            exc = f.exception()
            if exc is not None:
                # Cancel the second branch and fail-fast.
                if f_sub is not f:
                    f_sub.cancel()
                if f_foot is not f:
                    f_foot.cancel()
                raise exc

        # Both done successfully.
        return f_sub.result(), f_foot.result()


def _run_stage2_parallel_collect(
    subtitles_fn: Callable[[], T],
    footage_fn: Callable[[], U],
) -> Tuple[Optional[T], Optional[U], Dict[str, BaseException]]:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gemini_stage2") as ex:
        f_sub: Future[T] = ex.submit(subtitles_fn)
        f_foot: Future[U] = ex.submit(footage_fn)
        wait({f_sub, f_foot})

        subtitles_payload: Optional[T] = None
        footage_payload: Optional[U] = None
        errors: Dict[str, BaseException] = {}

        try:
            subtitles_payload = f_sub.result()
        except Exception as e:  # noqa: BLE001
            errors["stage2_subtitles"] = e

        try:
            footage_payload = f_foot.result()
        except Exception as e:  # noqa: BLE001
            errors["stage2_style"] = e

        return subtitles_payload, footage_payload, errors


def _sanitize_token_text(s: str) -> str:
    keep = []
    for ch in str(s or ""):
        if ch.isalnum() or ch in {"-", "'", "’"}:
            keep.append(ch)
    out = "".join(keep).strip()
    return out or str(s or "").strip()


def _span_to_tokens(span: TokenSpan, words: List[TranscriptWord]) -> List[Dict[str, Any]]:
    st = int(span.start_idx)
    en = int(span.end_idx)
    out: List[Dict[str, Any]] = []
    for i in range(st, en + 1):
        w = words[i]
        txt = _sanitize_token_text(w.text)
        out.append(
            {
                "text": txt,
                "t_start": float(w.t_start),
                "t_end": float(w.t_end),
                "trailing": " " if i < en else "",
            }
        )
    return out


def _tokens_to_phrase(tokens: List[Dict[str, Any]]) -> str:
    return " ".join(str(t.get("text") or "").strip() for t in tokens if str(t.get("text") or "").strip()).strip()


def _subtitles_limits() -> Dict[str, int]:
    def _int_env(name: str, default: int) -> int:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return default
        try:
            v = int(raw)
        except Exception:
            return default
        return v if v > 0 else default

    return {
        "max_words": _int_env("SUBTITLES_MAX_WORDS", 8),
        "max_line_chars": _int_env("SUBTITLES_MAX_LINE_CHARS", 24),
        "max_weighted_line_chars": _int_env("SUBTITLES_MAX_WEIGHTED_LINE_CHARS", 30),
    }


def _ordered_named_token_lists(payload: BlocksTokensPayload) -> List[Tuple[str, List[Any]]]:
    return [
        ("block_1", list(payload.block_1.tokens)),
        ("block_2.p1", list(payload.block_2.p1.tokens)),
        ("block_2.p2", list(payload.block_2.p2.tokens)),
        ("block_3", list(payload.block_3.tokens)),
        ("block_4.p1", list(payload.block_4.p1.tokens)),
        ("block_4.p2", list(payload.block_4.p2.tokens)),
        ("block_5.slowly_in", list(payload.block_5.slowly_in.tokens)),
        ("block_5.fast_reveal", list(payload.block_5.fast_reveal.tokens)),
        ("block_5.glitch_peak", list(payload.block_5.glitch_peak.tokens)),
        ("block_5.mine", list(payload.block_5.mine.tokens)),
        ("block_6", list(payload.block_6.tokens)),
        ("block_7.part1", list(payload.block_7.part1.tokens)),
        ("block_7.part2", list(payload.block_7.part2.tokens)),
    ]


def _norm_words_for_lex_compare(s: str) -> List[str]:
    out: List[str] = []
    text = str(s or "").lower().replace("ё", "е").replace("\r", " ")
    for raw in text.split():
        w = re.sub(r"[^\w\-]+", "", raw, flags=re.UNICODE).strip("_")
        if w:
            out.append(w)
    return out


def _subtitles_words_for_lex_compare(payload: BlocksTokensPayload) -> List[str]:
    out: List[str] = []
    for _, tokens in _ordered_named_token_lists(payload):
        for t in tokens:
            out.extend(_norm_words_for_lex_compare(getattr(t, "text", "")))
    return out


def _log_target_fragment_subtitles_alignment(
    *,
    payload: BlocksTokensPayload,
    target_fragment: str,
    logger: logging.Logger,
) -> None:
    target_words = _norm_words_for_lex_compare(target_fragment)
    if not target_words:
        return

    subtitle_words = _subtitles_words_for_lex_compare(payload)
    target_cnt = Counter(target_words)
    sub_cnt = Counter(subtitle_words)
    matched = sum(min(int(v), int(sub_cnt.get(k, 0))) for k, v in target_cnt.items())
    missing = list((target_cnt - sub_cnt).elements())
    extra = list((sub_cnt - target_cnt).elements())
    overlap = (float(matched) / float(len(target_words))) if target_words else 1.0

    logger.info(
        "subtitles_target_fragment_alignment overlap=%.3f target_words=%d subtitle_words=%d matched=%d missing=%d extra=%d",
        overlap,
        len(target_words),
        len(subtitle_words),
        matched,
        len(missing),
        len(extra),
    )
    if missing:
        logger.warning(
            "subtitles_target_fragment_missing_words sample=%s",
            missing[:20],
        )
    if extra:
        logger.info(
            "subtitles_target_fragment_extra_words sample=%s",
            extra[:20],
        )


def _log_subtitles_token_metrics(payload: BlocksTokensPayload) -> None:
    lim = _subtitles_limits()
    log = logging.getLogger("mlcore.gemini_orchestrator")

    def _phrase_from_tokens(tokens: List[Any]) -> str:
        # We intentionally ignore trailing here; downstream layout pass will insert '\r'.
        return " ".join(str(getattr(t, "text", "") or "").strip() for t in tokens if str(getattr(t, "text", "") or "").strip()).strip()

    issues: List[str] = []
    max_words = 0
    max_line1 = 0
    max_weighted = 0
    max_words_where = ""

    for where, tokens in _ordered_named_token_lists(payload):
        phrase = _phrase_from_tokens(tokens)
        phrase_words = [w for w in phrase.split(" ") if w]
        line1, _, line2 = phrase.partition("\r")
        line1_len = len(" ".join(line1.strip().split()))
        line2_len = len(" ".join(line2.strip().split()))
        weighted = max(line1_len, (2 * line2_len) if line2_len > 0 else line1_len)
        words_n = len(phrase_words)

        if words_n > max_words:
            max_words = words_n
            max_words_where = where
        max_line1 = max(max_line1, line1_len)
        max_weighted = max(max_weighted, weighted)

        log.info(
            "subtitles_segment_metrics where=%s words=%d line1=%d line2=%d weighted=%d phrase=%r",
            where,
            words_n,
            line1_len,
            line2_len,
            weighted,
            phrase,
        )

        if words_n > lim["max_words"]:
            issues.append(f"{where} too many words: {words_n} > {lim['max_words']} | phrase={phrase!r}")
        if line1_len > lim["max_line_chars"]:
            issues.append(f"{where} line1 too long: {line1_len} > {lim['max_line_chars']} | phrase={phrase!r}")
        if weighted > lim["max_weighted_line_chars"]:
            issues.append(
                f"{where} weighted line too long: {weighted} > {lim['max_weighted_line_chars']} | phrase={phrase!r}"
            )

    log.info(
        "subtitles_layout_summary max_words=%d max_words_where=%s max_line1=%d max_weighted=%d limits_words=%d limits_line1=%d limits_weighted=%d",
        max_words,
        max_words_where,
        max_line1,
        max_weighted,
        lim["max_words"],
        lim["max_line_chars"],
        lim["max_weighted_line_chars"],
    )

    for msg in issues[:20]:
        log.warning("subtitles_layout_warning %s", msg)


def _ordered_named_spans(spans: BlocksTokenSpansPayload) -> List[Tuple[str, TokenSpan]]:
    return [
        ("block_1", spans.block_1),
        ("block_2.p1", spans.block_2.p1),
        ("block_2.p2", spans.block_2.p2),
        ("block_3", spans.block_3),
        ("block_4.p1", spans.block_4.p1),
        ("block_4.p2", spans.block_4.p2),
        ("block_5.slowly_in", spans.block_5.slowly_in),
        ("block_5.fast_reveal", spans.block_5.fast_reveal),
        ("block_5.glitch_peak", spans.block_5.glitch_peak),
        ("block_5.mine", spans.block_5.mine),
        ("block_6", spans.block_6),
        ("block_7.part1", spans.block_7.part1),
        ("block_7.part2", spans.block_7.part2),
    ]


def _validate_subtitles_spans(
    spans: BlocksTokenSpansPayload,
    *,
    stage1: Stage1PlanPayload,
) -> None:
    n = len(stage1.transcript_words)
    if abs(float(spans.clip.start) - float(stage1.audio.clip_start_abs)) > 1e-6:
        raise ValueError("subtitles.clip.start must equal stage1.audio.clip_start_abs")
    if abs(float(spans.clip.end) - float(stage1.audio.clip_end_abs)) > 1e-6:
        raise ValueError("subtitles.clip.end must equal stage1.audio.clip_end_abs")

    ordered = _ordered_named_spans(spans)

    prev_end = -1
    used: set[int] = set()
    for where, sp in ordered:
        if int(sp.end_idx) >= n:
            raise ValueError(f"{where} end_idx out of range: {sp.end_idx} >= {n}")
        if int(sp.start_idx) <= prev_end:
            raise ValueError(f"{where} overlaps previous segment: start_idx={sp.start_idx}, prev_end={prev_end}")
        for i in range(int(sp.start_idx), int(sp.end_idx) + 1):
            if i in used:
                raise ValueError(f"{where} reuses transcript token idx={i}")
            used.add(i)
        prev_end = int(sp.end_idx)


def _validate_subtitles_phrase_layout(
    spans: BlocksTokenSpansPayload,
    *,
    stage1: Stage1PlanPayload,
) -> None:
    lim = _subtitles_limits()
    words = stage1.transcript_words

    issues: List[str] = []
    log = logging.getLogger("mlcore.gemini_orchestrator")
    max_words = 0
    max_line1 = 0
    max_weighted = 0
    max_words_where = ""
    for where, sp in _ordered_named_spans(spans):
        toks = _span_to_tokens(sp, words)
        phrase = _tokens_to_phrase(toks)
        phrase_words = [w for w in phrase.split(" ") if w]
        line1, _, line2 = phrase.partition("\r")
        line1_len = len(" ".join(line1.strip().split()))
        line2_len = len(" ".join(line2.strip().split()))
        weighted = max(line1_len, (2 * line2_len) if line2_len > 0 else line1_len)
        hint = int(sp.char_count_hint)
        words_n = len(phrase_words)

        if words_n > max_words:
            max_words = words_n
            max_words_where = where
        max_line1 = max(max_line1, line1_len)
        max_weighted = max(max_weighted, weighted)

        log.info(
            "subtitles_segment_metrics where=%s words=%d line1=%d line2=%d weighted=%d hint=%d phrase=%r",
            where,
            words_n,
            line1_len,
            line2_len,
            weighted,
            hint,
            phrase,
        )

        if words_n > lim["max_words"]:
            issues.append(
                f"{where} too many words: {words_n} > {lim['max_words']} | phrase={phrase!r}"
            )
        if line1_len > lim["max_line_chars"]:
            issues.append(
                f"{where} line1 too long: {line1_len} > {lim['max_line_chars']} | phrase={phrase!r}"
            )
        if weighted > lim["max_weighted_line_chars"]:
            issues.append(
                f"{where} weighted line too long: {weighted} > {lim['max_weighted_line_chars']} | phrase={phrase!r}"
            )
        if abs(hint - line1_len) > 6:
            issues.append(
                f"{where} char_count_hint mismatch: hint={hint}, measured_line1={line1_len} | phrase={phrase!r}"
            )

    log.info(
        "subtitles_layout_summary max_words=%d max_words_where=%s max_line1=%d max_weighted=%d limits_words=%d limits_line1=%d limits_weighted=%d",
        max_words,
        max_words_where,
        max_line1,
        max_weighted,
        lim["max_words"],
        lim["max_line_chars"],
        lim["max_weighted_line_chars"],
    )

    if not issues:
        return

    strict_layout = str(os.environ.get("SUBTITLES_LAYOUT_STRICT", "")).strip().lower() in {"1", "true", "yes"}
    if strict_layout:
        raise ValueError(issues[0])

    for msg in issues[:20]:
        log.warning("subtitles_layout_warning %s", msg)


def _materialize_subtitles_from_stage1(
    spans: BlocksTokenSpansPayload,
    *,
    stage1: Stage1PlanPayload,
) -> BlocksTokensPayload:
    words = stage1.transcript_words
    _validate_subtitles_spans(spans, stage1=stage1)
    _validate_subtitles_phrase_layout(spans, stage1=stage1)

    def seg(span: TokenSpan) -> Dict[str, Any]:
        toks = _span_to_tokens(span, words)
        return {"phrase": _tokens_to_phrase(toks), "tokens": toks}

    obj: Dict[str, Any] = {
        "clip": {"start": float(stage1.audio.clip_start_abs), "end": float(stage1.audio.clip_end_abs)},
        "block_1": seg(spans.block_1),
        "block_2": {"p1": seg(spans.block_2.p1), "p2": seg(spans.block_2.p2)},
        "block_3": seg(spans.block_3),
        "block_4": {"p1": seg(spans.block_4.p1), "p2": seg(spans.block_4.p2)},
        "block_5": {
            "slowly_in": seg(spans.block_5.slowly_in),
            "fast_reveal": seg(spans.block_5.fast_reveal),
            "glitch_peak": seg(spans.block_5.glitch_peak),
            "mine": seg(spans.block_5.mine),
        },
        "block_6": seg(spans.block_6),
        "block_7": {"part1": seg(spans.block_7.part1), "part2": seg(spans.block_7.part2)},
    }
    return BlocksTokensPayload.model_validate(obj)


def _deterministic_subtitles_spans_from_stage1(stage1: Stage1PlanPayload) -> BlocksTokenSpansPayload:
    """
    Deterministically derive subtitle token spans from stage1 draft blocks.
    This avoids an LLM shifting spans across clip boundaries or between segments.
    """
    rows = align_stage1_draft_to_transcript(stage1)
    by_where = {r["where"]: r for r in rows}

    def _span(where: str) -> TokenSpan:
        r = by_where.get(where)
        if not r:
            raise ValueError(f"missing aligned row for {where}")
        hint = int(max(1, int(r.get("line1_chars") or 1)))
        return TokenSpan(start_idx=int(r["start_idx"]), end_idx=int(r["end_idx"]), char_count_hint=hint)

    obj: Dict[str, Any] = {
        "clip": {"start": float(stage1.audio.clip_start_abs), "end": float(stage1.audio.clip_end_abs)},
        "block_1": _span("block_1"),
        "block_2": {"p1": _span("block_2.p1"), "p2": _span("block_2.p2")},
        "block_3": _span("block_3"),
        "block_4": {"p1": _span("block_4.p1"), "p2": _span("block_4.p2")},
        "block_5": {
            "slowly_in": _span("block_5.slowly_in"),
            "fast_reveal": _span("block_5.fast_reveal"),
            "glitch_peak": _span("block_5.glitch_peak"),
            "mine": _span("block_5.mine"),
        },
        "block_6": _span("block_6"),
        "block_7": {"part1": _span("block_7.part1"), "part2": _span("block_7.part2")},
    }
    return BlocksTokenSpansPayload.model_validate(obj)


def _validate_footage_coverage_abs(
    payload: FootageSelectionPayload,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
) -> None:
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    clips = sorted(list(payload.clips), key=lambda c: float(c.in_point))
    if not clips:
        raise ValueError("footage.clips is empty")

    if abs(float(clips[0].in_point) - cs) > 1e-6:
        raise ValueError(f"first.in_point != clip_start_abs ({clips[0].in_point} != {cs})")

    for i in range(len(clips) - 1):
        a = clips[i]
        b = clips[i + 1]
        if abs(float(a.out_point) - float(b.in_point)) > 1e-6:
            raise ValueError(
                f"gap/overlap clip[{i}].out={a.out_point} clip[{i+1}].in={b.in_point}"
            )

    if abs(float(clips[-1].out_point) - ce) > 1e-6:
        raise ValueError(f"last.out_point != clip_end_abs ({clips[-1].out_point} != {ce})")


def _log_footage_interval_picker_diagnostics(
    *,
    logger: logging.Logger,
    diagnostics: FootageIntervalPickerDiagnostics,
) -> None:
    logger.info(
        "footage_interval_picker mode=%s style=%s/%s intervals=%d max_interval=%.3f "
        "pool_primary=%d pool_selected=%d widen_genre=%s widen_global=%s repeats_used=%s seed=%d seed_key=%s",
        getattr(diagnostics, "selection_mode", "classic"),
        diagnostics.genre,
        diagnostics.tag,
        diagnostics.intervals_count,
        diagnostics.max_interval_sec,
        diagnostics.primary_pool_count,
        diagnostics.selected_pool_count,
        diagnostics.widened_to_genre,
        diagnostics.widened_to_global,
        diagnostics.repeats_used,
        diagnostics.deterministic_seed,
        diagnostics.seed_key,
    )
    logger.info(
        "footage_interval_picker selected_file_names_count=%d file_names=%s",
        len(diagnostics.selected_file_names),
        diagnostics.selected_file_names,
    )
    subgroup_order = list(getattr(diagnostics, "subgroup_order", []) or [])
    if subgroup_order:
        logger.info("footage_interval_picker subgroup_order_count=%d", len(subgroup_order))
        for row in subgroup_order:
            logger.info(
                "footage_interval_picker subgroup idx=%s theme=%s group=%s pool_all=%s pool_selected=%s "
                "exclude_people=%s exclude_tags=%s priority_tags=%s color_priority=%s",
                row.get("index"),
                row.get("theme"),
                row.get("tags_group"),
                row.get("pool_all_count"),
                row.get("pool_selected_count"),
                row.get("exclude_people"),
                row.get("exclude_tags"),
                row.get("priority_theme_tags"),
                row.get("color_priority"),
            )
    interval_trace = list(getattr(diagnostics, "interval_trace", []) or [])
    if interval_trace:
        logger.info("footage_interval_picker interval_trace_count=%d", len(interval_trace))
        for row in interval_trace:
            logger.info(
                "footage_interval_picker interval idx=%s in=%.3f out=%.3f dur=%.3f phase=%s "
                "picked_subgroup=%s picked_theme=%s picked_group=%s file=%s exclude_relaxed=%s attempts=%s",
                row.get("interval_idx"),
                float(row.get("in_point") or 0.0),
                float(row.get("out_point") or 0.0),
                float(row.get("duration") or 0.0),
                row.get("phase"),
                row.get("selected_subgroup_idx"),
                row.get("selected_theme"),
                row.get("selected_tags_group"),
                row.get("selected_file_name"),
                row.get("exclude_relaxed"),
                row.get("attempts"),
            )


def build_all_via_gemini_one_call(
    *,
    progress_cb: Optional[Callable[[str], None]] = None,
    resume_state_path: Optional[Path] = None,
) -> Dict[str, Path]:
    """
    Backward-compatible function name; implementation is now staged:
      - stage1: ASR + audio window + scenario draft
      - stage2: subtitles + style + timing + interval footage picking
      - stage3: merge -> FullPlanPayload -> render_all_steps
    """
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    gemini_asr_key = os.environ.get("GEMINI_ASR_KEY", "").strip()
    vertex_ai_api_key = os.environ.get("VERTEX_AI_API_KEY_V1", "").strip()
    vertex_ai_project = (os.environ.get("VERTEX_AI_PROJECT") or "").strip() or None
    vertex_ai_location = (os.environ.get("VERTEX_AI_LOCATION") or "").strip() or None
    llm_worker_type = normalize_llm_worker_type(
        os.environ.get("LLM_WORKER_TYPE", ""),
        default=LLM_WORKER_TYPE_SDK,
    )
    vertex_sdk_mix_enabled = llm_worker_type == LLM_WORKER_TYPE_VERTEX_SDK_MIX
    proxy = os.environ.get("OUTBOUND_PROXY", "").strip()
    temperature = _float_env("GEMINI_TEMPERATURE", 0.0)
    timeout_s = _float_env("GEMINI_TIMEOUT_S", 120.0)
    max_output_tokens = _optional_int_env("GEMINI_MAX_OUTPUT_TOKENS", None)
    max_thinking_tokens = _optional_int_env("GEMINI_MAX_THINKING_TOKENS", 40000)
    provider_mode = normalize_provider_mode(os.environ.get("LLM_PROVIDER_MODE", PROVIDER_MODE_GEMINI))
    hedge_delay_s = _float_env("LLM_HEDGE_DELAY_S", 60.0)
    # STAGE2_TIMING_MODE is a code-level switch (not a secret) — it lives in
    # code so flipping it is a git push, not an .env edit. Not read from env.
    _allowed_timing_modes = ["prompts", "hybrid", "hook_aware"]
    timing_mode = STAGE2_TIMING_MODE
    if timing_mode not in _allowed_timing_modes:
        raise RuntimeError(
            f"Invalid STAGE2_TIMING_MODE={timing_mode!r}; "
            f"allowed={_allowed_timing_modes}"
        )
    fast_start_seconds = _require_float_env("STAGE2_FAST_START_SECONDS")
    if fast_start_seconds < 0.0:
        raise RuntimeError(f"Invalid STAGE2_FAST_START_SECONDS: {fast_start_seconds!r}")

    logger = _get_logger()
    mode = get_runtime_mode()
    if mode not in {MODE_DEV, MODE_PROD}:
        raise RuntimeError(f"Unsupported MODE={mode!r}")

    if vertex_sdk_mix_enabled and provider_mode != PROVIDER_MODE_GEMINI:
        raise RuntimeError(
            "vertex_sdk_mix requires LLM_PROVIDER_MODE=gemini"
        )
    if provider_mode in {PROVIDER_MODE_GEMINI, PROVIDER_MODE_HEDGED}:
        if vertex_sdk_mix_enabled:
            if not gemini_asr_key:
                raise RuntimeError("Missing GEMINI_ASR_KEY in env for LLM_WORKER_TYPE=vertex_sdk_mix")
            if not vertex_ai_api_key:
                raise RuntimeError("Missing VERTEX_AI_API_KEY_V1 in env for LLM_WORKER_TYPE=vertex_sdk_mix")
        elif not gemini_api_key:
            raise RuntimeError(
                "Missing GEMINI_API_KEY in env for LLM_PROVIDER_MODE=gemini|hedged"
            )

    # Explicit-only model contract:
    # - GEMINI_MODEL_STAGE1 is required (base).
    # - Optional overrides: GEMINI_MODEL_STAGE1_ASR / GEMINI_MODEL_STAGE1_SCENARIO.
    model_stage1_base = _require_model("GEMINI_MODEL_STAGE1")
    model_stage1_asr = (os.environ.get("GEMINI_MODEL_STAGE1_ASR") or model_stage1_base).strip()
    model_stage1_scenario = (os.environ.get("GEMINI_MODEL_STAGE1_SCENARIO") or model_stage1_base).strip()
    model_subtitles = _require_model("GEMINI_MODEL_SUBTITLES")
    model_footage = _require_model("GEMINI_MODEL_FOOTAGE")
    model_fallback_raw = (os.environ.get("GEMINI_MODEL_FALLBACK") or "gemini-3-flash-preview").strip()
    model_fallback = model_fallback_raw
    if model_fallback.lower() in {"off", "none", "disabled", "disable", "0"}:
        model_fallback = ""
    openrouter_api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    openrouter_timeout_s = _float_env("OPENROUTER_TIMEOUT_S", timeout_s)
    if provider_mode in {PROVIDER_MODE_OPENROUTER, PROVIDER_MODE_HEDGED} and not openrouter_api_key:
        raise RuntimeError(
            "Missing OPENROUTER_API_KEY in env for LLM_PROVIDER_MODE=openrouter|hedged"
        )

    logger.info(
        "llm_provider_config mode=%s hedge_delay_s=%s gemini_timeout_s=%s openrouter_timeout_s=%s "
        "timing_mode=%s fast_start_seconds=%.3f gemini_max_output_tokens=%s "
        "gemini_max_thinking_tokens=%s gemini_fallback_model=%s llm_worker_type=%s "
        "vertex_sdk_mix=%s vertex_location=%s",
        provider_mode,
        hedge_delay_s,
        timeout_s,
        openrouter_timeout_s,
        timing_mode,
        fast_start_seconds,
        str(max_output_tokens),
        str(max_thinking_tokens),
        (model_fallback or "<disabled>"),
        llm_worker_type,
        str(vertex_sdk_mix_enabled).lower(),
        str(vertex_ai_location or ""),
    )

    client_stage1_asr: Optional[GeminiClient] = None
    client_stage1_forced: Optional[GeminiClient] = None
    client_stage1_scenario: Optional[GeminiClient] = None
    client_subtitles: Optional[GeminiClient] = None
    client_subtitles_single_step: Optional[GeminiClient] = None
    client_footage: Optional[GeminiClient] = None
    client_timing: Optional[GeminiClient] = None
    if provider_mode in {PROVIDER_MODE_GEMINI, PROVIDER_MODE_HEDGED}:
        shared_api_key = vertex_ai_api_key if vertex_sdk_mix_enabled else gemini_api_key
        shared_vertexai = bool(vertex_sdk_mix_enabled)
        # vertex_sdk_mix contract:
        # audio-attached stages must use Gemini Developer API (vertexai=False)
        # and GEMINI_ASR_KEY to keep SDK-like upload semantics.
        client_stage1_asr = _make_client(
            api_key=gemini_asr_key if vertex_sdk_mix_enabled else shared_api_key,
            model=model_stage1_asr,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=False if vertex_sdk_mix_enabled else shared_vertexai,
            vertex_project=None if vertex_sdk_mix_enabled else (vertex_ai_project if shared_vertexai else None),
            vertex_location=None if vertex_sdk_mix_enabled else (vertex_ai_location if shared_vertexai else None),
        )
        client_stage1_forced = _make_client(
            api_key=gemini_asr_key if vertex_sdk_mix_enabled else shared_api_key,
            model=model_stage1_asr,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=0.0,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=False,
            vertex_project=None,
            vertex_location=None,
        )
        client_stage1_scenario = _make_client(
            api_key=shared_api_key,
            model=model_stage1_scenario,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=shared_vertexai,
            vertex_project=vertex_ai_project if shared_vertexai else None,
            vertex_location=vertex_ai_location if shared_vertexai else None,
        )
        client_subtitles = _make_client(
            api_key=shared_api_key,
            model=model_subtitles,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=shared_vertexai,
            vertex_project=vertex_ai_project if shared_vertexai else None,
            vertex_location=vertex_ai_location if shared_vertexai else None,
        )
        client_subtitles_single_step = _make_client(
            api_key=gemini_asr_key if vertex_sdk_mix_enabled else shared_api_key,
            model=_SCENES_3RD_SINGLE_STEP_MODEL,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=False if vertex_sdk_mix_enabled else shared_vertexai,
            vertex_project=None if vertex_sdk_mix_enabled else (vertex_ai_project if shared_vertexai else None),
            vertex_location=None if vertex_sdk_mix_enabled else (vertex_ai_location if shared_vertexai else None),
        )
        client_footage = _make_client(
            api_key=shared_api_key,
            model=model_footage,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=shared_vertexai,
            vertex_project=vertex_ai_project if shared_vertexai else None,
            vertex_location=vertex_ai_location if shared_vertexai else None,
        )
        client_timing = _make_client(
            api_key=shared_api_key,
            model=model_stage1_base,
            fallback_model=model_fallback or None,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
            max_output_tokens=max_output_tokens,
            max_thinking_tokens=max_thinking_tokens,
            vertexai=shared_vertexai,
            vertex_project=vertex_ai_project if shared_vertexai else None,
            vertex_location=vertex_ai_location if shared_vertexai else None,
        )

    openrouter_stage1_asr: Optional[OpenRouterClient] = None
    openrouter_stage1_forced: Optional[OpenRouterClient] = None
    openrouter_stage1_scenario: Optional[OpenRouterClient] = None
    openrouter_subtitles: Optional[OpenRouterClient] = None
    openrouter_subtitles_single_step: Optional[OpenRouterClient] = None
    openrouter_footage: Optional[OpenRouterClient] = None
    openrouter_timing: Optional[OpenRouterClient] = None
    if provider_mode in {PROVIDER_MODE_OPENROUTER, PROVIDER_MODE_HEDGED}:
        openrouter_stage1_asr = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(model_stage1_asr),
            temperature=temperature,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )
        openrouter_stage1_forced = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(model_stage1_asr),
            temperature=0.0,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )
        openrouter_stage1_scenario = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(model_stage1_scenario),
            temperature=temperature,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )
        openrouter_subtitles = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(model_subtitles),
            temperature=temperature,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )
        openrouter_subtitles_single_step = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(_SCENES_3RD_SINGLE_STEP_MODEL),
            temperature=temperature,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )
        openrouter_footage = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(model_footage),
            temperature=temperature,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )
        openrouter_timing = _make_openrouter_client(
            api_key=openrouter_api_key,
            model=_openrouter_model_from_gemini(model_stage1_base),
            temperature=temperature,
            timeout_s=openrouter_timeout_s,
            logger=logger,
        )

    inv_path = Path(
        os.environ.get("FOOTAGE_INVENTORY_JSON", str(ROOT / "data" / "footage_inventory.json"))
    ).resolve()
    if not inv_path.exists():
        raise FileNotFoundError(f"FOOTAGE_INVENTORY_JSON missing: {inv_path}")

    inv = _load_footage_inventory(inv_path)
    picker_assets = load_picker_assets_from_inventory(inv)
    style_groups = build_style_groups_from_assets(picker_assets)
    style_metadata_paths = _resolve_style_metadata_db_paths(root=ROOT)
    style_metadata_rows = load_footage_style_metadata_rows(db_paths=style_metadata_paths)
    style_metadata_index = merge_footage_style_metadata_rows(style_metadata_rows)
    mapped_picker_assets, unmapped_picker_file_names = map_inventory_assets_with_style_metadata(
        assets=picker_assets,
        metadata_index=style_metadata_index,
    )

    out_dir = Path(os.environ.get("OUT_DIR", str(ROOT / "out"))).resolve()
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    selection_seed_key = _resolve_footage_seed_key(out_dir=out_dir, logger=logger)

    if not mapped_picker_assets:
        logger.warning(
            "style_metadata_empty_mapping inventory_assets=%d metadata_rows=%d db_files=%s",
            len(picker_assets),
            len(style_metadata_rows),
            [str(p) for p in style_metadata_paths],
        )
    logger.info(
        "style_metadata_loaded db_files=%s rows=%d merged_ids=%d inventory_assets=%d mapped=%d unmapped=%d",
        [str(p) for p in style_metadata_paths],
        len(style_metadata_rows),
        len(style_metadata_index),
        len(picker_assets),
        len(mapped_picker_assets),
        len(unmapped_picker_file_names),
    )
    resume_state = _load_resume_state(resume_state_path, logger=logger)
    if resume_state:
        logger.info(
            "llm_resume_state_loaded path=%s keys=%s",
            str(resume_state_path),
            sorted(resume_state.keys()),
        )

    audio_dir = Path(os.environ.get("AUDIO_DIR", str(ROOT / "audio"))).resolve()
    audio_files = pick_audio_files(audio_dir)
    logger.info("audio_files_selected n=%d files=%s", len(audio_files), [p.name for p in audio_files])
    ffprobe_bin = str(os.environ.get("FFPROBE_BIN", "ffprobe") or "ffprobe").strip()
    audio_fact_dur: Optional[float] = None
    if audio_files:
        audio_fact_dur = _ffprobe_duration_sec(media_path=audio_files[0], ffprobe_bin=ffprobe_bin)
        if audio_fact_dur is not None:
            logger.info(
                "stage1a_audio_duration fact_dur=%.3f ffprobe_bin=%s file=%s",
                float(audio_fact_dur),
                ffprobe_bin,
                audio_files[0].name,
            )
        else:
            logger.warning(
                "stage1a_audio_duration_unavailable ffprobe_bin=%s file=%s",
                ffprobe_bin,
                audio_files[0].name,
            )

    use_cache = (os.environ.get("GEMINI_UPLOAD_CACHE", "1") or "1").strip() not in {"0", "false", "False", "no", "NO"}
    cache_path = (out_dir / "gemini_files_cache.json") if use_cache else None

    stamp = _stamp()
    lyrics_text = str(os.environ.get("LYRICS_TEXT") or "").strip()
    target_fragment = str(os.environ.get("TARGET_FRAGMENT") or "").strip()
    user_clip_window = _optional_user_clip_window_from_env(logger=logger)
    target_fragment_stage1 = target_fragment
    if user_clip_window is not None and target_fragment:
        logger.warning(
            "user_clip_window_override active, stage1 target_fragment branch disabled "
            "target_fragment_chars=%d",
            len(target_fragment),
        )
        target_fragment_stage1 = ""
    subtitles_mode = normalize_subtitles_mode(
        os.environ.get("SUBTITLES_MODE"),
        default=SUBTITLES_MODE_LEGACY_BLOCKS,
    )
    footage_artist_id = str(os.environ.get("FOOTAGE_ARTIST_ID") or "").strip()
    use_stage1b_scenario = subtitles_mode == SUBTITLES_MODE_LEGACY_BLOCKS
    forced_reference_text_raw = lyrics_text or target_fragment
    forced_reference_text, dropped_structural_tags = _strip_structural_tags_from_text(
        forced_reference_text_raw
    )
    forced_reference_words = _reference_words_from_user_text(forced_reference_text)
    if forced_reference_text_raw and not forced_reference_words:
        raise RuntimeError(
            "Reference text for forced alignment is present but empty after tokenization "
            "(LYRICS_TEXT/TARGET_FRAGMENT)."
        )
    use_forced_alignment = bool(forced_reference_words)
    stage1a_mode = "forced_alignment" if use_forced_alignment else "asr"

    _emit(progress_cb, "llm_stage1a_asr")
    logger.info(
        "stage1a_start mode=%s model=%s reference_words=%d structural_tags_ignored=%d "
        "subtitles_mode=%s selected_fragment_required=%s",
        stage1a_mode,
        model_stage1_asr,
        len(forced_reference_words),
        dropped_structural_tags,
        subtitles_mode,
        (not use_stage1b_scenario),
    )

    # When user_clip_window is set, the user has already chosen the timing window.
    # Don't ask the LLM to pick a selected_fragment — we'll construct it from the
    # user's window after ASR completes.
    need_llm_selected_fragment = (not use_stage1b_scenario) and (user_clip_window is None)

    if use_forced_alignment:
        stage1a_system = build_stage1a_forced_alignment_system_instruction()
        stage1a_prompt = build_stage1a_forced_alignment_user_prompt(
            reference_text=forced_reference_text,
            schema_name="Stage1ForcedAlignmentPayload",
            require_selected_fragment=need_llm_selected_fragment,
            target_fragment=target_fragment_stage1,
            user_clip_window=user_clip_window,
        )
        stage1a_raw = logs_dir / f"gemini_raw_stage1_forced_alignment_{stamp}.json"
        stage1a_sys = logs_dir / f"gemini_system_stage1_forced_alignment_{stamp}.txt"
        stage1a_user = logs_dir / f"gemini_prompt_stage1_forced_alignment_{stamp}.txt"
    else:
        stage1a_system = build_stage1a_asr_system_instruction()
        stage1a_prompt = build_stage1a_asr_user_prompt(
            schema_name="Stage1AsrPayload",
            require_selected_fragment=need_llm_selected_fragment,
            target_fragment=target_fragment_stage1,
            user_clip_window=user_clip_window,
        )
        stage1a_raw = logs_dir / f"gemini_raw_stage1_asr_{stamp}.json"
        stage1a_sys = logs_dir / f"gemini_system_stage1_asr_{stamp}.txt"
        stage1a_user = logs_dir / f"gemini_prompt_stage1_asr_{stamp}.txt"

    stage1_asr: Stage1AsrPayload | None = None
    stage1_asr_cached = resume_state.get("stage1_asr")
    stage1_asr_mode_cached = str(resume_state.get("stage1_asr_mode") or "").strip()
    stage1_asr_reference_cached = str(resume_state.get("stage1_asr_reference_text") or "")
    if isinstance(stage1_asr_cached, dict):
        cache_compatible = True
        if use_forced_alignment:
            if stage1_asr_mode_cached != "forced_alignment":
                cache_compatible = False
            if stage1_asr_reference_cached != forced_reference_text_raw:
                cache_compatible = False
        elif stage1_asr_mode_cached and stage1_asr_mode_cached != "asr":
            cache_compatible = False
        if not cache_compatible:
            logger.info(
                "llm_resume_skip stage=stage1a_asr reason=mode_or_reference_mismatch "
                "cached_mode=%r current_mode=%r cached_ref_chars=%d current_ref_chars=%d",
                stage1_asr_mode_cached,
                stage1a_mode,
                len(stage1_asr_reference_cached),
                len(forced_reference_text_raw),
            )
            resume_state.pop("stage1_asr", None)
            stage1_asr_cached = None

    if isinstance(stage1_asr_cached, dict):
        try:
            stage1_asr = Stage1AsrPayload.model_validate(stage1_asr_cached)
            if need_llm_selected_fragment and stage1_asr.selected_fragment is None:
                logger.info(
                    "llm_resume_skip stage=stage1a_asr reason=missing_selected_fragment_for_non_legacy_mode"
                )
                stage1_asr = None
                resume_state.pop("stage1_asr", None)
            elif user_clip_window is not None and not use_stage1b_scenario:
                try:
                    _ensure_stage1a_user_clip_has_words(
                        stage1_asr=stage1_asr,
                        user_clip_window=user_clip_window,
                    )
                    logger.info("llm_resume_hit stage=stage1a_asr")
                except _Stage1AUserClipEmptyError as e:
                    logger.warning(
                        "llm_resume_skip stage=stage1a_asr reason=user_clip_empty err=%s",
                        str(e),
                    )
                    stage1_asr = None
                    resume_state.pop("stage1_asr", None)
            else:
                logger.info("llm_resume_hit stage=stage1a_asr")
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage1a_asr err=%s", str(e))
            resume_state.pop("stage1_asr", None)

    if stage1_asr is None:
        # Cache MISS: the Stage1 ASR LLM is about to be invoked for real.
        # Emitted only here (after the resume check), unlike "llm_stage1a_asr"
        # above which is set unconditionally. The bigtest safety-breaker watches
        # this stage to halt the batch if a reuse case re-runs ASR.
        _emit(progress_cb, "llm_stage1a_asr_invoke")
        if use_forced_alignment:
            stage1_forced_checked_asr: Stage1AsrPayload | None = None

            def _call_stage1_forced_alignment_checked(
                *,
                user_prompt: str,
                raw_response_path: Path,
                prompt_dump_path: Path,
                system_dump_path: Path,
            ) -> Stage1ForcedAlignmentPayload:
                nonlocal stage1_forced_checked_asr
                payload = call_stage1_forced_alignment_once(
                    client=client_stage1_forced,
                    openrouter_client=openrouter_stage1_forced,
                    provider_mode=provider_mode,
                    hedge_delay_s=hedge_delay_s,
                    logger=logger,
                    system_instruction=stage1a_system,
                    user_prompt=user_prompt,
                    audio_paths=audio_files,
                    raw_response_path=raw_response_path,
                    cache_path=cache_path,
                    prompt_dump_path=prompt_dump_path,
                    system_dump_path=system_dump_path,
                )
                _validate_forced_alignment_payload(
                    payload=payload,
                    reference_words=forced_reference_words,
                    logger=logger,
                )
                checked_asr = _stage1_asr_from_forced_alignment(payload, logger=logger)
                if user_clip_window is not None and not use_stage1b_scenario:
                    _ensure_stage1a_user_clip_has_words(
                        stage1_asr=checked_asr,
                        user_clip_window=user_clip_window,
                    )
                stage1_forced_checked_asr = checked_asr
                return payload

            stage1_forced = _run_stage_with_model_validation_retries(
                stage_name="stage1_forced_alignment",
                logger=logger,
                fn=lambda: _call_stage1_forced_alignment_checked(
                    user_prompt=stage1a_prompt,
                    raw_response_path=stage1a_raw,
                    prompt_dump_path=stage1a_user,
                    system_dump_path=stage1a_sys,
                ),
            )
            stage1_forced_attempt_1 = stage1_forced.model_dump(mode="json")
            stage1_asr = stage1_forced_checked_asr or _stage1_asr_from_forced_alignment(
                stage1_forced,
                logger=logger,
            )
            transcribed_dur = _transcribed_duration_sec(stage1_asr)
            precision_diag = _analyze_stage1a_timecode_precision(payload=stage1_forced)
            logger.info(
                "stage1a_forced_timecode_precision words=%d points=%d unique_ms=%d quantized_50_ratio=%.3f "
                "zero_ms_ratio=%.3f mode_duration_ms=%d mode_duration_share=%.3f unique_durations=%d suspicious=%s reasons=%s",
                int(precision_diag.get("words") or 0),
                int(precision_diag.get("points") or 0),
                int(precision_diag.get("unique_ms") or 0),
                float(precision_diag.get("quantized_50_ratio") or 0.0),
                float(precision_diag.get("zero_ms_ratio") or 0.0),
                int(precision_diag.get("mode_duration_ms") or 0),
                float(precision_diag.get("mode_duration_share") or 0.0),
                int(precision_diag.get("unique_durations") or 0),
                bool(precision_diag.get("suspicious")),
                list(precision_diag.get("reasons") or []),
            )

            rework_hints: List[str] = []
            if (
                audio_fact_dur is not None
                and transcribed_dur is not None
                and _should_retry_stage1a_duration_drift(
                    reference_words_count=len(forced_reference_words),
                    fact_dur=float(audio_fact_dur),
                    transcribed_dur=float(transcribed_dur),
                )
            ):
                logger.warning(
                    "stage1a_forced_duration_drift_rework fact_dur=%.3f transcribed_dur=%.3f words=%d",
                    float(audio_fact_dur),
                    float(transcribed_dur),
                    len(forced_reference_words),
                )
                rework_hints.append(
                    _build_stage1a_duration_rework_hint(
                        fact_dur=float(audio_fact_dur),
                        transcribed_dur=float(transcribed_dur),
                        transcribe_attempt_1=stage1_forced_attempt_1,
                    )
                )
            if _should_retry_stage1a_suspicious_precision(
                reference_words_count=len(forced_reference_words),
                precision_diag=precision_diag,
            ):
                logger.warning(
                    "stage1a_forced_precision_rework reasons=%s words=%d unique_ms=%d quantized_50_ratio=%.3f mode_duration_share=%.3f",
                    list(precision_diag.get("reasons") or []),
                    len(forced_reference_words),
                    int(precision_diag.get("unique_ms") or 0),
                    float(precision_diag.get("quantized_50_ratio") or 0.0),
                    float(precision_diag.get("mode_duration_share") or 0.0),
                )
                rework_hints.append(
                    _build_stage1a_precision_rework_hint(
                        precision_diag=precision_diag,
                        transcribe_attempt_1=stage1_forced_attempt_1,
                        target_fragment=target_fragment_stage1,
                    )
                )

            if rework_hints:
                rework_hint = "".join(rework_hints)
                stage1a_raw_retry = logs_dir / f"gemini_raw_stage1_forced_alignment_rework_{stamp}.json"
                stage1a_sys_retry = logs_dir / f"gemini_system_stage1_forced_alignment_rework_{stamp}.txt"
                stage1a_user_retry = logs_dir / f"gemini_prompt_stage1_forced_alignment_rework_{stamp}.txt"
                stage1_forced = _run_stage_with_model_validation_retries(
                    stage_name="stage1_forced_alignment_rework",
                    logger=logger,
                    fn=lambda: _call_stage1_forced_alignment_checked(
                        user_prompt=stage1a_prompt + rework_hint,
                        raw_response_path=stage1a_raw_retry,
                        prompt_dump_path=stage1a_user_retry,
                        system_dump_path=stage1a_sys_retry,
                    ),
                )
                stage1_asr = stage1_forced_checked_asr or _stage1_asr_from_forced_alignment(
                    stage1_forced,
                    logger=logger,
                )
                (logs_dir / f"stage1_forced_alignment_attempt_1_{stamp}.json").write_text(
                    json.dumps(stage1_forced_attempt_1, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (logs_dir / f"stage1_forced_alignment_attempt_2_{stamp}.json").write_text(
                    json.dumps(stage1_forced.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            (logs_dir / f"stage1_forced_alignment_{stamp}.json").write_text(
                json.dumps(stage1_forced.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            def _run_stage1_asr_once() -> Stage1AsrPayload:
                payload = call_stage1_asr_once(
                    client=client_stage1_asr,
                    openrouter_client=openrouter_stage1_asr,
                    provider_mode=provider_mode,
                    hedge_delay_s=hedge_delay_s,
                    logger=logger,
                    system_instruction=stage1a_system,
                    user_prompt=stage1a_prompt,
                    audio_paths=audio_files,
                    raw_response_path=stage1a_raw,
                    cache_path=cache_path,
                    prompt_dump_path=stage1a_user,
                    system_dump_path=stage1a_sys,
                )
                if need_llm_selected_fragment and payload.selected_fragment is None:
                    raise RuntimeError(
                        f"stage1a_selected_fragment_missing subtitles_mode={subtitles_mode!r}"
                    )
                return payload

            stage1_asr = _run_stage_with_model_validation_retries(
                stage_name="stage1_asr",
                logger=logger,
                fn=_run_stage1_asr_once,
            )
        resume_state["stage1_asr"] = stage1_asr.model_dump(mode="json")
        resume_state["stage1_asr_mode"] = stage1a_mode
        resume_state["stage1_asr_reference_text"] = (
            forced_reference_text_raw if use_forced_alignment else ""
        )
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    # When user_clip_window is set in non-legacy mode, construct selected_fragment
    # from the user's explicit timing window instead of relying on LLM selection.
    # NOTE: we intentionally ignore any model-returned selected_fragment here.
    # With OpenRouter strict JSON schema the model may fill the optional field
    # with its own (wrong) timing even when the prompt does not request it.
    # The user's explicit window MUST take precedence unconditionally.
    if user_clip_window is not None and not use_stage1b_scenario:
        if stage1_asr.selected_fragment is not None:
            logger.warning(
                "user_clip_window_overriding_model_selected_fragment "
                "model_clip=%.3f..%.3f user_clip=%.3f..%.3f",
                float(stage1_asr.selected_fragment.audio.clip_start_abs),
                float(stage1_asr.selected_fragment.audio.clip_end_abs),
                float(user_clip_window[0]),
                float(user_clip_window[1]),
            )
        user_start, user_end = user_clip_window
        frag_words = _words_in_window(
            words=list(stage1_asr.transcript_words),
            start_abs=float(user_start),
            end_abs=float(user_end),
        )
        if not frag_words:
            raise ValueError(
                f"user clip window has no transcript words in range "
                f"{float(user_start):.3f}..{float(user_end):.3f}"
            )
        frag_pauses = _pause_spans_in_window(
            pause_spans=list(stage1_asr.pause_spans),
            start_abs=float(user_start),
            end_abs=float(user_end),
        )
        frag_srt = [
            s for s in stage1_asr.srt_items
            if float(s.start) >= float(user_start) - 1e-6
            and float(s.end) <= float(user_end) + 1e-6
        ]
        asr_dict = stage1_asr.model_dump(mode="json")
        asr_dict["selected_fragment"] = {
            "audio": {
                "clip_start_abs": float(user_start),
                "clip_end_abs": float(user_end),
            },
            "transcript_words": [w.model_dump(mode="json") for w in frag_words],
            "pause_spans": [p.model_dump(mode="json") for p in frag_pauses],
            "srt_items": [s.model_dump(mode="json") for s in frag_srt],
        }
        stage1_asr = Stage1AsrPayload.model_validate(asr_dict)
        logger.info(
            "user_clip_window_selected_fragment_constructed clip=%.3f..%.3f words=%d pauses=%d srt=%d",
            float(user_start), float(user_end),
            len(frag_words), len(frag_pauses), len(frag_srt),
        )

    stage1_asr_json = stage1_asr.model_dump(mode="json")
    (logs_dir / f"stage1_asr_{stamp}.json").write_text(
        json.dumps(stage1_asr_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    stage1b_transcript_words = list(stage1_asr.transcript_words)
    stage1b_pause_spans = list(stage1_asr.pause_spans)
    stage1b_prompt_transcript_words_json = stage1_asr_json.get("transcript_words", [])
    if user_clip_window is not None:
        user_start, user_end = user_clip_window
        stage1b_transcript_words = _words_in_window(
            words=list(stage1_asr.transcript_words),
            start_abs=float(user_start),
            end_abs=float(user_end),
        )
        if not stage1b_transcript_words:
            raise ValueError(
                f"user clip window has no transcript words in range {float(user_start):.3f}..{float(user_end):.3f}"
            )
        stage1b_pause_spans = _pause_spans_in_window(
            pause_spans=list(stage1_asr.pause_spans),
            start_abs=float(user_start),
            end_abs=float(user_end),
        )
        stage1b_prompt_transcript_words_json = [
            w.model_dump(mode="json")
            for w in stage1b_transcript_words
        ]
        logger.info(
            "user_clip_window_stage1b_context words=%d pauses=%d clip=%.3f..%.3f",
            len(stage1b_transcript_words),
            len(stage1b_pause_spans),
            float(user_start),
            float(user_end),
        )

    fragment_branch_on = bool(target_fragment_stage1)
    expected_stage1_plan_source = "stage1b_scenario" if use_stage1b_scenario else "stage1a_selected_fragment"

    stage1: Stage1PlanPayload | None = None
    stage1_cached = resume_state.get("stage1_plan")
    stage1_plan_source_cached = str(resume_state.get("stage1_plan_source") or "").strip()
    if isinstance(stage1_cached, dict):
        source_compatible = True
        if use_stage1b_scenario:
            if stage1_plan_source_cached and stage1_plan_source_cached != expected_stage1_plan_source:
                source_compatible = False
        else:
            # Non-legacy flow must use Stage1A-selected fragment plan.
            source_compatible = stage1_plan_source_cached == expected_stage1_plan_source
        if not source_compatible:
            logger.info(
                "llm_resume_skip stage=stage1_plan reason=source_mismatch cached=%r expected=%r",
                stage1_plan_source_cached,
                expected_stage1_plan_source,
            )
            resume_state.pop("stage1_plan", None)
            resume_state.pop("stage1_plan_source", None)
        else:
            try:
                stage1 = Stage1PlanPayload.model_validate(stage1_cached)
                if fragment_branch_on:
                    _validate_fragment_analytics_for_target(
                        target_fragment=target_fragment_stage1,
                        audio_start_abs=float(stage1.audio.clip_start_abs),
                        audio_end_abs=float(stage1.audio.clip_end_abs),
                        analytics=stage1.fragment_analytics,
                        logger=logger,
                    )
                if user_clip_window is not None:
                    user_start, user_end = user_clip_window
                    if (
                        abs(float(stage1.audio.clip_start_abs) - float(user_start)) > 1e-6
                        or abs(float(stage1.audio.clip_end_abs) - float(user_end)) > 1e-6
                    ):
                        raise RuntimeError(
                            "stage1_plan clip window mismatch for active user clip override "
                            f"(cached={float(stage1.audio.clip_start_abs):.3f}..{float(stage1.audio.clip_end_abs):.3f}, "
                            f"user={float(user_start):.3f}..{float(user_end):.3f})"
                        )
                logger.info("llm_resume_hit stage=stage1_plan source=%s", expected_stage1_plan_source)
            except Exception as e:
                logger.warning("llm_resume_bad stage=stage1_plan err=%s", str(e))
                resume_state.pop("stage1_plan", None)
                resume_state.pop("stage1_plan_source", None)

    if stage1 is None and use_stage1b_scenario:
        _emit(progress_cb, "llm_stage1b_scenario")
        logger.info("stage1b_start model=%s", model_stage1_scenario)
        logger.info(
            "stage1b_fragment_branch enabled=%s target_fragment_chars=%d",
            fragment_branch_on,
            len(target_fragment_stage1),
        )

        stage1b_system = build_stage1b_scenario_system_instruction()
        stage1b_base_prompt = build_stage1b_scenario_user_prompt(
            asr_json={
                "transcript_words": stage1b_prompt_transcript_words_json,
            },
            target_fragment=target_fragment_stage1,
            schema_name="Stage1ScenarioPayload",
        )
        stage1b_sys = logs_dir / f"gemini_system_stage1_scenario_{stamp}.txt"
        stage1b_raw = logs_dir / f"gemini_raw_stage1_scenario_{stamp}.json"
        stage1b_user = logs_dir / f"gemini_prompt_stage1_scenario_{stamp}.txt"

        def _run_stage1_scenario_once() -> Tuple[Stage1PlanPayload, Stage1ScenarioPayload]:
            prompt = stage1b_base_prompt
            exact_retry_used = False

            while True:
                stage1_scenario = call_stage1_scenario_once(
                    client=client_stage1_scenario,
                    openrouter_client=openrouter_stage1_scenario,
                    provider_mode=provider_mode,
                    hedge_delay_s=hedge_delay_s,
                    logger=logger,
                    system_instruction=stage1b_system,
                    user_prompt=prompt,
                    # IMPORTANT:
                    # Stage1B is scenario planning based on Stage1A transcript_words.
                    # We do NOT attach audio here to avoid the model "re-listening" and drifting from transcript.
                    audio_paths=[],
                    raw_response_path=stage1b_raw,
                    cache_path=cache_path,
                    prompt_dump_path=stage1b_user,
                    system_dump_path=stage1b_sys,
                )

                audio_obj = stage1_scenario.audio.model_dump(mode="json")
                if fragment_branch_on:
                    forced_start, forced_end = _validate_fragment_analytics_for_target(
                        target_fragment=target_fragment_stage1,
                        audio_start_abs=float(stage1_scenario.audio.clip_start_abs),
                        audio_end_abs=float(stage1_scenario.audio.clip_end_abs),
                        analytics=stage1_scenario.fragment_analytics,
                        logger=logger,
                    )
                    # Keep clip window deterministic in target-fragment branch:
                    # always use analytics-confirmed working window.
                    audio_obj["clip_start_abs"] = float(forced_start)
                    audio_obj["clip_end_abs"] = float(forced_end)

                    mismatch = _is_fragment_target_exact_mismatch(
                        target_fragment=target_fragment_stage1,
                        analytics=stage1_scenario.fragment_analytics,
                    )
                    if mismatch and not exact_retry_used:
                        got_fragment = ""
                        if stage1_scenario.fragment_analytics is not None:
                            got_fragment = str(stage1_scenario.fragment_analytics.target_fragment or "")
                        retry_hint = _build_stage1b_fragment_exact_retry_hint(
                            target_fragment=target_fragment_stage1,
                            got_fragment=got_fragment,
                        )
                        logger.warning(
                            "stage1b_fragment_exact_retry_hint_applied expected=%r got=%r hint_chars=%d",
                            target_fragment_stage1,
                            got_fragment,
                            len(retry_hint),
                        )
                        prompt = stage1b_base_prompt + retry_hint
                        exact_retry_used = True
                        continue
                    if mismatch:
                        got_fragment = ""
                        if stage1_scenario.fragment_analytics is not None:
                            got_fragment = str(stage1_scenario.fragment_analytics.target_fragment or "")
                        logger.warning(
                            "stage1b_fragment_target_mismatch_persisted expected=%r got=%r (continuing)",
                            target_fragment_stage1,
                            got_fragment,
                        )
                if user_clip_window is not None:
                    user_start, user_end = user_clip_window
                    audio_obj["clip_start_abs"] = float(user_start)
                    audio_obj["clip_end_abs"] = float(user_end)
                    audio_obj["moment_of_interest_sec"] = float(
                        user_start + (user_end - user_start) / 2.0
                    )

                stage1_candidate = Stage1PlanPayload.model_validate(
                    {
                        "audio": audio_obj,
                        "transcript_words": stage1b_transcript_words,
                        "pause_spans": stage1b_pause_spans,
                        "draft_blocks": stage1_scenario.draft_blocks.model_dump(mode="json"),
                        "fragment_analytics": (
                            stage1_scenario.fragment_analytics.model_dump(mode="json")
                            if stage1_scenario.fragment_analytics is not None
                            else None
                        ),
                    }
                )
                return stage1_candidate, stage1_scenario

        stage1, stage1_scenario = _run_stage_with_model_validation_retries(
            stage_name="stage1_scenario",
            logger=logger,
            fn=_run_stage1_scenario_once,
        )

        # Best-effort alignment report (useful for debugging), but do not fail Stage1 if it can't be aligned.
        report_path = logs_dir / f"stage1_report_{stamp}.txt"
        try:
            align_rows = align_stage1_draft_to_transcript(stage1)
            report_path.write_text(build_stage1_report(stage1, align_rows), encoding="utf-8")
        except Exception as e:
            logger.warning("stage1b_align_warning err=%s", str(e))
            report_path.write_text(
                "STAGE1 ALIGNMENT WARNING (non-fatal)\n"
                f"err={e}\n\n"
                "DRAFT_BLOCKS_JSON:\n"
                + json.dumps(stage1_scenario.draft_blocks.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        resume_state["stage1_plan"] = stage1.model_dump(mode="json")
        resume_state["stage1_plan_source"] = expected_stage1_plan_source
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    if stage1 is None and (not use_stage1b_scenario):
        _emit(progress_cb, "llm_stage1a_fragment_select")
        logger.info(
            "stage1b_skip mode=%s reason=non_legacy_uses_stage1a_selected_fragment",
            subtitles_mode,
        )
        selected = stage1_asr.selected_fragment
        if selected is None:
            raise ValueError(
                f"subtitles_mode={subtitles_mode!r} requires Stage1A.selected_fragment, got null"
            )
        stage1 = _build_stage1_plan_from_selected_fragment(
            stage1_asr=stage1_asr,
            selected=selected,
            target_fragment=target_fragment_stage1,
            logger=logger,
        )
        (logs_dir / f"stage1a_selected_fragment_{stamp}.json").write_text(
            json.dumps(selected.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resume_state["stage1_plan"] = stage1.model_dump(mode="json")
        resume_state["stage1_plan_source"] = expected_stage1_plan_source
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    if stage1 is None:
        raise RuntimeError("stage1 plan is empty after stage1 processing")
    if user_clip_window is not None:
        user_start, user_end = user_clip_window
        stage1 = _apply_user_clip_window_to_stage1(
            stage1=stage1,
            stage1_asr=stage1_asr,
            start_abs=float(user_start),
            end_abs=float(user_end),
            logger=logger,
        )
    _warn_stage1_clip_over_max(
        clip_start_abs=float(stage1.audio.clip_start_abs),
        clip_end_abs=float(stage1.audio.clip_end_abs),
        logger=logger,
        source=expected_stage1_plan_source,
    )
    stage1_json = stage1.model_dump(mode="json")
    stage1_json["lyrics_text"] = lyrics_text
    stage1_json["target_fragment"] = target_fragment
    (logs_dir / f"stage1_plan_merged_{stamp}.json").write_text(
        json.dumps(stage1_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Backward-compatible artifact name.
    (logs_dir / f"stage1_plan_{stamp}.json").write_text(
        json.dumps(stage1_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(progress_cb, "llm_stage2_parallel")
    subtitles_planner = SubtitlesPlannerFactory.create(subtitles_mode)
    subtitles_model_effective = (
        _SCENES_3RD_SINGLE_STEP_MODEL
        if subtitles_mode == SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP
        else model_subtitles
    )
    logger.info(
        "stage2_start subtitles_model=%s footage_style_model=%s timing_model=%s timing_mode=%s style_groups=%d subtitles_mode=%s subtitles_schema=%s",
        subtitles_model_effective,
        model_footage,
        model_stage1_base,
        timing_mode,
        len(style_groups),
        subtitles_mode,
        subtitles_planner.schema_model.__name__,
    )

    stage1_words_in_clip = _words_in_window(
        words=list(stage1.transcript_words),
        start_abs=float(stage1.audio.clip_start_abs),
        end_abs=float(stage1.audio.clip_end_abs),
    )
    if not stage1_words_in_clip:
        logger.warning(
            "stage2_context_empty_transcript_words clip=%s..%s stage1_words_total=%d subtitles_mode=%s stage1_plan_source=%s",
            stage1.audio.clip_start_abs,
            stage1.audio.clip_end_abs,
            len(stage1.transcript_words),
            subtitles_mode,
            expected_stage1_plan_source,
        )
    else:
        logger.info(
            "stage2_context_words_in_clip count=%d clip=%s..%s",
            len(stage1_words_in_clip),
            stage1.audio.clip_start_abs,
            stage1.audio.clip_end_abs,
        )

    sub_system = subtitles_planner.build_system_instruction()
    sub_prompt = subtitles_planner.build_user_prompt(stage1_json=stage1_json)
    subtitles_retry_hint = (os.environ.get("STAGE2_SUBTITLES_RETRY_HINT") or "").strip()
    if subtitles_retry_hint:
        sub_prompt = (
            str(sub_prompt)
            + "\n\nSUBTITLES_RETRY_HINT:\n"
            + subtitles_retry_hint
            + "\n"
        )
        logger.info("stage2_subtitles_retry_hint_applied chars=%d", len(subtitles_retry_hint))
    sub_raw = logs_dir / f"gemini_raw_stage2_subtitles_{stamp}.json"
    sub_sys = logs_dir / f"gemini_system_stage2_subtitles_{stamp}.txt"
    sub_user = logs_dir / f"gemini_prompt_stage2_subtitles_{stamp}.txt"

    foot_system = build_stage2_footage_system_instruction(artist_id=footage_artist_id)
    # Per-user rotation cursor override: when set, forces Stage 2B to emit a
    # single subgroup for the exact (theme, tags_group) that the bot-side
    # rotation picked for this user/artist. Empty means no override.
    rotation_theme_override = str(os.environ.get("FOOTAGE_ROTATION_THEME") or "").strip()
    rotation_group_override = str(os.environ.get("FOOTAGE_ROTATION_GROUP") or "").strip()
    foot_prompt = build_stage2_footage_user_prompt(
        stage1_json=stage1_json,
        style_groups=style_groups,
        schema_name="FootageStyleRotation",
        artist_id=footage_artist_id,
        rotation_theme=rotation_theme_override,
        rotation_tags_group=rotation_group_override,
    )
    foot_raw = logs_dir / f"gemini_raw_stage2_style_{stamp}.json"
    foot_sys = logs_dir / f"gemini_system_stage2_style_{stamp}.txt"
    foot_user = logs_dir / f"gemini_prompt_stage2_style_{stamp}.txt"

    timing_analysis_system = build_stage2_timing_analysis_system_instruction(timing_mode=timing_mode)
    timing_cuts_system = build_stage2_timing_cuts_system_instruction(timing_mode=timing_mode)
    timing_analysis_raw = logs_dir / f"gemini_raw_stage2_timing_analysis_{stamp}.json"
    timing_analysis_sys = logs_dir / f"gemini_system_stage2_timing_analysis_{stamp}.txt"
    timing_analysis_user = logs_dir / f"gemini_prompt_stage2_timing_analysis_{stamp}.txt"
    timing_cuts_raw = logs_dir / f"gemini_raw_stage2_timing_cuts_{stamp}.json"
    timing_cuts_sys = logs_dir / f"gemini_system_stage2_timing_cuts_{stamp}.txt"
    timing_cuts_user = logs_dir / f"gemini_prompt_stage2_timing_cuts_{stamp}.txt"

    def _run_subtitles_once() -> BlocksTokensPayload | SubtitleFlowPlan:
        subtitles_audio_paths = list(audio_files) if subtitles_planner.attach_audio_for_stage2 else []
        subtitles_client = (
            client_subtitles_single_step
            if subtitles_mode == SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP
            else client_subtitles
        )
        subtitles_openrouter_client = (
            openrouter_subtitles_single_step
            if subtitles_mode == SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP
            else openrouter_subtitles
        )
        if subtitles_planner.use_tokens_structured:
            raw_payload = call_subtitles_plan_once(
                client=subtitles_client,
                openrouter_client=subtitles_openrouter_client,
                provider_mode=provider_mode,
                hedge_delay_s=hedge_delay_s,
                logger=logger,
                system_instruction=sub_system,
                user_prompt=str(sub_prompt),
                audio_paths=subtitles_audio_paths,
                raw_response_path=sub_raw,
                cache_path=cache_path,
                prompt_dump_path=sub_user,
                system_dump_path=sub_sys,
            )
        else:
            raw_payload = call_subtitles_plan_model_once(
                client=subtitles_client,
                schema_model=subtitles_planner.schema_model,
                openrouter_client=subtitles_openrouter_client,
                provider_mode=provider_mode,
                hedge_delay_s=hedge_delay_s,
                logger=logger,
                system_instruction=sub_system,
                user_prompt=str(sub_prompt),
                audio_paths=subtitles_audio_paths,
                raw_response_path=sub_raw,
                cache_path=cache_path,
                prompt_dump_path=sub_user,
                system_dump_path=sub_sys,
                stage_name=f"stage2_subtitles_{subtitles_mode}",
            )

        payload = subtitles_planner.normalize_payload(
            payload=raw_payload,
            stage1=stage1,
            logger=logger,
        )

        if isinstance(payload, BlocksTokensPayload):
            _log_subtitles_token_metrics(payload)
            if target_fragment:
                _log_target_fragment_subtitles_alignment(
                    payload=payload,
                    target_fragment=target_fragment,
                    logger=logger,
                )
        else:
            logger.info(
                "subtitle_flow_summary mode=%s segments=%d clip=%s..%s",
                subtitles_mode,
                len(payload.segments),
                payload.clip.start,
                payload.clip.end,
            )
        return payload

    def _run_subtitles() -> BlocksTokensPayload | SubtitleFlowPlan:
        return _run_stage_with_model_validation_retries(
            stage_name="stage2_subtitles",
            logger=logger,
            fn=_run_subtitles_once,
        )

    style_raw_payload: Optional[FootageStyleRawPayload] = None
    style_adapter_diag: Optional[FootageStyleRawAdapterDiagnostics] = None
    style_rotation_payload: Optional[FootageStyleRotation] = None

    def _run_style_once() -> FootageStylePickPayload:
        nonlocal style_raw_payload, style_adapter_diag, style_rotation_payload

        def _accept_direct_pick(
            pick: FootageStylePickPayload, *, source: str
        ) -> FootageStylePickPayload:
            nonlocal style_raw_payload, style_adapter_diag, style_rotation_payload
            if footage_artist_id:
                raise RuntimeError(
                    "stage2_style_direct_pick_not_allowed_with_footage_artist_id: "
                    f"source={source!r} expected=FootageStyleRotation"
                )
            validate_style_pick_in_groups(pick, style_groups)
            style_raw_payload = None
            style_adapter_diag = None
            style_rotation_payload = None
            logger.info(
                "stage2_style_direct_pick_selected source=%s genre=%s tag=%s inventory_assets=%d",
                source,
                pick.genre,
                pick.tag,
                len(picker_assets),
            )
            return pick

        payload_any = call_footage_style_once(
            client=client_footage,
            openrouter_client=openrouter_footage,
            provider_mode=provider_mode,
            hedge_delay_s=hedge_delay_s,
            logger=logger,
            system_instruction=foot_system,
            user_prompt=str(foot_prompt),
            # Style selection needs only Stage1 context + style pool groups.
            audio_paths=[],
            extra_file_paths=None,
            raw_response_path=foot_raw,
            cache_path=cache_path,
            prompt_dump_path=foot_user,
            system_dump_path=foot_sys,
            schema_model=FootageStyleRotation,
        )

        if isinstance(payload_any, FootageStylePickPayload):
            return _accept_direct_pick(payload_any, source="model")

        if isinstance(payload_any, FootageStyleRotation):
            rotation = payload_any
        else:
            try:
                direct_pick = FootageStylePickPayload.model_validate(payload_any)
            except Exception:
                rotation = FootageStyleRotation.model_validate(payload_any)
            else:
                return _accept_direct_pick(direct_pick, source="compat")

        if not mapped_picker_assets:
            raise RuntimeError(
                "style_rotation_requires_mapped_inventory_assets: no inventory assets are mapped "
                "to style metadata. Check merged metadata dbs and inventory filename clip ids."
            )

        # Resolve genre/tag from highest-priority subgroup that can be mapped to inventory groups.
        # Rotation-override hard check: if we pinned (theme, tags_group), enforce exact match.
        if rotation_theme_override and rotation_group_override:
            if len(rotation.subgroups) != 1:
                raise RuntimeError(
                    "stage2_style_rotation_override_violation: expected exactly 1 subgroup "
                    f"when cursor override is set, got {len(rotation.subgroups)}"
                )
            only = rotation.subgroups[0]
            if str(only.theme).strip() != rotation_theme_override:
                raise RuntimeError(
                    "stage2_style_rotation_override_theme_mismatch: "
                    f"expected={rotation_theme_override!r} got={only.theme!r}"
                )
            if str(only.tags_group or "").strip() != rotation_group_override:
                raise RuntimeError(
                    "stage2_style_rotation_override_group_mismatch: "
                    f"expected={rotation_group_override!r} got={only.tags_group!r}"
                )
        base_raw = rotation.subgroups[0]
        if footage_artist_id:
            for idx, subgroup in enumerate(rotation.subgroups):
                subgroup_artist = str(subgroup.artist_id or "").strip()
                if not subgroup_artist:
                    raise RuntimeError(
                        "stage2_style_rotation_missing_artist_id "
                        f"expected={footage_artist_id!r} subgroup_idx={idx}"
                    )
                if subgroup_artist != footage_artist_id:
                    raise RuntimeError(
                        "stage2_style_rotation_artist_id_mismatch "
                        f"expected={footage_artist_id!r} got={subgroup_artist!r} subgroup_idx={idx}"
                    )
        resolved: Optional[FootageStylePickPayload] = None
        diag: Optional[FootageStyleRawAdapterDiagnostics] = None
        resolve_errors: List[str] = []
        selected_subgroup_idx = 0
        for idx, subgroup in enumerate(rotation.subgroups):
            try:
                resolved_candidate, diag_candidate = resolve_style_pick_from_raw_filters(
                    raw_pick=subgroup,
                    mapped_assets=mapped_picker_assets,
                    seed_key=selection_seed_key,
                    requested_style_id=footage_artist_id,
                    total_assets=len(picker_assets),
                    unmapped_assets=len(unmapped_picker_file_names),
                    metadata_rows_merged=len(style_metadata_index),
                )
            except Exception as e:
                resolve_errors.append(f"subgroup[{idx}] theme={subgroup.theme!r} group={subgroup.tags_group!r}: {e}")
                continue
            resolved = resolved_candidate
            diag = diag_candidate
            base_raw = subgroup
            selected_subgroup_idx = idx
            break
        if resolved is None or diag is None:
            detail = "; ".join(resolve_errors) if resolve_errors else "no subgroup could be resolved"
            raise RuntimeError(f"stage2_style_rotation_resolve_failed: {detail}")
        validate_style_pick_in_groups(resolved, style_groups)
        style_raw_payload = base_raw  # enables mapped_picker_assets selection path
        style_adapter_diag = diag
        style_rotation_payload = rotation
        logger.info(
            "stage2_style_adapter_selected subgroup_idx=%d theme=%s group=%s mood=%s subgroups=%d "
            "genre=%s tag=%s requested_style_id=%s requested_style_genre=%s "
            "resolved_style_genre=%s resolved_rank=%d fallback=%s mapped=%d unmapped=%d",
            int(selected_subgroup_idx),
            base_raw.theme,
            base_raw.tags_group or "-",
            base_raw.mood,
            len(rotation.subgroups),
            resolved.genre,
            resolved.tag,
            diag.requested_style_id or "-",
            diag.requested_style_genre_key or "-",
            diag.resolved_style_genre_key or "-",
            int(diag.resolved_similarity_rank),
            bool(diag.similarity_fallback_used),
            len(mapped_picker_assets),
            len(unmapped_picker_file_names),
        )
        return resolved

    def _run_style() -> FootageStylePickPayload:
        return _run_stage_with_model_validation_retries(
            stage_name="stage2_style",
            logger=logger,
            fn=_run_style_once,
        )

    subtitles_payload: BlocksTokensPayload | SubtitleFlowPlan | None = None
    style_payload: FootageStylePickPayload | None = None
    subtitles_from_resume = False
    style_from_resume = False
    subtitles_cached = resume_state.get("stage2_subtitles")
    subtitles_cached_mode = str(resume_state.get("stage2_subtitles_mode") or "").strip()
    if isinstance(subtitles_cached, dict):
        try:
            if subtitles_cached_mode and subtitles_cached_mode != subtitles_mode:
                raise RuntimeError(
                    f"subtitles mode mismatch in resume state ({subtitles_cached_mode!r} != {subtitles_mode!r})"
                )
            subtitles_payload = subtitles_planner.validate_resume_payload(subtitles_cached)
            subtitles_from_resume = True
            logger.info("llm_resume_hit stage=stage2_subtitles")
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage2_subtitles err=%s", str(e))
            resume_state.pop("stage2_subtitles", None)
            resume_state.pop("stage2_subtitles_mode", None)

    style_cached = resume_state.get("stage2_style")
    style_rotation_cached = resume_state.get("stage2_style_rotation")
    if isinstance(style_cached, dict):
        try:
            style_payload = FootageStylePickPayload.model_validate(style_cached)
            validate_style_pick_in_groups(style_payload, style_groups)
            if isinstance(style_rotation_cached, dict):
                style_rotation_payload = FootageStyleRotation.model_validate(style_rotation_cached)
                if not style_rotation_payload.subgroups:
                    raise RuntimeError("stage2_style_rotation.subgroups is empty in resume state")
                style_raw_payload = style_rotation_payload.subgroups[0]
                logger.info(
                    "llm_resume_hit stage=stage2_style source=rotation subgroups=%d",
                    len(style_rotation_payload.subgroups),
                )
            elif style_rotation_cached is None:
                style_raw_payload = None
                style_rotation_payload = None
                logger.info("llm_resume_hit stage=stage2_style source=direct_pick")
            else:
                raise RuntimeError("stage2_style_rotation has invalid type in resume state")
            style_from_resume = True
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage2_style err=%s", str(e))
            resume_state.pop("stage2_style", None)
            resume_state.pop("stage2_style_rotation", None)
            style_raw_payload = None
            style_rotation_payload = None
    elif style_rotation_cached is not None:
        logger.warning(
            "llm_resume_bad stage=stage2_style err=stage2_style_rotation present without stage2_style"
        )
        resume_state.pop("stage2_style_rotation", None)

    subtitles_only = os.environ.get("SUBTITLES_ONLY", "").strip() in ("1", "true", "yes")
    if subtitles_only and style_payload is None:
        # Pick the first available genre/tag from inventory — no Gemini call, no tag filtering.
        first_asset = next(iter(picker_assets), None)
        if first_asset is None:
            raise RuntimeError("subtitles_only=True but inventory is empty")
        style_payload = FootageStylePickPayload(
            genre=str(first_asset.get("genre", "Alternative")),
            tag=str(first_asset.get("tag", "dark_aesthetic")),
        )
        style_raw_payload = None
        style_rotation_payload = None
        logger.info("subtitles_only_mode genre=%s tag=%s", style_payload.genre, style_payload.tag)

    stage2_errors: Dict[str, BaseException] = {}
    if subtitles_payload is None and style_payload is None:
        subtitles_payload, style_payload, stage2_errors = _run_stage2_parallel_collect(
            _run_subtitles,
            _run_style,
        )
    else:
        if subtitles_payload is None:
            try:
                subtitles_payload = _run_subtitles()
            except Exception as e:  # noqa: BLE001
                stage2_errors["stage2_subtitles"] = e
        if style_payload is None:
            try:
                style_payload = _run_style()
            except Exception as e:  # noqa: BLE001
                stage2_errors["stage2_style"] = e

    state_dirty = False
    if subtitles_payload is not None and not subtitles_from_resume:
        resume_state["stage2_subtitles"] = subtitles_payload.model_dump(mode="json")
        resume_state["stage2_subtitles_mode"] = subtitles_mode
        state_dirty = True
    if style_payload is not None and not style_from_resume:
        resume_state["stage2_style"] = style_payload.model_dump(mode="json")
        if style_rotation_payload is not None:
            resume_state["stage2_style_rotation"] = style_rotation_payload.model_dump(mode="json")
        else:
            resume_state.pop("stage2_style_rotation", None)
        state_dirty = True
    if state_dirty:
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    if stage2_errors:
        detail = "; ".join(
            f"{name}={type(err).__name__}: {err}" for name, err in stage2_errors.items()
        )
        raise RuntimeError(f"Stage2 failed: {detail}")

    if subtitles_payload is None or style_payload is None:
        raise RuntimeError("Stage2 failed: missing payloads after execution")

    clip_start_abs = float(stage1.audio.clip_start_abs)
    clip_end_abs = float(stage1.audio.clip_end_abs)
    if isinstance(subtitles_payload, SubtitleFlowPlan):
        sub_clip_start_abs = float(subtitles_payload.clip.start)
        sub_clip_end_abs = float(subtitles_payload.clip.end)
        if abs(sub_clip_start_abs - clip_start_abs) > 1e-6 or abs(sub_clip_end_abs - clip_end_abs) > 1e-6:
            logger.info(
                "stage2_effective_clip_from_subtitles mode=%s stage1=%s..%s subtitles=%s..%s",
                subtitles_mode,
                clip_start_abs,
                clip_end_abs,
                sub_clip_start_abs,
                sub_clip_end_abs,
            )
        clip_start_abs = sub_clip_start_abs
        clip_end_abs = sub_clip_end_abs

    stage1_timing_json = dict(stage1_json)
    stage1_timing_audio = dict(stage1_timing_json.get("audio") or {})
    stage1_timing_audio["clip_start_abs"] = float(clip_start_abs)
    stage1_timing_audio["clip_end_abs"] = float(clip_end_abs)
    stage1_timing_json["audio"] = stage1_timing_audio

    bpm: Optional[float] = None
    hook_analysis_dict: Optional[Dict[str, object]] = None
    if timing_mode == "hybrid":
        if not audio_files:
            raise RuntimeError("No audio files available for BPM detection in STAGE2_TIMING_MODE=hybrid")
        bpm = detect_bpm_librosa(
            audio_path=audio_files[0],
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
        )
        bpm_obj = {
            "audio_file": str(audio_files[0]),
            "clip_start_abs": clip_start_abs,
            "clip_end_abs": clip_end_abs,
            "bpm": float(bpm),
        }
        (logs_dir / f"stage2_bpm_librosa_{stamp}.json").write_text(
            json.dumps(bpm_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (logs_dir / "stage2_bpm_librosa.json").write_text(
            json.dumps(bpm_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif timing_mode == "hook_aware":
        # Phase A: full hook audio analysis (BPM + beats + onsets + drop + sections).
        # No-fallback policy: any failure here must surface as an explicit error;
        # do NOT silently degrade to hybrid or prompts.
        if not audio_files:
            raise RuntimeError(
                "No audio files available for hook analysis in STAGE2_TIMING_MODE=hook_aware"
            )
        from mlcore.audio_analysis import (
            DropCandidate as _DropCand,
            analyze_focus_clip,
            to_jsonable,
        )
        # include_envelope=True so the persisted artifact carries the RMS
        # loudness curve for the downstream AE-FX phase (wiggle amplitude /
        # glow intensity that follow loudness). It is trimmed out of the LLM
        # prompt below — kept on disk only.
        hook_analysis = analyze_focus_clip(
            audio_path=audio_files[0],
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            include_envelope=True,
        )
        bpm = float(hook_analysis.bpm)
        # User-confirmed drop override (Phase A-UX). If USER_DROP_T was set by
        # the build task, the user picked a specific drop moment in the bot.
        # Replace the algorithmic top-1 with the user value so downstream
        # sections segmentation pivots around what the user actually heard.
        # The original computed top-1 is preserved further down the list for
        # debugging and A/B analysis.
        user_drop_t_raw = (os.environ.get("USER_DROP_T") or "").strip()
        algorithmic_top1: Optional[float] = (
            float(hook_analysis.drop_candidates[0].t)
            if hook_analysis.drop_candidates else None
        )
        user_drop_t_value: Optional[float] = None
        if user_drop_t_raw:
            try:
                user_drop_t_value = float(user_drop_t_raw)
            except Exception as e:
                raise RuntimeError(f"invalid USER_DROP_T={user_drop_t_raw!r}") from e
            if not (clip_start_abs <= user_drop_t_value <= clip_end_abs):
                raise RuntimeError(
                    f"USER_DROP_T={user_drop_t_value!r} outside clip window "
                    f"[{clip_start_abs}, {clip_end_abs}]"
                )
            user_pick = _DropCand(
                t=round(float(user_drop_t_value), 3),
                confidence=1.0,
                score_raw=0.0,
                score_adj=0.0,
                snapped_to_beat=False,
                source="user_override",
            )
            preserved = [
                c for c in hook_analysis.drop_candidates
                if abs(float(c.t) - float(user_drop_t_value)) > 0.5
            ]
            hook_analysis.drop_candidates = [user_pick] + preserved[: max(0, 4)]
            # Re-segment sections around the user-picked drop_t.
            from mlcore.audio_analysis import _segment_sections as _segment_for_user_drop  # type: ignore
            hook_analysis.sections = _segment_for_user_drop(
                hook_analysis.density_curve, float(user_drop_t_value)
            )
            logger.info(
                "stage2_hook_user_drop_override user_t=%.3f algorithmic_top1_t=%s",
                float(user_drop_t_value),
                f"{algorithmic_top1:.3f}" if algorithmic_top1 is not None else "none",
            )
        full_hook_dict = to_jsonable(hook_analysis)
        full_hook_dict["audio_file"] = str(audio_files[0])
        if user_drop_t_value is not None:
            full_hook_dict["user_drop_t_override"] = float(user_drop_t_value)
            full_hook_dict["algorithmic_top1_drop_t"] = (
                float(algorithmic_top1) if algorithmic_top1 is not None else None
            )
        (logs_dir / f"stage2_hook_analysis_{stamp}.json").write_text(
            json.dumps(full_hook_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (logs_dir / "stage2_hook_analysis.json").write_text(
            json.dumps(full_hook_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Trim heavy fields not useful inside the LLM prompt: density_curve is
        # already aggregated into sections[], and energy_envelope is purely
        # for downstream JSX/effects (AE wiggle/glow). Keep both on disk.
        hook_analysis_dict = {
            k: v for k, v in full_hook_dict.items()
            if k not in ("density_curve", "energy_envelope")
        }
        logger.info(
            "stage2_hook_analysis_ok version=%s bpm=%.2f beats=%d onsets=%d sections=%d "
            "drop_t=%s drop_conf=%s",
            hook_analysis.analysis_version,
            bpm,
            len(hook_analysis.beats),
            len(hook_analysis.onsets),
            len(hook_analysis.sections),
            (f"{hook_analysis.drop_candidates[0].t:.3f}" if hook_analysis.drop_candidates else "none"),
            (f"{hook_analysis.drop_candidates[0].confidence:.2f}" if hook_analysis.drop_candidates else "none"),
        )
    else:
        logger.info("stage2_bpm_skipped mode=%s source=gemini_only", timing_mode)

    # Current user-picked drop (if any) participates in the resume-cache key:
    # cached switch points computed under a different drop must NOT be reused.
    # Normalized identically on read and write; None when no override is set
    # (so non-hook modes always compare None==None and resume cleanly).
    _cur_user_drop_raw = (os.environ.get("USER_DROP_T") or "").strip()
    _cur_user_drop_t: Optional[float] = (
        round(float(_cur_user_drop_raw), 3) if _cur_user_drop_raw else None
    )

    switch_payload: SwitchTimingPayload | None = None
    switch_cached = resume_state.get("stage2_switch_timestamps")
    switch_mode_cached = str(resume_state.get("stage2_timing_mode") or "").strip()
    switch_fast_cached = resume_state.get("stage2_fast_start_seconds")
    if isinstance(switch_cached, dict):
        try:
            if switch_mode_cached != timing_mode:
                raise RuntimeError("timing mode mismatch in resume state")
            if float(switch_fast_cached) != float(fast_start_seconds):
                raise RuntimeError("fast-start seconds mismatch in resume state")
            _cached_drop = resume_state.get("stage2_user_drop_t")
            _cached_drop_norm: Optional[float] = (
                round(float(_cached_drop), 3) if _cached_drop is not None else None
            )
            if _cached_drop_norm != _cur_user_drop_t:
                raise RuntimeError("user_drop_t mismatch in resume state")
            switch_payload = SwitchTimingPayload.model_validate(switch_cached)
            if abs(float(switch_payload.clip_start_abs) - clip_start_abs) > 1e-6:
                raise RuntimeError("clip_start_abs mismatch in resume stage2_switch_timestamps")
            if abs(float(switch_payload.clip_end_abs) - clip_end_abs) > 1e-6:
                raise RuntimeError("clip_end_abs mismatch in resume stage2_switch_timestamps")
            logger.info("llm_resume_hit stage=stage2_switch_timestamps")
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage2_switch_timestamps err=%s", str(e))
            resume_state.pop("stage2_switch_timestamps", None)

    timing_analysis_payload: Stage2TimingAnalysisPayload | None = None
    timing_cuts_payload: Stage2TimingCutsPayload | None = None
    if switch_payload is None:
        timing_analysis_prompt = build_stage2_timing_analysis_user_prompt(
            stage1_json=stage1_timing_json,
            subtitles_json=subtitles_payload.model_dump(mode="json"),
            bpm=bpm,
            fast_start_seconds=float(fast_start_seconds),
            timing_mode=timing_mode,
            schema_name="Stage2TimingAnalysisPayload",
            hook_analysis=hook_analysis_dict,
        )

        timing_analysis_payload = _run_stage_with_model_validation_retries(
            stage_name="stage2_timing_analysis",
            logger=logger,
            fn=lambda: call_timing_analysis_once(
                client=client_timing,
                openrouter_client=openrouter_timing,
                provider_mode=provider_mode,
                hedge_delay_s=hedge_delay_s,
                logger=logger,
                system_instruction=timing_analysis_system,
                user_prompt=timing_analysis_prompt,
                audio_paths=[],
                raw_response_path=timing_analysis_raw,
                cache_path=cache_path,
                prompt_dump_path=timing_analysis_user,
                system_dump_path=timing_analysis_sys,
            ),
        )

        timing_cuts_prompt = build_stage2_timing_cuts_user_prompt(
            stage1_json=stage1_timing_json,
            timing_analysis_json=timing_analysis_payload.model_dump(mode="json"),
            bpm=bpm,
            fast_start_seconds=float(fast_start_seconds),
            timing_mode=timing_mode,
            schema_name="Stage2TimingCutsPayload",
            hook_analysis=hook_analysis_dict,
        )

        timing_cuts_payload = _run_stage_with_model_validation_retries(
            stage_name="stage2_timing_cuts",
            logger=logger,
            fn=lambda: call_timing_cuts_once(
                client=client_timing,
                openrouter_client=openrouter_timing,
                provider_mode=provider_mode,
                hedge_delay_s=hedge_delay_s,
                logger=logger,
                system_instruction=timing_cuts_system,
                user_prompt=timing_cuts_prompt,
                audio_paths=[],
                raw_response_path=timing_cuts_raw,
                cache_path=cache_path,
                prompt_dump_path=timing_cuts_user,
                system_dump_path=timing_cuts_sys,
            ),
        )
        if timing_cuts_payload.applied_rule != timing_analysis_payload.selected_rule:
            raise RuntimeError(
                "stage2_timing_cuts.applied_rule must match stage2_timing_analysis.selected_rule "
                f"({timing_cuts_payload.applied_rule!r} != {timing_analysis_payload.selected_rule!r})"
            )

        switch_points = normalize_switch_points(
            raw_cut_timings=list(timing_cuts_payload.final_cut_timings),
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            merge_gap_sec=0.2,
            min_segment_sec=0.3,
            compact_short_segments=True,
        )
        # hook_aware mode does NOT need fast-start beat synthesis: the LLM
        # already receives full beats[]/onsets[]/sections[] in its prompt and
        # is expected to place cuts on real measured anchors.
        if timing_mode == "hybrid":
            if bpm is None:
                raise RuntimeError("Hybrid timing mode requires librosa BPM before fast-start beat synthesis")
            fast_points = _hybrid_fast_start_switch_points(
                clip_start_abs=clip_start_abs,
                clip_end_abs=clip_end_abs,
                fast_start_seconds=float(fast_start_seconds),
                bpm=float(bpm),
            )
            fast_end_abs = min(clip_end_abs, clip_start_abs + float(fast_start_seconds))
            semantic_tail = [x for x in switch_points if x >= fast_end_abs - 1e-6]
            switch_points = normalize_switch_points(
                raw_cut_timings=sorted(list(fast_points) + list(semantic_tail)),
                clip_start_abs=clip_start_abs,
                clip_end_abs=clip_end_abs,
                merge_gap_sec=0.2,
                min_segment_sec=0.3,
                compact_short_segments=True,
            )

        switch_payload = SwitchTimingPayload.model_validate(
            {
                "clip_start_abs": clip_start_abs,
                "clip_end_abs": clip_end_abs,
                "fast_start_seconds": float(fast_start_seconds),
                "bpm": float(bpm) if bpm is not None else None,
                "switch_points_abs": switch_points,
            }
        )
        resume_state["stage2_timing_mode"] = timing_mode
        resume_state["stage2_fast_start_seconds"] = float(fast_start_seconds)
        resume_state["stage2_user_drop_t"] = _cur_user_drop_t
        resume_state["stage2_switch_timestamps"] = switch_payload.model_dump(mode="json")
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    if switch_payload is None:
        raise RuntimeError("Stage2 failed: switch timing payload is empty")

    exclude_file_names: List[str] = []
    seen_excluded: set[str] = set()

    # Source 1: inline JSON list via env var (per-job overrides).
    exclude_raw = (os.environ.get("FOOTAGE_EXCLUDE_FILE_NAMES_JSON") or "").strip()
    if exclude_raw:
        try:
            parsed = json.loads(exclude_raw)
        except Exception as e:
            raise RuntimeError(f"Invalid FOOTAGE_EXCLUDE_FILE_NAMES_JSON: {e!r}") from e
        if not isinstance(parsed, list):
            raise RuntimeError("FOOTAGE_EXCLUDE_FILE_NAMES_JSON must be a JSON list")
        for it in parsed:
            name = str(it or "").strip()
            if not name or name in seen_excluded:
                continue
            seen_excluded.add(name)
            exclude_file_names.append(name)

    # Source 2: persistent blacklist file (FOOTAGE_BLACKLIST_PATH).
    blacklist_path = (os.environ.get("FOOTAGE_BLACKLIST_PATH") or "").strip()
    if blacklist_path:
        bl_file = Path(blacklist_path)
        if bl_file.exists():
            try:
                bl_data = json.loads(bl_file.read_text(encoding="utf-8"))
                if isinstance(bl_data, list):
                    for it in bl_data:
                        name = str(it or "").strip()
                        if not name or name in seen_excluded:
                            continue
                        seen_excluded.add(name)
                        exclude_file_names.append(name)
                else:
                    logger.warning("footage_blacklist_invalid path=%s (expected JSON list)", blacklist_path)
            except Exception as e:
                logger.warning("footage_blacklist_load_error path=%s err=%r", blacklist_path, e)
        else:
            logger.warning("footage_blacklist_missing path=%s", blacklist_path)

    if exclude_file_names:
        logger.info(
            "footage_exclude_input count=%d names=%s",
            len(exclude_file_names),
            exclude_file_names,
        )

    selection_assets = mapped_picker_assets if style_rotation_payload is not None else picker_assets
    if style_rotation_payload is not None:
        logger.info(
            "footage_selection_mode mode=raw_priority_v2 mapped_assets=%d subgroups=%d",
            len(selection_assets),
            len(style_rotation_payload.subgroups),
        )
    footage_payload, interval_diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style_payload,
        assets=selection_assets,
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        switch_points_abs=list(switch_payload.switch_points_abs),
        seed_key=selection_seed_key,
        fit_mode="cover",
        exclude_file_names=exclude_file_names,
        raw_pick=None,
        raw_picks=style_rotation_payload.subgroups if style_rotation_payload is not None else None,
    )
    if getattr(interval_diag, "exclude_relaxed", False):
        logger.warning(
            "footage_exclude_relaxed excluded_count=%d selected_excluded_count=%d",
            int(getattr(interval_diag, "excluded_input_count", 0)),
            int(getattr(interval_diag, "selected_excluded_count", 0)),
        )
    _validate_footage_coverage_abs(
        footage_payload,
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
    )
    _log_footage_interval_picker_diagnostics(logger=logger, diagnostics=interval_diag)

    # Debug artifacts (like Stage1): dump parsed Stage2 payloads so we can inspect what the model returned
    # without digging into the raw response wrapper.
    (logs_dir / f"stage2_subtitles_{stamp}.json").write_text(
        json.dumps(subtitles_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / f"stage2_style_{stamp}.json").write_text(
        json.dumps(style_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if style_rotation_payload is not None:
        (logs_dir / f"stage2_style_rotation_{stamp}.json").write_text(
            json.dumps(style_rotation_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if style_raw_payload is not None:
        (logs_dir / f"stage2_style_raw_{stamp}.json").write_text(
            json.dumps(style_raw_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if style_adapter_diag is not None:
        style_diag_obj = {
            "seed_key": selection_seed_key,
            "seed": _seed_from_key_material(selection_seed_key),
            "total_assets": int(style_adapter_diag.total_assets),
            "metadata_rows_merged": int(style_adapter_diag.metadata_rows_merged),
            "mapped_assets": int(style_adapter_diag.mapped_assets),
            "unmapped_assets": int(style_adapter_diag.unmapped_assets),
            "unmapped_file_names": list(unmapped_picker_file_names),
            "mood_filtered_out": int(style_adapter_diag.mood_filtered_out),
            "exclude_filtered_out": int(style_adapter_diag.exclude_filtered_out),
            "scored_assets": int(style_adapter_diag.scored_assets),
            "selected_genre": style_adapter_diag.selected_genre,
            "selected_tag": style_adapter_diag.selected_tag,
            "selected_group_score": float(style_adapter_diag.selected_group_score),
            "selected_group_duration_sec": float(style_adapter_diag.selected_group_duration_sec),
            "selected_group_assets_count": int(style_adapter_diag.selected_group_assets_count),
            "requested_style_id": style_adapter_diag.requested_style_id,
            "requested_style_genre_key": style_adapter_diag.requested_style_genre_key,
            "resolved_style_genre_key": style_adapter_diag.resolved_style_genre_key,
            "resolved_similarity_rank": int(style_adapter_diag.resolved_similarity_rank),
            "similarity_fallback_used": bool(style_adapter_diag.similarity_fallback_used),
            "similarity_chain": list(style_adapter_diag.similarity_chain),
            "top_groups": list(style_adapter_diag.top_groups),
        }
        (logs_dir / f"stage2_style_adapter_diag_{stamp}.json").write_text(
            json.dumps(style_diag_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if timing_analysis_payload is not None:
        (logs_dir / f"stage2_timing_analysis_{stamp}.json").write_text(
            json.dumps(timing_analysis_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if timing_cuts_payload is not None:
        (logs_dir / f"stage2_timing_cuts_{stamp}.json").write_text(
            json.dumps(timing_cuts_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (logs_dir / f"stage2_switch_timestamps_{stamp}.json").write_text(
        json.dumps(switch_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / f"stage2_footage_{stamp}.json").write_text(
        json.dumps(footage_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    intervals = build_intervals_from_switch_points(
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        switch_points_abs=list(switch_payload.switch_points_abs),
    )
    interval_rows: List[Dict[str, Any]] = []
    clips_sorted = sorted(footage_payload.clips, key=lambda c: float(c.in_point))
    if len(clips_sorted) != len(intervals):
        raise RuntimeError(
            f"Internal mismatch: clips={len(clips_sorted)} intervals={len(intervals)}"
        )
    for idx, (a, b) in enumerate(intervals):
        clip = clips_sorted[idx]
        interval_rows.append(
            {
                "in_point": float(a),
                "out_point": float(b),
                "duration": float(b - a),
                "file_name": str(clip.file_name),
            }
        )
    interval_obj = {"timing_mode": timing_mode, "intervals": interval_rows}
    (logs_dir / f"stage2_footage_intervals_{stamp}.json").write_text(
        json.dumps(interval_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    selection_trace_obj = {
        "selection_mode": str(getattr(interval_diag, "selection_mode", "classic")),
        "subgroup_order": list(getattr(interval_diag, "subgroup_order", []) or []),
        "interval_trace": list(getattr(interval_diag, "interval_trace", []) or []),
    }
    (logs_dir / f"stage2_footage_selection_trace_{stamp}.json").write_text(
        json.dumps(selection_trace_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Compact diagnostics for the bot rotation-advance logic.
    # Consumed by tg_bot_public / tg_bot_botapi to decide whether the cursor
    # should advance after this job (bad run: avg_score<1.5 OR repeat_ratio>=0.75
    # OR exclude_relaxed).
    rotation_diag_obj = {
        "selection_mode": str(getattr(interval_diag, "selection_mode", "classic")),
        "resolved_theme": str(getattr(interval_diag, "tag", "") or ""),
        "resolved_tags_group": (
            str(list(getattr(interval_diag, "subgroup_order", []) or [{}])[0].get("tags_group") or "")
            if getattr(interval_diag, "subgroup_order", None) else ""
        ),
        "rotation_theme_requested": rotation_theme_override,
        "rotation_group_requested": rotation_group_override,
        "exclude_relaxed": bool(getattr(interval_diag, "exclude_relaxed", False)),
        "repeats_used": bool(getattr(interval_diag, "repeats_used", False)),
        "primary_pool_avg_score": float(
            getattr(interval_diag, "primary_pool_avg_score", 0.0) or 0.0
        ),
        "primary_pool_repeat_ratio": float(
            getattr(interval_diag, "primary_pool_repeat_ratio", 0.0) or 0.0
        ),
        "intervals_count": int(getattr(interval_diag, "intervals_count", 0) or 0),
        "primary_pool_count": int(getattr(interval_diag, "primary_pool_count", 0) or 0),
        "selected_pool_count": int(getattr(interval_diag, "selected_pool_count", 0) or 0),
        "selected_file_names": [
            str(x) for x in (getattr(interval_diag, "selected_file_names", []) or [])
        ],
    }
    (logs_dir / f"stage2_footage_rotation_diag_{stamp}.json").write_text(
        json.dumps(rotation_diag_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / "stage2_footage_rotation_diag.json").write_text(
        json.dumps(rotation_diag_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # clips_manifest.json — human-readable list of clips used in this job.
    # Use this to identify bad clips by timestamp and add them to FOOTAGE_BLACKLIST_PATH.
    (logs_dir / "clips_manifest.json").write_text(
        json.dumps(interval_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Convenience "latest" names (per job OUT_DIR).
    (logs_dir / "stage2_subtitles.json").write_text(
        json.dumps(subtitles_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / "stage2_style.json").write_text(
        json.dumps(style_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if style_rotation_payload is not None:
        (logs_dir / "stage2_style_rotation.json").write_text(
            json.dumps(style_rotation_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if style_raw_payload is not None:
        (logs_dir / "stage2_style_raw.json").write_text(
            json.dumps(style_raw_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if style_adapter_diag is not None:
        style_diag_obj_latest = {
            "seed_key": selection_seed_key,
            "seed": _seed_from_key_material(selection_seed_key),
            "total_assets": int(style_adapter_diag.total_assets),
            "metadata_rows_merged": int(style_adapter_diag.metadata_rows_merged),
            "mapped_assets": int(style_adapter_diag.mapped_assets),
            "unmapped_assets": int(style_adapter_diag.unmapped_assets),
            "unmapped_file_names": list(unmapped_picker_file_names),
            "mood_filtered_out": int(style_adapter_diag.mood_filtered_out),
            "exclude_filtered_out": int(style_adapter_diag.exclude_filtered_out),
            "scored_assets": int(style_adapter_diag.scored_assets),
            "selected_genre": style_adapter_diag.selected_genre,
            "selected_tag": style_adapter_diag.selected_tag,
            "selected_group_score": float(style_adapter_diag.selected_group_score),
            "selected_group_duration_sec": float(style_adapter_diag.selected_group_duration_sec),
            "selected_group_assets_count": int(style_adapter_diag.selected_group_assets_count),
            "requested_style_id": style_adapter_diag.requested_style_id,
            "requested_style_genre_key": style_adapter_diag.requested_style_genre_key,
            "resolved_style_genre_key": style_adapter_diag.resolved_style_genre_key,
            "resolved_similarity_rank": int(style_adapter_diag.resolved_similarity_rank),
            "similarity_fallback_used": bool(style_adapter_diag.similarity_fallback_used),
            "similarity_chain": list(style_adapter_diag.similarity_chain),
            "top_groups": list(style_adapter_diag.top_groups),
        }
        (logs_dir / "stage2_style_adapter_diag.json").write_text(
            json.dumps(style_diag_obj_latest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (logs_dir / "stage2_switch_timestamps.json").write_text(
        json.dumps(switch_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / "stage2_footage_intervals.json").write_text(
        json.dumps(interval_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / "stage2_footage_selection_trace.json").write_text(
        json.dumps(selection_trace_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (logs_dir / "stage2_footage.json").write_text(
        json.dumps(footage_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(progress_cb, "llm_merge")
    logger.info("stage3_merge_start")

    full_payload = FullPlanPayload.model_validate(
        {
            "audio": stage1_json["audio"],
            "subtitles_mode": subtitles_mode,
            "subtitles": subtitles_payload.model_dump(mode="json"),
            "footage": footage_payload.model_dump(mode="json"),
        }
    )

    (logs_dir / f"gemini_full_plan_merged_{stamp}.json").write_text(
        json.dumps(full_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── F5 Cognition hook («Мысль»): между Stage 2 (футаж) и Stage 3 (JSON для AE).
    #    Если в env есть F5_HOOK_DEVICE — гоняем F5 pipeline (Stage1 текст + Stage2
    #    TTS), грузим .wav в S3 и получаем блок для full_edit_config["f5"].
    #    Нет device → f5_block=None → обычный job не затрагивается.
    #    Хук — опциональное усиление, НЕ должен ронять основной рендер. Любая
    #    ошибка F5 (Gemini 5xx, короткий TTS, focal out-of-bounds, S3, ffmpeg…)
    #    логируется с трейсбеком, и джоб рендерится БЕЗ хука (f5_block=None).
    _emit(progress_cb, "f5_hook")
    f5_block = None
    try:
        from mlcore.hooks.f5_cognition.orchestrator_hook import build_f5_block_if_requested

        f5_block = build_f5_block_if_requested(
            track_path=str(audio_files[0]) if audio_files else "",
            lyrics=str(stage1_json.get("lyrics_text") or ""),
            clip_start_abs_sec=float(stage1_json["audio"]["clip_start_abs"]),
            out_dir=out_dir,
            job_tag=(os.environ.get("JOB_ID") or out_dir.name),
            transcript_words=stage1_json.get("transcript_words"),
            is_prod=(mode == MODE_PROD),
        )
    except Exception:
        logger.exception(
            "f5.hook FAILED — продолжаю рендер БЕЗ хука "
            "(device=%s job=%s)",
            os.environ.get("F5_HOOK_DEVICE") or "<none>",
            os.environ.get("JOB_ID") or out_dir.name,
        )
        f5_block = None

    # ── F4 Cognition («Движение»): visual engagement-bait overlay. If env
    #    F4_HOOK_DEVICE is set, emit a block {device, bpm} that
    #    project_builder injects as an AE overlay JSX. bpm comes from the
    #    hook_aware Stage2 analysis (`bpm` in scope). Optional enhancement —
    #    any failure logs and renders WITHOUT the overlay (f4_block=None).
    f4_block = None
    _f4_device_env = (os.environ.get("F4_HOOK_DEVICE") or "").strip().lower()
    if _f4_device_env:
        try:
            from mlcore.hooks.f4_motion.overlay import F4_DEVICES, LEAD_BY_DEVICE
            if _f4_device_env not in LEAD_BY_DEVICE:
                raise RuntimeError(f"unknown F4_HOOK_DEVICE={_f4_device_env!r}")
            if _f4_device_env not in F4_DEVICES:
                raise RuntimeError(f"F4 device {_f4_device_env!r} not wired yet")
            if bpm is None or not (float(bpm) > 0.0):
                raise RuntimeError("F4 hook requires measured bpm (hook_aware)")
            f4_block = {"device": _f4_device_env, "bpm": float(bpm)}
            logger.info("f4.hook block device=%s bpm=%.2f", _f4_device_env, float(bpm))
        except Exception:
            logger.exception(
                "f4.hook FAILED — render without overlay (device=%s job=%s)",
                _f4_device_env,
                os.environ.get("JOB_ID") or out_dir.name,
            )
            f4_block = None

    # ── F3 «Эффект»: visual FX overlay (hook/transition/extra + sound + logo).
    #    Env F3_HOOK / F3_TRANSITION / F3_EXTRA (+ F3_HOOK_EXTEND) выбирают эффекты;
    #    drop_time — COMP-relative (= USER_DROP_T - clip_start_abs), как у f5.
    #    Опциональное усиление — любая ошибка логируется и рендер идёт БЕЗ fx.
    f3_block = None
    _f3_hook = (os.environ.get("F3_HOOK") or "").strip().lower()
    _f3_trans = (os.environ.get("F3_TRANSITION") or "").strip().lower()
    _f3_extra = (os.environ.get("F3_EXTRA") or "").strip().lower()
    if _f3_hook or _f3_trans or _f3_extra:
        try:
            from mlcore.hooks.f3_effect.overlay import F3_HOOKS, F3_TRANSITIONS, F3_EXTRAS
            if _f3_hook and _f3_hook not in F3_HOOKS:
                raise RuntimeError(f"unknown F3_HOOK={_f3_hook!r}")
            if _f3_trans and _f3_trans not in F3_TRANSITIONS:
                raise RuntimeError(f"unknown F3_TRANSITION={_f3_trans!r}")
            if _f3_extra and _f3_extra not in F3_EXTRAS:
                raise RuntimeError(f"unknown F3_EXTRA={_f3_extra!r}")
            _cs = float(stage1_json["audio"]["clip_start_abs"])
            _udt = (os.environ.get("USER_DROP_T") or "").strip()
            if not _udt:
                raise RuntimeError("F3 fx requires USER_DROP_T (drop anchor)")
            _drop_rel = float(_udt) - _cs
            if not (_drop_rel > 0.0):
                raise RuntimeError(
                    f"F3 drop_rel must be > 0 (USER_DROP_T={_udt}, clip_start={_cs})"
                )
            f3_block = {
                "hook": _f3_hook or None,
                "transition": _f3_trans or None,
                "extra": _f3_extra or None,
                "hook_extend": (os.environ.get("F3_HOOK_EXTEND") or "").strip().lower() or None,
                "drop_time": _drop_rel,
                # filled by asset_picker below; empty => visual only (silent slots)
                "assets": {},
                # download list для рендер-ноды; project_builder перепишет в payload.f3_media
                "_media": [],
            }
            # S3-резолв звуков/лого по манифесту (FX_ASSETS_S3_BUCKET). Падение
            # не валит рендер — fx будет без звука/лого, визуал работает.
            try:
                from mlcore.hooks.f3_effect.asset_picker import resolve_assets as _f3_resolve_assets
                _seed = (
                    os.environ.get("STAGE2_SELECTION_SEED")
                    or os.environ.get("JOB_ID")
                    or out_dir.name
                )
                _resolved = _f3_resolve_assets(
                    hook=_f3_hook or None,
                    transition=_f3_trans or None,
                    extra=_f3_extra or None,
                    seed=str(_seed),
                )
                f3_block["assets"] = dict(_resolved.get("assets") or {})
                f3_block["_media"] = list(_resolved.get("media") or [])
            except Exception:
                logger.exception(
                    "f3.assets resolve failed — render without sound/logo (job=%s)",
                    os.environ.get("JOB_ID") or out_dir.name,
                )
            logger.info(
                "f3.fx block hook=%s trans=%s extra=%s drop_rel=%.3f slots=%d media=%d",
                _f3_hook or "-", _f3_trans or "-", _f3_extra or "-", _drop_rel,
                len(f3_block.get("assets") or {}), len(f3_block.get("_media") or []),
            )
        except Exception:
            logger.exception(
                "f3.fx FAILED — render without fx (job=%s)",
                os.environ.get("JOB_ID") or out_dir.name,
            )
            f3_block = None

    # ── F2 «Объект»: packaged-combo overlay (shape на pre-drop склейках +
    #    hook_light на дропе + рандомный F3-переход на post-drop склейках).
    #    Env F2_SHAPE выбирает форму; drop_time COMP-relative (= USER_DROP_T −
    #    clip_start_abs). Seed = F2_SEED или job_id-derived (детерминизм).
    #    Опциональное усиление — любая ошибка логируется и рендер идёт БЕЗ f2.
    f2_block = None
    _f2_shape = (os.environ.get("F2_SHAPE") or "").strip().lower()
    if _f2_shape:
        try:
            from mlcore.hooks.f2_object.overlay import F2_SHAPES
            if _f2_shape not in F2_SHAPES:
                raise RuntimeError(f"unknown F2_SHAPE={_f2_shape!r}; allowed={list(F2_SHAPES)}")
            _cs = float(stage1_json["audio"]["clip_start_abs"])
            _udt = (os.environ.get("USER_DROP_T") or "").strip()
            if not _udt:
                raise RuntimeError("F2 combo requires USER_DROP_T (drop anchor)")
            _drop_rel_f2 = float(_udt) - _cs
            if not (_drop_rel_f2 > 0.0):
                raise RuntimeError(
                    f"F2 drop_rel must be > 0 (USER_DROP_T={_udt}, clip_start={_cs})"
                )
            _seed_env = (os.environ.get("F2_SEED") or "").strip()
            if _seed_env:
                _f2_seed = int(_seed_env) & 0xFFFFFFFF
            else:
                # Derive a stable 32-bit seed from job id (fall back to out_dir
                # name) so reruns of the same job pick the same post-drop
                # transitions.
                _seed_src = os.environ.get("JOB_ID") or out_dir.name
                _f2_seed = abs(hash(("f2", _seed_src))) & 0xFFFFFFFF
            f2_block = {
                "shape": _f2_shape,
                "drop_time": _drop_rel_f2,
                "seed": int(_f2_seed),
            }
            logger.info(
                "f2.combo block shape=%s drop_rel=%.3f seed=%d",
                _f2_shape, _drop_rel_f2, _f2_seed,
            )
        except Exception:
            logger.exception(
                "f2.combo FAILED — render without f2 (shape=%s job=%s)",
                _f2_shape, os.environ.get("JOB_ID") or out_dir.name,
            )
            f2_block = None

    # ── F1 «Звук»: user-uploaded pre-drop sound + F2-style visual combo.
    #    Env F1_SOUND_URL — S3/HTTP ссылка на загруженный пользователем звук;
    #    drop_time COMP-relative (= USER_DROP_T − clip_start). Seed = F2_SEED env
    #    или job-derived (визуал — тот же combo, что у f2). Аудио ляжет в окно
    #    [0.5, drop−0.5]. Любая ошибка → лог + рендер БЕЗ f1.
    f1_block = None
    _f1_sound = (os.environ.get("F1_SOUND_URL") or "").strip()
    if _f1_sound:
        try:
            _cs = float(stage1_json["audio"]["clip_start_abs"])
            _udt = (os.environ.get("USER_DROP_T") or "").strip()
            if not _udt:
                raise RuntimeError("F1 combo requires USER_DROP_T (drop anchor)")
            _drop_rel_f1 = float(_udt) - _cs
            # Need room for the [0.5, drop-0.5] audio window + a real post-drop.
            if not (_drop_rel_f1 > 1.0):
                raise RuntimeError(
                    f"F1 drop_rel must be > 1.0 (USER_DROP_T={_udt}, clip_start={_cs})"
                )
            _seed_env_f1 = (os.environ.get("F2_SEED") or "").strip()
            if _seed_env_f1:
                _f1_seed = int(_seed_env_f1) & 0xFFFFFFFF
            else:
                _seed_src_f1 = os.environ.get("JOB_ID") or out_dir.name
                _f1_seed = abs(hash(("f1", _seed_src_f1))) & 0xFFFFFFFF
            f1_block = {
                "sound_url": _f1_sound,
                "drop_time": _drop_rel_f1,
                "seed": int(_f1_seed),
            }
            logger.info(
                "f1.combo block sound=%s drop_rel=%.3f seed=%d",
                _f1_sound[:80], _drop_rel_f1, _f1_seed,
            )
        except Exception:
            logger.exception(
                "f1.combo FAILED — render without f1 (job=%s)",
                os.environ.get("JOB_ID") or out_dir.name,
            )
            f1_block = None

    outputs = render_all_steps(
        repo_root=ROOT,
        plan=full_payload,
        footage_inventory_json=inv_path,
        out_dir=out_dir,
        data_dir=Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve(),
        f5_block=f5_block,
        f4_block=f4_block,
        f3_block=f3_block,
        f2_block=f2_block,
        f1_block=f1_block,
    )

    logger.info("render_done %s", {k: str(v) for k, v in outputs.items()})
    return outputs
