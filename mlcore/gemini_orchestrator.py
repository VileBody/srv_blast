# mlcore/gemini_orchestrator.py
from __future__ import annotations

import json
import os
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
import logging

from mlcore.gemini_call import (
    call_footage_style_once,
    call_stage1_asr_once,
    call_stage1_scenario_once,
    call_subtitles_plan_once,
    pick_audio_files,
)
from mlcore.gemini_client import GeminiClient, GeminiSettings
from mlcore.footage_picker import (
    FootagePickerDiagnostics,
    build_style_groups_from_assets,
    load_picker_assets_from_inventory,
    pick_footage_clips_deterministic,
    validate_style_pick_in_groups,
)
from mlcore.gemini_postprocess import render_all_steps
from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.stage1_plan import TranscriptWord
from mlcore.models.stage1_scenario import Stage1ScenarioPayload
from mlcore.models.subtitles_spans import BlocksTokenSpansPayload, TokenSpan
from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.prompts import (
    build_stage1a_asr_system_instruction,
    build_stage1a_asr_user_prompt,
    build_stage1b_scenario_system_instruction,
    build_stage1b_scenario_user_prompt,
    build_stage2_footage_system_instruction,
    build_stage2_footage_user_prompt,
    build_stage2_subtitles_system_instruction,
    build_stage2_subtitles_user_prompt,
)
from mlcore.stage1_tools import align_stage1_draft_to_transcript, build_stage1_report
from core.runtime_mode import get_runtime_mode, MODE_DEV, MODE_PROD


ROOT = Path(__file__).resolve().parent.parent


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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


def _emit(progress_cb: Optional[Callable[[str], None]], stage: str) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(stage)
    except Exception:
        pass


T = TypeVar("T")
U = TypeVar("U")


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


def _log_footage_picker_diagnostics(*, logger: logging.Logger, diagnostics: FootagePickerDiagnostics) -> None:
    logger.info(
        "footage_picker style=%s/%s target_duration=%.3f primary_pool_duration=%.3f selected_pool_duration=%.3f "
        "widen=%s repeats=%s seed=%d seed_key=%s",
        diagnostics.genre,
        diagnostics.tag,
        diagnostics.target_duration_sec,
        diagnostics.primary_pool_duration_sec,
        diagnostics.selected_pool_duration_sec,
        diagnostics.widened_to_genre,
        diagnostics.repeats_used,
        diagnostics.deterministic_seed,
        diagnostics.seed_key,
    )
    logger.info(
        "footage_picker selected_file_names_count=%d file_names=%s",
        len(diagnostics.selected_file_names),
        diagnostics.selected_file_names,
    )


def build_all_via_gemini_one_call(*, progress_cb: Optional[Callable[[str], None]] = None) -> Dict[str, Path]:
    """
    Backward-compatible function name; implementation is now staged:
      - stage1: ASR + audio window + scenario draft
      - stage2 (parallel): subtitles and footage
      - stage3: merge -> FullPlanPayload -> render_all_steps
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    proxy = os.environ.get("OUTBOUND_PROXY", "").strip()
    temperature = float(os.environ.get("GEMINI_TEMPERATURE", "0") or "0")
    timeout_s = float(os.environ.get("GEMINI_TIMEOUT_S", "120") or "120")

    logger = _get_logger()
    mode = get_runtime_mode()
    if mode not in {MODE_DEV, MODE_PROD}:
        raise RuntimeError(f"Unsupported MODE={mode!r}")

    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in env")

    # Explicit-only model contract:
    # - GEMINI_MODEL_STAGE1 is required (base).
    # - Optional overrides: GEMINI_MODEL_STAGE1_ASR / GEMINI_MODEL_STAGE1_SCENARIO.
    model_stage1_base = _require_model("GEMINI_MODEL_STAGE1")
    model_stage1_asr = (os.environ.get("GEMINI_MODEL_STAGE1_ASR") or model_stage1_base).strip()
    model_stage1_scenario = (os.environ.get("GEMINI_MODEL_STAGE1_SCENARIO") or model_stage1_base).strip()
    model_subtitles = _require_model("GEMINI_MODEL_SUBTITLES")
    model_footage = _require_model("GEMINI_MODEL_FOOTAGE")

    client_stage1_asr = _make_client(
        api_key=api_key,
        model=model_stage1_asr,
        proxy=proxy,
        temperature=temperature,
        timeout_s=timeout_s,
        logger=logger,
    )
    client_stage1_scenario = _make_client(
        api_key=api_key,
        model=model_stage1_scenario,
        proxy=proxy,
        temperature=temperature,
        timeout_s=timeout_s,
        logger=logger,
    )
    client_subtitles = _make_client(
        api_key=api_key,
        model=model_subtitles,
        proxy=proxy,
        temperature=temperature,
        timeout_s=timeout_s,
        logger=logger,
    )
    client_footage = _make_client(
        api_key=api_key,
        model=model_footage,
        proxy=proxy,
        temperature=temperature,
        timeout_s=timeout_s,
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

    audio_dir = Path(os.environ.get("AUDIO_DIR", str(ROOT / "audio"))).resolve()
    audio_files = pick_audio_files(audio_dir)
    logger.info("audio_files_selected n=%d files=%s", len(audio_files), [p.name for p in audio_files])

    use_cache = (os.environ.get("GEMINI_UPLOAD_CACHE", "1") or "1").strip() not in {"0", "false", "False", "no", "NO"}
    cache_path = (out_dir / "gemini_files_cache.json") if use_cache else None

    stamp = _stamp()

    _emit(progress_cb, "llm_stage1a_asr")
    logger.info("stage1a_start model=%s", model_stage1_asr)

    stage1a_system = build_stage1a_asr_system_instruction()
    stage1a_prompt = build_stage1a_asr_user_prompt(schema_name="Stage1AsrPayload")
    stage1a_raw = logs_dir / f"gemini_raw_stage1_asr_{stamp}.json"
    stage1a_sys = logs_dir / f"gemini_system_stage1_asr_{stamp}.txt"
    stage1a_user = logs_dir / f"gemini_prompt_stage1_asr_{stamp}.txt"

    stage1_asr: Stage1AsrPayload = call_stage1_asr_once(
        client=client_stage1_asr,
        system_instruction=stage1a_system,
        user_prompt=stage1a_prompt,
        audio_paths=audio_files,
        raw_response_path=stage1a_raw,
        cache_path=cache_path,
        prompt_dump_path=stage1a_user,
        system_dump_path=stage1a_sys,
    )

    stage1_asr_json = stage1_asr.model_dump(mode="json")
    (logs_dir / f"stage1_asr_{stamp}.json").write_text(
        json.dumps(stage1_asr_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(progress_cb, "llm_stage1b_scenario")
    logger.info("stage1b_start model=%s", model_stage1_scenario)

    stage1b_system = build_stage1b_scenario_system_instruction()
    stage1b_base_prompt = build_stage1b_scenario_user_prompt(
        asr_json=stage1_asr_json,
        schema_name="Stage1ScenarioPayload",
    )
    stage1b_sys = logs_dir / f"gemini_system_stage1_scenario_{stamp}.txt"
    stage1b_raw = logs_dir / f"gemini_raw_stage1_scenario_{stamp}.json"
    stage1b_user = logs_dir / f"gemini_prompt_stage1_scenario_{stamp}.txt"
    stage1b_raw_retry = logs_dir / f"gemini_raw_stage1_scenario_retry_{stamp}.json"
    stage1b_user_retry = logs_dir / f"gemini_prompt_stage1_scenario_retry_{stamp}.txt"

    stage1: Stage1PlanPayload | None = None
    stage1_last_exc: Exception | None = None
    for attempt, strict in enumerate((False, True), start=1):
        strict_addendum = (
            "\n\nSTRICT_RETRY_RULES:\n"
            "- Every draft phrase must be copy-pasted from transcript words (no paraphrase).\n"
            "- Keep selected window in 13..18 sec and preserve 1..5 development, 6 fixation, 7 exit arc.\n"
            "- Keep block phrases concise and balanced (target <=6 words, hard cap <=8).\n"
            "- Avoid dangling leftovers: split only at natural phrase boundaries.\n"
            "- Repeats are OK in songs.\n"
        )
        prompt = stage1b_base_prompt + strict_addendum if strict else stage1b_base_prompt
        raw_path = stage1b_raw_retry if strict else stage1b_raw
        prompt_path = stage1b_user_retry if strict else stage1b_user
        try:
            stage1_scenario: Stage1ScenarioPayload = call_stage1_scenario_once(
                client=client_stage1_scenario,
                system_instruction=stage1b_system,
                user_prompt=prompt,
                # IMPORTANT:
                # Stage1B is scenario planning based on Stage1A transcript_words.
                # We do NOT attach audio here to avoid the model "re-listening" and drifting from transcript.
                audio_paths=[],
                raw_response_path=raw_path,
                cache_path=cache_path,
                prompt_dump_path=prompt_path,
                system_dump_path=stage1b_sys,
            )

            stage1_candidate = Stage1PlanPayload.model_validate(
                {
                    "audio": stage1_scenario.audio.model_dump(mode="json"),
                    "transcript_words": stage1_asr.transcript_words,
                    "draft_blocks": stage1_scenario.draft_blocks.model_dump(mode="json"),
                }
            )
            stage1 = stage1_candidate
            # Best-effort alignment report (useful for debugging), but do not fail Stage1 if it can't be aligned.
            report_path = logs_dir / f"stage1_report_{stamp}.txt"
            try:
                align_rows = align_stage1_draft_to_transcript(stage1_candidate)
                report_path.write_text(build_stage1_report(stage1_candidate, align_rows), encoding="utf-8")
            except Exception as e:
                logger.warning("stage1b_align_warning err=%s", str(e))
                report_path.write_text(
                    "STAGE1 ALIGNMENT WARNING (non-fatal)\n"
                    f"err={e}\n\n"
                    "DRAFT_BLOCKS_JSON:\n"
                    + json.dumps(stage1_scenario.draft_blocks.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            break
        except Exception as e:
            stage1_last_exc = e
            logger.warning("stage1b_scenario_invalid attempt=%d strict=%s err=%s", attempt, strict, str(e))
            if attempt >= 2:
                break

    if stage1 is None:
        raise RuntimeError(f"Stage1 scenario validation failed after retry: {stage1_last_exc}")

    stage1_json = stage1.model_dump(mode="json")
    stage1_json["lyrics_text"] = str(os.environ.get("LYRICS_TEXT") or "")
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
        "stage2_start subtitles_model=%s footage_style_model=%s style_groups=%d",
        model_subtitles,
        model_footage,
        len(style_groups),
    )

    sub_system = build_stage2_subtitles_system_instruction()
    sub_prompt = build_stage2_subtitles_user_prompt(stage1_json=stage1_json, schema_name="BlocksTokensPayload")
    sub_raw = logs_dir / f"gemini_raw_stage2_subtitles_{stamp}.json"
    sub_sys = logs_dir / f"gemini_system_stage2_subtitles_{stamp}.txt"
    sub_user = logs_dir / f"gemini_prompt_stage2_subtitles_{stamp}.txt"

    foot_system = build_stage2_footage_system_instruction()
    foot_prompt = build_stage2_footage_user_prompt(
        stage1_json=stage1_json,
        style_groups=style_groups,
        schema_name="FootageStylePickPayload",
    )
    foot_raw = logs_dir / f"gemini_raw_stage2_style_{stamp}.json"
    foot_sys = logs_dir / f"gemini_system_stage2_style_{stamp}.txt"
    foot_user = logs_dir / f"gemini_prompt_stage2_style_{stamp}.txt"

    def _run_subtitles(*, strict: bool) -> BlocksTokensPayload:
        strict_retry_addendum = (
            "\n\nSTRICT_RETRY_RULES:\n"
            "- Output tokens only (text + ABS t_start/t_end) copied from stage1.transcript_words.\n"
            "- Do NOT invent words or timings.\n"
            "- Do NOT reuse the same timed word across segments.\n"
            "- Segments must be strictly increasing in timeline order.\n"
            "- block_5.mine must contain exactly ONE token.\n"
            "- trailing: only \" \" or \"\"; last token trailing must be \"\".\n"
            "- Keep each segment short: target <= 6 words, hard cap <= 8 words.\n"
            "- Keep first line concise: target <= 24 chars.\n"
        )
        prompt = sub_prompt + strict_retry_addendum if strict else sub_prompt
        raw_path = (logs_dir / f"gemini_raw_stage2_subtitles_retry_{stamp}.json") if strict else sub_raw
        prompt_path = (logs_dir / f"gemini_prompt_stage2_subtitles_retry_{stamp}.txt") if strict else sub_user

        payload = call_subtitles_plan_once(
            client=client_subtitles,
            system_instruction=sub_system,
            user_prompt=str(prompt),
            # IMPORTANT:
            # Subtitles alignment is done strictly against stage1.transcript_words (ABS timings).
            # Do not attach audio to reduce ambiguity and cost.
            audio_paths=[],
            raw_response_path=raw_path,
            cache_path=cache_path,
            prompt_dump_path=prompt_path,
            system_dump_path=sub_sys,
        )

        # Enforce clip window identity with Stage1 (hard runtime invariant).
        if abs(float(payload.clip.start) - float(stage1.audio.clip_start_abs)) > 1e-6:
            raise ValueError("subtitles.clip.start must equal stage1.audio.clip_start_abs")
        if abs(float(payload.clip.end) - float(stage1.audio.clip_end_abs)) > 1e-6:
            raise ValueError("subtitles.clip.end must equal stage1.audio.clip_end_abs")

        _log_subtitles_token_metrics(payload)
        return payload

    def _run_style(*, strict: bool) -> FootageStylePickPayload:
        strict_retry_addendum = (
            "\n\nSTRICT_RETRY_RULES:\n"
            "- Output only genre + tag from STYLE_POOL_GROUPS_JSON.\n"
            "- Do NOT output clip timings or file names.\n"
            "- Prefer style groups with enough aggregate duration to cover stage1 audio window.\n"
        )
        prompt = foot_prompt + strict_retry_addendum if strict else foot_prompt
        raw_path = (logs_dir / f"gemini_raw_stage2_style_retry_{stamp}.json") if strict else foot_raw
        prompt_path = (logs_dir / f"gemini_prompt_stage2_style_retry_{stamp}.txt") if strict else foot_user

        payload = call_footage_style_once(
            client=client_footage,
            system_instruction=foot_system,
            user_prompt=str(prompt),
            # Style selection needs only Stage1 context + style pool groups.
            audio_paths=[],
            extra_file_paths=None,
            raw_response_path=raw_path,
            cache_path=cache_path,
            prompt_dump_path=prompt_path,
            system_dump_path=foot_sys,
        )
        validate_style_pick_in_groups(payload, style_groups)
        return payload

    stage2_last_exc: Exception | None = None
    subtitles_payload: BlocksTokensPayload | None = None
    style_payload: FootageStylePickPayload | None = None
    for stage2_attempt, strict in enumerate((False, True), start=1):
        try:
            subtitles_payload, style_payload = _run_stage2_parallel(
                lambda strict=strict: _run_subtitles(strict=strict),
                lambda strict=strict: _run_style(strict=strict),
            )
            break
        except Exception as e:
            stage2_last_exc = e
            logger.warning("stage2_full_attempt_failed attempt=%d strict=%s err=%s", stage2_attempt, strict, str(e))
            if stage2_attempt >= 2:
                break

    if subtitles_payload is None or style_payload is None:
        raise RuntimeError(f"Stage2 failed after one retry: {stage2_last_exc}")

    seed_key = _resolve_footage_seed_key(out_dir=out_dir, logger=logger)
    footage_payload, pick_diag = pick_footage_clips_deterministic(
        style_pick=style_payload,
        assets=picker_assets,
        clip_start_abs=float(stage1.audio.clip_start_abs),
        clip_end_abs=float(stage1.audio.clip_end_abs),
        seed_key=seed_key,
        fit_mode="cover",
    )
    _validate_footage_coverage_abs(
        footage_payload,
        clip_start_abs=float(stage1.audio.clip_start_abs),
        clip_end_abs=float(stage1.audio.clip_end_abs),
    )
    _log_footage_picker_diagnostics(logger=logger, diagnostics=pick_diag)

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
    (logs_dir / f"stage2_footage_{stamp}.json").write_text(
        json.dumps(footage_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
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
    (logs_dir / "stage2_footage.json").write_text(
        json.dumps(footage_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(progress_cb, "llm_merge")
    logger.info("stage3_merge_start")

    full_payload = FullPlanPayload.model_validate(
        {
            "audio": stage1_json["audio"],
            "subtitles": subtitles_payload.model_dump(mode="json"),
            "footage": footage_payload.model_dump(mode="json"),
        }
    )

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
