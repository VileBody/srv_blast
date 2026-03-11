# mlcore/gemini_orchestrator.py
from __future__ import annotations

import json
from json import JSONDecodeError
import os
import re
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
    call_stage1_scenario_once,
    call_subtitles_tagged_once,
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
    build_style_groups_from_assets,
    build_intervals_from_switch_points,
    load_picker_assets_from_inventory,
    pick_footage_clips_by_intervals_deterministic,
    validate_style_pick_in_groups,
)
from mlcore.gemini_postprocess import render_all_steps
from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload
from mlcore.models.stage1_plan import FragmentAnalytics, Stage1PlanPayload
from mlcore.models.stage1_plan import TranscriptWord
from mlcore.models.stage1_scenario import Stage1ScenarioPayload
from mlcore.models.subtitles_spans import BlocksTokenSpansPayload, TokenSpan
from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.models.tagged_subtitles import TaggedSubtitlesPayload
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
    build_stage2_subtitles_tagged_system_instruction,
    build_stage2_subtitles_tagged_user_prompt,
    build_stage2_subtitles_user_prompt,
    build_stage2_timing_analysis_system_instruction,
    build_stage2_timing_analysis_user_prompt,
    build_stage2_timing_cuts_system_instruction,
    build_stage2_timing_cuts_user_prompt,
)
from mlcore.stage1_tools import align_stage1_draft_to_transcript, build_stage1_report
from core.runtime_mode import get_runtime_mode, MODE_DEV, MODE_PROD


ROOT = Path(__file__).resolve().parent.parent
MODEL_VALIDATION_IMMEDIATE_RETRIES = 2


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


def _make_client(*, api_key: str, model: str, proxy: str, temperature: float, timeout_s: float, logger: logging.Logger) -> GeminiClient:
    return GeminiClient(
        GeminiSettings(
            api_key=api_key,
            model=model,
            temperature=temperature,
            proxy=proxy,
            timeout_s=timeout_s,
            max_attempts=1,
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


def _resolve_text_subtitle_preset() -> str:
    raw = (os.environ.get("TEXT_SUBTITLE_PRESET") or "classic").strip().lower()
    if raw not in {"classic", "impulse"}:
        raise RuntimeError(
            f"Invalid TEXT_SUBTITLE_PRESET: {raw!r}; allowed=['classic','impulse']"
        )
    return raw


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


def _reference_words_from_user_text(text: str) -> List[str]:
    out: List[str] = []
    for raw in str(text or "").replace("\r", " ").split():
        w = re.sub(r"^[^\w]+|[^\w]+$", "", raw, flags=re.UNICODE).strip()
        if w:
            out.append(w)
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
    for idx, (got, expected) in enumerate(zip(payload.aligned_words, reference_words)):
        got_norm = _normalize_forced_word_compare(got.text)
        exp_norm = _normalize_forced_word_compare(expected)
        if not got_norm:
            logger.warning("stage1a_forced_empty_word idx=%d got=%r (continuing)", idx, got.text)
            mismatch_count += 1
            continue
        if got_norm != exp_norm:
            logger.warning(
                "stage1a_forced_text_mismatch idx=%d got=%r expected=%r (continuing)",
                idx,
                got.text,
                expected,
            )
            mismatch_count += 1
    if mismatch_count > 0:
        logger.warning("stage1a_forced_text_mismatch_total count=%d (continuing)", mismatch_count)


def _stage1_asr_from_forced_alignment(payload: Stage1ForcedAlignmentPayload) -> Stage1AsrPayload:
    transcript_words = [
        {
            "text": str(w.text),
            "t_start": float(w.t_start),
            "t_end": float(w.t_end),
        }
        for w in payload.aligned_words
    ]
    return Stage1AsrPayload.model_validate(
        {
            "transcript_words": transcript_words,
            "srt_items": [],
        }
    )


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

    forced_start = float(analytics.working_start_abs)
    forced_end = float(analytics.working_end_abs)
    if abs(float(audio_start_abs) - forced_start) > 1e-6 or abs(float(audio_end_abs) - forced_end) > 1e-6:
        logger.warning(
            "stage1b_fragment_window_forced audio=%.3f..%.3f analytics=%.3f..%.3f",
            float(audio_start_abs),
            float(audio_end_abs),
            forced_start,
            forced_end,
        )

    relation = str(analytics.relation_to_target)
    action = str(analytics.chosen_action)
    expected = {
        "inside_13_18": "none",
        "wider": "expand",
        "narrower": "select_subfragment",
    }
    exp = expected.get(relation)
    if exp is None:
        raise ValueError(f"fragment_analytics.relation_to_target unsupported: {relation!r}")
    if action != exp:
        raise ValueError(
            "fragment_analytics.chosen_action is inconsistent with relation_to_target "
            f"(relation={relation!r} action={action!r} expected={exp!r})"
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
    if "fragment_analytics" in lo and "target_fragment" in lo:
        return True
    if "fragment_analytics.chosen_action is inconsistent with relation_to_target" in lo:
        return True
    if "fragment_analytics.relation_to_target unsupported" in lo:
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
    return False


def _is_model_validation_error(exc: BaseException) -> bool:
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


def _log_tagged_subtitles_metrics(payload: TaggedSubtitlesPayload) -> None:
    log = logging.getLogger("mlcore.gemini_orchestrator")
    items = list(payload.subtitles)
    log.info(
        "tagged_subtitles_summary count=%d clip=%.3f..%.3f",
        len(items),
        float(payload.clip_start_abs),
        float(payload.clip_end_abs),
    )
    for i, it in enumerate(items):
        log.info(
            "tagged_subtitle[%d] tag=%s in=%.3f out=%.3f text=%r",
            i,
            str(it.tag),
            float(it.in_abs),
            float(it.out_abs),
            str(it.text),
        )


def _first_token_word(text: str) -> str:
    words = _norm_words_for_lex_compare(text)
    if words:
        return str(words[0])
    fallback = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return fallback or "word"


def _tagged_item_to_segment_dict(
    *,
    item: Any,
    clip_start_abs: float,
    clip_end_abs: float,
    trailing: str,
) -> Dict[str, Any]:
    t_start = max(float(clip_start_abs), float(item.in_abs))
    t_end = min(float(clip_end_abs), float(item.out_abs))
    if t_end <= t_start + 1e-6:
        t_end = min(float(clip_end_abs), t_start + 0.05)
        if t_end <= t_start + 1e-6:
            t_start = max(float(clip_start_abs), float(clip_end_abs) - 0.06)
            t_end = float(clip_end_abs) - 0.01
    tok_text = _first_token_word(str(item.text))
    return {
        "phrase": str(item.text),
        "tokens": [
            {
                "text": tok_text,
                "t_start": float(t_start),
                "t_end": float(t_end),
                "trailing": trailing,
            }
        ],
    }


def _build_proxy_blocks_from_tagged(
    *,
    payload: TaggedSubtitlesPayload,
    clip_start_abs: float,
    clip_end_abs: float,
) -> BlocksTokensPayload:
    items = list(payload.subtitles)
    if not items:
        raise ValueError("Tagged subtitles are empty")

    def pick(idx: int) -> Any:
        if idx < len(items):
            return items[idx]
        return items[-1]

    b5_glitch_item = pick(8)
    b5_glitch_seg = _tagged_item_to_segment_dict(
        item=b5_glitch_item,
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        trailing=" ",
    )

    g = b5_glitch_seg["tokens"][0]
    gs = float(g["t_start"])
    ge = float(g["t_end"])
    eps = 1e-6

    mine_item = None
    for cand in items:
        cs = float(cand.in_abs)
        ce = float(cand.out_abs)
        overlaps = (cs < ge - eps) and (gs < ce - eps)
        if not overlaps:
            mine_item = cand
            break

    if mine_item is None:
        mine_start = max(float(clip_start_abs), float(clip_end_abs) - 0.12)
        mine_end = min(float(clip_end_abs), mine_start + 0.08)
        if mine_end <= mine_start + 1e-6:
            mine_end = min(float(clip_end_abs), mine_start + 0.05)
        mine_word = "mine"
        mine_seg = {
            "phrase": mine_word,
            "tokens": [
                {
                    "text": mine_word,
                    "t_start": float(mine_start),
                    "t_end": float(mine_end),
                    "trailing": "",
                }
            ],
        }
    else:
        mine_seg = _tagged_item_to_segment_dict(
            item=mine_item,
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            trailing="",
        )
        mine_word = _first_token_word(str(mine_item.text))
        mine_seg["phrase"] = mine_word
        mine_seg["tokens"][0]["text"] = mine_word

    obj: Dict[str, Any] = {
        "clip": {
            "start": float(clip_start_abs),
            "end": float(clip_end_abs),
        },
        "block_1": _tagged_item_to_segment_dict(
            item=pick(0), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
        ),
        "block_2": {
            "p1": _tagged_item_to_segment_dict(
                item=pick(1), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
            "p2": _tagged_item_to_segment_dict(
                item=pick(2), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
        },
        "block_3": _tagged_item_to_segment_dict(
            item=pick(3), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
        ),
        "block_4": {
            "p1": _tagged_item_to_segment_dict(
                item=pick(4), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
            "p2": _tagged_item_to_segment_dict(
                item=pick(5), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
        },
        "block_5": {
            "slowly_in": _tagged_item_to_segment_dict(
                item=pick(6), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
            "fast_reveal": _tagged_item_to_segment_dict(
                item=pick(7), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
            "glitch_peak": b5_glitch_seg,
            "mine": mine_seg,
        },
        "block_6": _tagged_item_to_segment_dict(
            item=pick(9), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
        ),
        "block_7": {
            "part1": _tagged_item_to_segment_dict(
                item=pick(10), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
            "part2": _tagged_item_to_segment_dict(
                item=pick(11), clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, trailing=" "
            ),
        },
    }
    return BlocksTokensPayload.model_validate(obj)


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
        "footage_interval_picker style=%s/%s intervals=%d max_interval=%.3f "
        "pool_primary=%d pool_selected=%d widen=%s seed=%d seed_key=%s",
        diagnostics.genre,
        diagnostics.tag,
        diagnostics.intervals_count,
        diagnostics.max_interval_sec,
        diagnostics.primary_pool_count,
        diagnostics.selected_pool_count,
        diagnostics.widened_to_genre,
        diagnostics.deterministic_seed,
        diagnostics.seed_key,
    )
    logger.info(
        "footage_interval_picker selected_file_names_count=%d file_names=%s",
        len(diagnostics.selected_file_names),
        diagnostics.selected_file_names,
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
    proxy = os.environ.get("OUTBOUND_PROXY", "").strip()
    temperature = _float_env("GEMINI_TEMPERATURE", 0.0)
    timeout_s = _float_env("GEMINI_TIMEOUT_S", 120.0)
    provider_mode = normalize_provider_mode(os.environ.get("LLM_PROVIDER_MODE", PROVIDER_MODE_GEMINI))
    hedge_delay_s = _float_env("LLM_HEDGE_DELAY_S", 60.0)
    timing_mode = _require_choice_env("STAGE2_TIMING_MODE", allowed=["prompts", "hybrid"])
    fast_start_seconds = _require_float_env("STAGE2_FAST_START_SECONDS")
    text_subtitle_preset = _resolve_text_subtitle_preset()
    if fast_start_seconds < 0.0:
        raise RuntimeError(f"Invalid STAGE2_FAST_START_SECONDS: {fast_start_seconds!r}")

    logger = _get_logger()
    mode = get_runtime_mode()
    if mode not in {MODE_DEV, MODE_PROD}:
        raise RuntimeError(f"Unsupported MODE={mode!r}")

    if provider_mode in {PROVIDER_MODE_GEMINI, PROVIDER_MODE_HEDGED} and not gemini_api_key:
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
    openrouter_api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    openrouter_timeout_s = _float_env("OPENROUTER_TIMEOUT_S", timeout_s)
    if provider_mode in {PROVIDER_MODE_OPENROUTER, PROVIDER_MODE_HEDGED} and not openrouter_api_key:
        raise RuntimeError(
            "Missing OPENROUTER_API_KEY in env for LLM_PROVIDER_MODE=openrouter|hedged"
        )

    logger.info(
        "llm_provider_config mode=%s hedge_delay_s=%s gemini_timeout_s=%s openrouter_timeout_s=%s "
        "timing_mode=%s fast_start_seconds=%.3f text_preset=%s",
        provider_mode,
        hedge_delay_s,
        timeout_s,
        openrouter_timeout_s,
        timing_mode,
        fast_start_seconds,
        text_subtitle_preset,
    )

    client_stage1_asr: Optional[GeminiClient] = None
    client_stage1_forced: Optional[GeminiClient] = None
    client_stage1_scenario: Optional[GeminiClient] = None
    client_subtitles: Optional[GeminiClient] = None
    client_footage: Optional[GeminiClient] = None
    client_timing: Optional[GeminiClient] = None
    if provider_mode in {PROVIDER_MODE_GEMINI, PROVIDER_MODE_HEDGED}:
        client_stage1_asr = _make_client(
            api_key=gemini_api_key,
            model=model_stage1_asr,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
        )
        client_stage1_forced = _make_client(
            api_key=gemini_api_key,
            model=model_stage1_asr,
            proxy=proxy,
            temperature=0.0,
            timeout_s=timeout_s,
            logger=logger,
        )
        client_stage1_scenario = _make_client(
            api_key=gemini_api_key,
            model=model_stage1_scenario,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
        )
        client_subtitles = _make_client(
            api_key=gemini_api_key,
            model=model_subtitles,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
        )
        client_footage = _make_client(
            api_key=gemini_api_key,
            model=model_footage,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
        )
        client_timing = _make_client(
            api_key=gemini_api_key,
            model=model_stage1_base,
            proxy=proxy,
            temperature=temperature,
            timeout_s=timeout_s,
            logger=logger,
        )

    openrouter_stage1_asr: Optional[OpenRouterClient] = None
    openrouter_stage1_forced: Optional[OpenRouterClient] = None
    openrouter_stage1_scenario: Optional[OpenRouterClient] = None
    openrouter_subtitles: Optional[OpenRouterClient] = None
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

    out_dir = Path(os.environ.get("OUT_DIR", str(ROOT / "out"))).resolve()
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
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

    use_cache = (os.environ.get("GEMINI_UPLOAD_CACHE", "1") or "1").strip() not in {"0", "false", "False", "no", "NO"}
    cache_path = (out_dir / "gemini_files_cache.json") if use_cache else None

    stamp = _stamp()
    lyrics_text = str(os.environ.get("LYRICS_TEXT") or "").strip()
    target_fragment = str(os.environ.get("TARGET_FRAGMENT") or "").strip()
    forced_reference_text = lyrics_text or target_fragment
    forced_reference_words = _reference_words_from_user_text(forced_reference_text)
    if forced_reference_text and not forced_reference_words:
        raise RuntimeError(
            "Reference text for forced alignment is present but empty after tokenization "
            "(LYRICS_TEXT/TARGET_FRAGMENT)."
        )
    use_forced_alignment = bool(forced_reference_words)
    stage1a_mode = "forced_alignment" if use_forced_alignment else "asr"

    _emit(progress_cb, "llm_stage1a_asr")
    logger.info(
        "stage1a_start mode=%s model=%s reference_words=%d",
        stage1a_mode,
        model_stage1_asr,
        len(forced_reference_words),
    )

    if use_forced_alignment:
        stage1a_system = build_stage1a_forced_alignment_system_instruction()
        stage1a_prompt = build_stage1a_forced_alignment_user_prompt(
            reference_text=forced_reference_text,
            schema_name="Stage1ForcedAlignmentPayload",
        )
        stage1a_raw = logs_dir / f"gemini_raw_stage1_forced_alignment_{stamp}.json"
        stage1a_sys = logs_dir / f"gemini_system_stage1_forced_alignment_{stamp}.txt"
        stage1a_user = logs_dir / f"gemini_prompt_stage1_forced_alignment_{stamp}.txt"
    else:
        stage1a_system = build_stage1a_asr_system_instruction()
        stage1a_prompt = build_stage1a_asr_user_prompt(schema_name="Stage1AsrPayload")
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
            if stage1_asr_reference_cached != forced_reference_text:
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
                len(forced_reference_text),
            )
            resume_state.pop("stage1_asr", None)
            stage1_asr_cached = None

    if isinstance(stage1_asr_cached, dict):
        try:
            stage1_asr = Stage1AsrPayload.model_validate(stage1_asr_cached)
            logger.info("llm_resume_hit stage=stage1a_asr")
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage1a_asr err=%s", str(e))
            resume_state.pop("stage1_asr", None)

    if stage1_asr is None:
        if use_forced_alignment:
            stage1_forced = _run_stage_with_model_validation_retries(
                stage_name="stage1_forced_alignment",
                logger=logger,
                fn=lambda: call_stage1_forced_alignment_once(
                    client=client_stage1_forced,
                    openrouter_client=openrouter_stage1_forced,
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
                ),
            )
            _validate_forced_alignment_payload(
                payload=stage1_forced,
                reference_words=forced_reference_words,
                logger=logger,
            )
            stage1_asr = _stage1_asr_from_forced_alignment(stage1_forced)
            (logs_dir / f"stage1_forced_alignment_{stamp}.json").write_text(
                json.dumps(stage1_forced.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            stage1_asr = _run_stage_with_model_validation_retries(
                stage_name="stage1_asr",
                logger=logger,
                fn=lambda: call_stage1_asr_once(
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
                ),
            )
        resume_state["stage1_asr"] = stage1_asr.model_dump(mode="json")
        resume_state["stage1_asr_mode"] = stage1a_mode
        resume_state["stage1_asr_reference_text"] = forced_reference_text if use_forced_alignment else ""
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    stage1_asr_json = stage1_asr.model_dump(mode="json")
    (logs_dir / f"stage1_asr_{stamp}.json").write_text(
        json.dumps(stage1_asr_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(progress_cb, "llm_stage1b_scenario")
    logger.info("stage1b_start model=%s", model_stage1_scenario)
    fragment_branch_on = bool(target_fragment)
    logger.info(
        "stage1b_fragment_branch enabled=%s target_fragment_chars=%d",
        fragment_branch_on,
        len(target_fragment),
    )

    stage1b_system = build_stage1b_scenario_system_instruction()
    stage1b_base_prompt = build_stage1b_scenario_user_prompt(
        asr_json={
            "transcript_words": stage1_asr_json.get("transcript_words", []),
        },
        target_fragment=target_fragment,
        schema_name="Stage1ScenarioPayload",
    )
    stage1b_sys = logs_dir / f"gemini_system_stage1_scenario_{stamp}.txt"
    stage1b_raw = logs_dir / f"gemini_raw_stage1_scenario_{stamp}.json"
    stage1b_user = logs_dir / f"gemini_prompt_stage1_scenario_{stamp}.txt"

    stage1: Stage1PlanPayload | None = None
    stage1_cached = resume_state.get("stage1_plan")
    if isinstance(stage1_cached, dict):
        try:
            stage1 = Stage1PlanPayload.model_validate(stage1_cached)
            if fragment_branch_on:
                _validate_fragment_analytics_for_target(
                    target_fragment=target_fragment,
                    audio_start_abs=float(stage1.audio.clip_start_abs),
                    audio_end_abs=float(stage1.audio.clip_end_abs),
                    analytics=stage1.fragment_analytics,
                    logger=logger,
                )
            logger.info("llm_resume_hit stage=stage1b_scenario")
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage1b_scenario err=%s", str(e))
            resume_state.pop("stage1_plan", None)

    if stage1 is None:
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
                        target_fragment=target_fragment,
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
                        target_fragment=target_fragment,
                        analytics=stage1_scenario.fragment_analytics,
                    )
                    if mismatch and not exact_retry_used:
                        got_fragment = ""
                        if stage1_scenario.fragment_analytics is not None:
                            got_fragment = str(stage1_scenario.fragment_analytics.target_fragment or "")
                        retry_hint = _build_stage1b_fragment_exact_retry_hint(
                            target_fragment=target_fragment,
                            got_fragment=got_fragment,
                        )
                        logger.warning(
                            "stage1b_fragment_exact_retry_hint_applied expected=%r got=%r hint_chars=%d",
                            target_fragment,
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
                            target_fragment,
                            got_fragment,
                        )

                stage1_candidate = Stage1PlanPayload.model_validate(
                    {
                        "audio": audio_obj,
                        "transcript_words": stage1_asr.transcript_words,
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
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

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
    logger.info(
        "stage2_start subtitles_model=%s footage_style_model=%s timing_model=%s timing_mode=%s text_preset=%s style_groups=%d",
        model_subtitles,
        model_footage,
        model_stage1_base,
        timing_mode,
        text_subtitle_preset,
        len(style_groups),
    )

    use_tagged_subtitles = (text_subtitle_preset == "impulse")
    if use_tagged_subtitles:
        sub_system = build_stage2_subtitles_tagged_system_instruction()
        sub_prompt = build_stage2_subtitles_tagged_user_prompt(
            stage1_json=stage1_json,
            schema_name="TaggedSubtitlesPayload",
        )
        sub_raw = logs_dir / f"gemini_raw_stage2_subtitles_tagged_{stamp}.json"
        sub_sys = logs_dir / f"gemini_system_stage2_subtitles_tagged_{stamp}.txt"
        sub_user = logs_dir / f"gemini_prompt_stage2_subtitles_tagged_{stamp}.txt"
    else:
        sub_system = build_stage2_subtitles_system_instruction()
        sub_prompt = build_stage2_subtitles_user_prompt(stage1_json=stage1_json, schema_name="BlocksTokensPayload")
        sub_raw = logs_dir / f"gemini_raw_stage2_subtitles_{stamp}.json"
        sub_sys = logs_dir / f"gemini_system_stage2_subtitles_{stamp}.txt"
        sub_user = logs_dir / f"gemini_prompt_stage2_subtitles_{stamp}.txt"

    subtitles_retry_hint = (os.environ.get("STAGE2_SUBTITLES_RETRY_HINT") or "").strip()
    if subtitles_retry_hint:
        sub_prompt = (
            str(sub_prompt)
            + "\n\nSUBTITLES_RETRY_HINT:\n"
            + subtitles_retry_hint
            + "\n"
        )
        logger.info(
            "stage2_subtitles_retry_hint_applied mode=%s chars=%d",
            "tagged" if use_tagged_subtitles else "classic",
            len(subtitles_retry_hint),
        )

    foot_system = build_stage2_footage_system_instruction()
    foot_prompt = build_stage2_footage_user_prompt(
        stage1_json=stage1_json,
        style_groups=style_groups,
        schema_name="FootageStylePickPayload",
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

    def _run_subtitles_once_classic() -> BlocksTokensPayload:
        payload = call_subtitles_plan_once(
            client=client_subtitles,
            openrouter_client=openrouter_subtitles,
            provider_mode=provider_mode,
            hedge_delay_s=hedge_delay_s,
            logger=logger,
            system_instruction=sub_system,
            user_prompt=str(sub_prompt),
            # IMPORTANT:
            # Subtitles alignment is done strictly against stage1.transcript_words (ABS timings).
            # Do not attach audio to reduce ambiguity and cost.
            audio_paths=[],
            raw_response_path=sub_raw,
            cache_path=cache_path,
            prompt_dump_path=sub_user,
            system_dump_path=sub_sys,
        )

        # Enforce clip window identity with Stage1 (hard runtime invariant).
        if abs(float(payload.clip.start) - float(stage1.audio.clip_start_abs)) > 1e-6:
            raise ValueError("subtitles.clip.start must equal stage1.audio.clip_start_abs")
        if abs(float(payload.clip.end) - float(stage1.audio.clip_end_abs)) > 1e-6:
            raise ValueError("subtitles.clip.end must equal stage1.audio.clip_end_abs")

        _log_subtitles_token_metrics(payload)
        if target_fragment:
            _log_target_fragment_subtitles_alignment(
                payload=payload,
                target_fragment=target_fragment,
                logger=logger,
            )
        return payload

    def _run_subtitles_classic() -> BlocksTokensPayload:
        return _run_stage_with_model_validation_retries(
            stage_name="stage2_subtitles",
            logger=logger,
            fn=_run_subtitles_once_classic,
        )

    def _run_subtitles_once_tagged() -> TaggedSubtitlesPayload:
        payload = call_subtitles_tagged_once(
            client=client_subtitles,
            openrouter_client=openrouter_subtitles,
            provider_mode=provider_mode,
            hedge_delay_s=hedge_delay_s,
            logger=logger,
            system_instruction=sub_system,
            user_prompt=str(sub_prompt),
            audio_paths=[],
            raw_response_path=sub_raw,
            cache_path=cache_path,
            prompt_dump_path=sub_user,
            system_dump_path=sub_sys,
        )
        if abs(float(payload.clip_start_abs) - float(stage1.audio.clip_start_abs)) > 1e-6:
            raise ValueError("tagged_subtitles.clip_start_abs must equal stage1.audio.clip_start_abs")
        if abs(float(payload.clip_end_abs) - float(stage1.audio.clip_end_abs)) > 1e-6:
            raise ValueError("tagged_subtitles.clip_end_abs must equal stage1.audio.clip_end_abs")
        _log_tagged_subtitles_metrics(payload)
        return payload

    def _run_subtitles_tagged() -> TaggedSubtitlesPayload:
        return _run_stage_with_model_validation_retries(
            stage_name="stage2_subtitles_tagged",
            logger=logger,
            fn=_run_subtitles_once_tagged,
        )

    def _run_subtitles_any() -> Any:
        if use_tagged_subtitles:
            return _run_subtitles_tagged()
        return _run_subtitles_classic()

    def _run_style_once() -> FootageStylePickPayload:
        payload = call_footage_style_once(
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
        )
        validate_style_pick_in_groups(payload, style_groups)
        return payload

    def _run_style() -> FootageStylePickPayload:
        return _run_stage_with_model_validation_retries(
            stage_name="stage2_style",
            logger=logger,
            fn=_run_style_once,
        )

    subtitles_payload: BlocksTokensPayload | None = None
    tagged_subtitles_payload: TaggedSubtitlesPayload | None = None
    style_payload: FootageStylePickPayload | None = None
    subtitles_from_resume = False
    style_from_resume = False
    if use_tagged_subtitles:
        subtitles_cached = resume_state.get("stage2_subtitles_tagged")
        if isinstance(subtitles_cached, dict):
            try:
                tagged_subtitles_payload = TaggedSubtitlesPayload.model_validate(subtitles_cached)
                subtitles_payload = _build_proxy_blocks_from_tagged(
                    payload=tagged_subtitles_payload,
                    clip_start_abs=float(stage1.audio.clip_start_abs),
                    clip_end_abs=float(stage1.audio.clip_end_abs),
                )
                subtitles_from_resume = True
                logger.info("llm_resume_hit stage=stage2_subtitles_tagged")
            except Exception as e:
                logger.warning("llm_resume_bad stage=stage2_subtitles_tagged err=%s", str(e))
                resume_state.pop("stage2_subtitles_tagged", None)
    else:
        subtitles_cached = resume_state.get("stage2_subtitles")
        if isinstance(subtitles_cached, dict):
            try:
                subtitles_payload = BlocksTokensPayload.model_validate(subtitles_cached)
                subtitles_from_resume = True
                logger.info("llm_resume_hit stage=stage2_subtitles")
            except Exception as e:
                logger.warning("llm_resume_bad stage=stage2_subtitles err=%s", str(e))
                resume_state.pop("stage2_subtitles", None)

    style_cached = resume_state.get("stage2_style")
    if isinstance(style_cached, dict):
        try:
            style_payload = FootageStylePickPayload.model_validate(style_cached)
            validate_style_pick_in_groups(style_payload, style_groups)
            style_from_resume = True
            logger.info("llm_resume_hit stage=stage2_style")
        except Exception as e:
            logger.warning("llm_resume_bad stage=stage2_style err=%s", str(e))
            resume_state.pop("stage2_style", None)

    stage2_errors: Dict[str, BaseException] = {}
    if subtitles_payload is None and tagged_subtitles_payload is None and style_payload is None:
        subtitles_any, style_payload, stage2_errors = _run_stage2_parallel_collect(
            _run_subtitles_any,
            _run_style,
        )
        if subtitles_any is not None:
            if use_tagged_subtitles:
                tagged_subtitles_payload = TaggedSubtitlesPayload.model_validate(subtitles_any)
                subtitles_payload = _build_proxy_blocks_from_tagged(
                    payload=tagged_subtitles_payload,
                    clip_start_abs=float(stage1.audio.clip_start_abs),
                    clip_end_abs=float(stage1.audio.clip_end_abs),
                )
            else:
                subtitles_payload = BlocksTokensPayload.model_validate(subtitles_any)
    else:
        if subtitles_payload is None and tagged_subtitles_payload is None:
            try:
                subtitles_any = _run_subtitles_any()
                if use_tagged_subtitles:
                    tagged_subtitles_payload = TaggedSubtitlesPayload.model_validate(subtitles_any)
                    subtitles_payload = _build_proxy_blocks_from_tagged(
                        payload=tagged_subtitles_payload,
                        clip_start_abs=float(stage1.audio.clip_start_abs),
                        clip_end_abs=float(stage1.audio.clip_end_abs),
                    )
                else:
                    subtitles_payload = BlocksTokensPayload.model_validate(subtitles_any)
            except Exception as e:  # noqa: BLE001
                stage2_errors["stage2_subtitles_tagged" if use_tagged_subtitles else "stage2_subtitles"] = e
        if style_payload is None:
            try:
                style_payload = _run_style()
            except Exception as e:  # noqa: BLE001
                stage2_errors["stage2_style"] = e

    if use_tagged_subtitles and "stage2_subtitles" in stage2_errors:
        stage2_errors["stage2_subtitles_tagged"] = stage2_errors.pop("stage2_subtitles")

    state_dirty = False
    if use_tagged_subtitles:
        if tagged_subtitles_payload is not None and not subtitles_from_resume:
            resume_state["stage2_subtitles_tagged"] = tagged_subtitles_payload.model_dump(mode="json")
            state_dirty = True
    else:
        if subtitles_payload is not None and not subtitles_from_resume:
            resume_state["stage2_subtitles"] = subtitles_payload.model_dump(mode="json")
            state_dirty = True
    if style_payload is not None and not style_from_resume:
        resume_state["stage2_style"] = style_payload.model_dump(mode="json")
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
    bpm: Optional[float] = None
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
    else:
        logger.info("stage2_bpm_skipped mode=%s source=gemini_only", timing_mode)

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
        timing_semantic_subtitles_json = (
            tagged_subtitles_payload.model_dump(mode="json")
            if tagged_subtitles_payload is not None
            else subtitles_payload.model_dump(mode="json")
        )
        timing_analysis_prompt = build_stage2_timing_analysis_user_prompt(
            stage1_json=stage1_json,
            subtitles_json=timing_semantic_subtitles_json,
            bpm=bpm,
            fast_start_seconds=float(fast_start_seconds),
            timing_mode=timing_mode,
            schema_name="Stage2TimingAnalysisPayload",
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
            stage1_json=stage1_json,
            timing_analysis_json=timing_analysis_payload.model_dump(mode="json"),
            bpm=bpm,
            fast_start_seconds=float(fast_start_seconds),
            timing_mode=timing_mode,
            schema_name="Stage2TimingCutsPayload",
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
        resume_state["stage2_switch_timestamps"] = switch_payload.model_dump(mode="json")
        _save_resume_state(resume_state_path, logger=logger, state=resume_state)

    if switch_payload is None:
        raise RuntimeError("Stage2 failed: switch timing payload is empty")

    seed_key = _resolve_footage_seed_key(out_dir=out_dir, logger=logger)
    footage_payload, interval_diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style_payload,
        assets=picker_assets,
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        switch_points_abs=list(switch_payload.switch_points_abs),
        seed_key=seed_key,
        fit_mode="cover",
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
    if tagged_subtitles_payload is not None:
        (logs_dir / f"stage2_subtitles_tagged_{stamp}.json").write_text(
            json.dumps(tagged_subtitles_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (logs_dir / f"stage2_style_{stamp}.json").write_text(
        json.dumps(style_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
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
    # Convenience "latest" names (per job OUT_DIR).
    (logs_dir / "stage2_subtitles.json").write_text(
        json.dumps(subtitles_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if tagged_subtitles_payload is not None:
        (logs_dir / "stage2_subtitles_tagged.json").write_text(
            json.dumps(tagged_subtitles_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (logs_dir / "stage2_style.json").write_text(
        json.dumps(style_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
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
    (logs_dir / "stage2_footage.json").write_text(
        json.dumps(footage_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(progress_cb, "llm_merge")
    logger.info("stage3_merge_start")

    full_payload_obj: Dict[str, Any] = {
        "audio": stage1_json["audio"],
        "subtitles": subtitles_payload.model_dump(mode="json"),
        "footage": footage_payload.model_dump(mode="json"),
    }
    if tagged_subtitles_payload is not None:
        full_payload_obj["tagged_subtitles"] = tagged_subtitles_payload.model_dump(mode="json")
    full_payload = FullPlanPayload.model_validate(full_payload_obj)

    (logs_dir / f"gemini_full_plan_merged_{stamp}.json").write_text(
        json.dumps(full_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    outputs = render_all_steps(
        repo_root=ROOT,
        plan=full_payload,
        footage_inventory_json=inv_path,
        out_dir=out_dir,
        data_dir=Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve(),
    )

    logger.info("render_done %s", {k: str(v) for k, v in outputs.items()})
    return outputs
