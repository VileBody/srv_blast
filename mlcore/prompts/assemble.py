# mlcore/prompts/assemble.py
from __future__ import annotations

from typing import Dict, List
import json

from core.clip_window import (
    CLIP_WINDOW_MAX_LABEL,
    CLIP_WINDOW_MIN_LABEL,
    CLIP_WINDOW_RANGE_LABEL,
)
from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    normalize_subtitles_mode,
)
from .step1_audio_window import SYSTEM_PART as S1
from .step2_subtitles import SYSTEM_PART as S2
from .step3_footage import SYSTEM_PART as S3
from .step1_asr_scenario import SYSTEM_PART as STAGE1
from .step1a_asr_only import SYSTEM_PART as STAGE1A_ASR
from .step1a_forced_alignment import SYSTEM_PART as STAGE1A_FORCED_ALIGNMENT
from .step1b_scenario_only import SYSTEM_PART as STAGE1B_SCENARIO
from .step2_subtitles_only import SYSTEM_PART as STAGE2_SUBS
from .stage2_subtitles_impulse_2nd import SYSTEM_PART as STAGE2_SUBS_IMPULSE_2ND
from .stage2_subtitles_scenes_3rd import SYSTEM_PART as STAGE2_SUBS_SCENES_3RD
from .stage2_footage_style_only import SYSTEM_PART as STAGE2_FOOTAGE_STYLE
from .stage2_timing_switches import (
    SYSTEM_BASE_JSON as STAGE2_TIMING_BASE_JSON,
    SYSTEM_FAST_START_BY_BEAT as STAGE2_TIMING_FAST_START,
    SYSTEM_SEMANTIC_AFTER_FAST_START as STAGE2_TIMING_SEMANTIC_AFTER,
    SYSTEM_TIMING_ANALYSIS as STAGE2_TIMING_ANALYSIS,
    SYSTEM_TIMING_CUTS as STAGE2_TIMING_CUTS,
)


def build_system_instruction() -> str:
    """
    Build one big SYSTEM instruction by concatenating 3 modular parts.
    """
    return (
        "You are a multi-stage planner for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + S1.strip()
        + "\n\n"
        + S2.strip()
        + "\n\n"
        + S3.strip()
        + "\n"
    )


def build_user_prompt(*, assets: List[Dict], schema_name: str = "FullPlanPayload") -> str:
    """
    USER prompt strategy (SINGLE SOURCE OF TRUTH):
      - We provide ONE descriptions bundle that also acts as the allow-list.
      - It may be provided either:
          (A) INLINE in the prompt (DESCRIPTIONS_BUNDLE_JSON),
          (B) as an attached TEXT file containing JSON (also named DESCRIPTIONS_BUNDLE_JSON).
      - Model must choose file_name ONLY from that bundle.

    NOTE: 'assets' arg is kept for backward compatibility with existing call sites,
          but we intentionally do NOT embed it in the prompt to avoid token bloat.
    """
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "You will receive ONE descriptions bundle in JSON format, either inline or as an attached TEXT file.\n"
        "Bundle name: DESCRIPTIONS_BUNDLE_JSON.\n\n"
        "DESCRIPTIONS_BUNDLE_JSON format:\n"
        "- JSON array of objects.\n"
        "- Each object MUST contain at least: file_name, src_w, src_h.\n"
        "- It MAY contain technical fields: duration_sec, genre, tag, dominant_color, palette_bins.\n\n"
        "Hard rule:\n"
        "- For footage planning you MUST choose file_name ONLY from DESCRIPTIONS_BUNDLE_JSON.\n"
        "- Do NOT invent new file_name.\n\n"
        "Notes:\n"
        "- Use the same audio track for all steps.\n"
        "- Token times MUST be ABSOLUTE seconds on full track.\n"
        "- Footage clip times MUST be ABSOLUTE seconds on full track (inside the chosen audio window).\n"
        "- For footage planning: you output only file_name + timings; file_path/src_w/src_h will be resolved later.\n"
    )


def build_stage1_system_instruction() -> str:
    return (
        "You are a multi-stage planner for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE1.strip()
        + "\n"
    )


def build_stage1_user_prompt(*, schema_name: str = "Stage1PlanPayload") -> str:
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "Use the attached audio as the source of truth.\n"
        "Output stage-1 plan: audio window + transcript_words + draft_blocks.\n"
    )


def build_stage1a_asr_system_instruction() -> str:
    return (
        "You are an ASR assistant for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE1A_ASR.strip()
        + "\n"
    )


def build_stage1a_asr_user_prompt(
    *,
    schema_name: str = "Stage1AsrPayload",
    require_selected_fragment: bool = False,
    target_fragment: str = "",
) -> str:
    base = (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "Use the attached audio as the source of truth.\n"
        "Output transcript_words for the full track and optional srt_items.\n"
        "All returned timestamps must be ABSOLUTE full-track seconds.\n"
    )
    if not require_selected_fragment:
        return base

    tf = str(target_fragment or "").strip()
    if tf:
        branch = (
            "\nSELECT_FRAGMENT_BRANCH=ON\n"
            "USER_TARGET_FRAGMENT_BRANCH=ON\n"
            "USER_TARGET_FRAGMENT:\n"
            + tf
            + "\n\n"
            "Additionally output selected_fragment with:\n"
            + f"- audio.clip_start_abs / clip_end_abs duration MUST be >= {CLIP_WINDOW_MIN_LABEL}s;\n"
            + f"- duration MAY exceed {CLIP_WINDOW_MAX_LABEL}s when needed to keep USER_TARGET_FRAGMENT fully covered;\n"
            "- selected_fragment.transcript_words only inside that clip window;\n"
            "- selected_fragment.srt_items only inside that clip window (optional);\n"
            "- selected_fragment.transcript_words/srt_items timings MUST stay ABSOLUTE full-track seconds "
            "(same global timeline as transcript_words; do NOT normalize to clip start).\n"
            "- selected_fragment.fragment_analytics is REQUIRED and target_fragment must copy USER_TARGET_FRAGMENT exactly.\n"
            "fragment_analytics semantics:\n"
            "- relation_to_target must be one of: wider | inside_13_30;\n"
            "- chosen_action must be one of: expand | none;\n"
            "- relation_to_target/chosen_action must describe your FINAL selected segment.\n"
            "Selection rules:\n"
            "- maximize overlap of selected clip with USER_TARGET_FRAGMENT;\n"
            + f"- if USER_TARGET_FRAGMENT is shorter than {CLIP_WINDOW_MIN_LABEL}s: expand context around it;\n"
            + f"- if USER_TARGET_FRAGMENT is longer than {CLIP_WINDOW_MAX_LABEL}s: keep the full fragment (do NOT narrow/select subfragment).\n"
            "- do not perform phrase grouping/draft blocks at this stage.\n"
        )
    else:
        branch = (
            "\nSELECT_FRAGMENT_BRANCH=ON\n"
            "USER_TARGET_FRAGMENT_BRANCH=OFF\n"
            "Additionally output selected_fragment with:\n"
            + f"- audio.clip_start_abs / clip_end_abs in {CLIP_WINDOW_RANGE_LABEL} seconds total duration;\n"
            "- selected_fragment.transcript_words only inside that clip window;\n"
            "- selected_fragment.srt_items only inside that clip window (optional).\n"
            "- selected_fragment.transcript_words/srt_items timings MUST stay ABSOLUTE full-track seconds "
            "(same global timeline as transcript_words; do NOT normalize to clip start).\n"
            "Selection rule:\n"
            + f"- choose the most memorable/expressive {CLIP_WINDOW_RANGE_LABEL}s moment in the track.\n"
            "- do not perform phrase grouping/draft blocks at this stage.\n"
        )
    return base + branch


def build_stage1a_forced_alignment_system_instruction() -> str:
    return (
        "You are a forced-alignment ASR assistant for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE1A_FORCED_ALIGNMENT.strip()
        + "\n"
    )


def build_stage1a_forced_alignment_user_prompt(
    *,
    reference_text: str,
    schema_name: str = "Stage1ForcedAlignmentPayload",
    require_selected_fragment: bool = False,
    target_fragment: str = "",
) -> str:
    ref = str(reference_text or "").strip()
    if not ref:
        raise ValueError("reference_text must be non-empty for forced alignment prompt")
    base = (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "Use the attached audio as the source of truth.\n"
        "Align every word in REFERENCE_TEXT and return one timed item per word.\n"
        "REFERENCE_TEXT is the only allowed word source (no extra backing/ad-lib words).\n\n"
        "All returned timestamps must be ABSOLUTE full-track seconds.\n\n"
        "Structural markers like [pause], [bridge], [hook], [verse] are not spoken words.\n"
        "Do not output these markers in aligned_words.\n\n"
        "REFERENCE_TEXT:\n"
        + ref
        + "\n"
    )
    if not require_selected_fragment:
        return base

    tf = str(target_fragment or "").strip()
    if tf:
        branch = (
            "\nSELECT_FRAGMENT_BRANCH=ON\n"
            "USER_TARGET_FRAGMENT_BRANCH=ON\n"
            "USER_TARGET_FRAGMENT:\n"
            + tf
            + "\n\n"
            "Additionally output selected_fragment with:\n"
            + f"- audio.clip_start_abs / clip_end_abs duration MUST be >= {CLIP_WINDOW_MIN_LABEL}s;\n"
            + f"- duration MAY exceed {CLIP_WINDOW_MAX_LABEL}s when needed to keep USER_TARGET_FRAGMENT fully covered;\n"
            "- selected_fragment.transcript_words only inside that clip window;\n"
            "- selected_fragment.srt_items only inside that clip window (optional);\n"
            "- selected_fragment.transcript_words/srt_items timings MUST stay ABSOLUTE full-track seconds "
            "(same global timeline as aligned_words; do NOT normalize to clip start).\n"
            "- selected_fragment.fragment_analytics is REQUIRED and target_fragment must copy USER_TARGET_FRAGMENT exactly.\n"
            "fragment_analytics semantics:\n"
            "- relation_to_target must be one of: wider | inside_13_30;\n"
            "- chosen_action must be one of: expand | none;\n"
            "- relation_to_target/chosen_action must describe your FINAL selected segment.\n"
            "Selection rules:\n"
            "- maximize overlap of selected clip with USER_TARGET_FRAGMENT;\n"
            + f"- if USER_TARGET_FRAGMENT is shorter than {CLIP_WINDOW_MIN_LABEL}s: expand context around it;\n"
            + f"- if USER_TARGET_FRAGMENT is longer than {CLIP_WINDOW_MAX_LABEL}s: keep the full fragment (do NOT narrow/select subfragment).\n"
            "- do not perform phrase grouping/draft blocks at this stage.\n"
        )
    else:
        branch = (
            "\nSELECT_FRAGMENT_BRANCH=ON\n"
            "USER_TARGET_FRAGMENT_BRANCH=OFF\n"
            "Additionally output selected_fragment with:\n"
            + f"- audio.clip_start_abs / clip_end_abs in {CLIP_WINDOW_RANGE_LABEL} seconds total duration;\n"
            "- selected_fragment.transcript_words only inside that clip window;\n"
            "- selected_fragment.srt_items only inside that clip window (optional).\n"
            "- selected_fragment.transcript_words/srt_items timings MUST stay ABSOLUTE full-track seconds "
            "(same global timeline as aligned_words; do NOT normalize to clip start).\n"
            "Selection rule:\n"
            + f"- choose the most memorable/expressive {CLIP_WINDOW_RANGE_LABEL}s moment in the track.\n"
            "- do not perform phrase grouping/draft blocks at this stage.\n"
        )
    return base + branch


def build_stage1b_scenario_system_instruction() -> str:
    return (
        "You are an editorial scenario planner for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE1B_SCENARIO.strip()
        + "\n"
    )


def build_stage1b_scenario_user_prompt(
    *,
    asr_json: Dict[str, object],
    target_fragment: str = "",
    schema_name: str = "Stage1ScenarioPayload",
) -> str:
    base = (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "STAGE1A_ASR_JSON:\n"
        + json.dumps(asr_json, ensure_ascii=False)
    )
    tf = str(target_fragment or "").strip()
    if not tf:
        return base

    branch = (
        "\n\nUSER_TARGET_FRAGMENT_BRANCH=ON\n"
        "USER_TARGET_FRAGMENT:\n"
        + tf
        + "\n\n"
        "UNIVERSAL_RULES_FOR_TARGET_FRAGMENT:\n"
        + f"- Working audio window MUST be >= {CLIP_WINDOW_MIN_LABEL}s.\n"
        + f"- Working audio window MAY exceed {CLIP_WINDOW_MAX_LABEL}s when needed to keep USER_TARGET_FRAGMENT fully covered.\n"
        "- Maximize overlap of the selected working window with USER_TARGET_FRAGMENT.\n"
        + f"- If requested fragment is shorter than {CLIP_WINDOW_MIN_LABEL}s: expand context around it (left/right as needed) while keeping overlap.\n"
        + f"- If requested fragment is longer than {CLIP_WINDOW_MAX_LABEL}s: keep the full fragment (do NOT narrow/select subfragment).\n"
        "- USER_TARGET_FRAGMENT is lexical source of truth for wording in this branch.\n"
        "- If transcript has recognition mistakes, fix wording in draft_blocks to match USER_TARGET_FRAGMENT while preserving timeline/order.\n"
        "- fragment_analytics.target_fragment MUST copy USER_TARGET_FRAGMENT wording exactly (no paraphrase).\n"
        "- Return fragment_analytics and ensure it is consistent with selected audio.clip_start_abs/audio.clip_end_abs.\n"
    )
    return base + branch


def _stage2_subtitles_system_by_mode(mode: str) -> str:
    resolved = normalize_subtitles_mode(mode)
    if resolved == SUBTITLES_MODE_LEGACY_BLOCKS:
        return STAGE2_SUBS
    if resolved == SUBTITLES_MODE_IMPULSE_2ND:
        return STAGE2_SUBS_IMPULSE_2ND
    if resolved == SUBTITLES_MODE_SCENES_3RD:
        return STAGE2_SUBS_SCENES_3RD
    raise ValueError(f"Unsupported subtitles mode: {mode!r}")


def build_stage2_subtitles_system_instruction(*, subtitles_mode: str = SUBTITLES_MODE_LEGACY_BLOCKS) -> str:
    return (
        "You are a subtitle alignment assistant for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n"
        + f"Mode: {normalize_subtitles_mode(subtitles_mode)}\n\n"
        + _stage2_subtitles_system_by_mode(subtitles_mode).strip()
        + "\n"
    )


def build_stage2_subtitles_user_prompt(
    *,
    stage1_json: Dict[str, object],
    schema_name: str = "BlocksTokensPayload",
    subtitles_mode: str = SUBTITLES_MODE_LEGACY_BLOCKS,
) -> str:
    resolved_mode = normalize_subtitles_mode(subtitles_mode)
    # Stage2 subtitles should only deal with the chosen clip window; reduce ambiguity by passing
    # only transcript words that lie inside that window (ABS times).
    audio = stage1_json.get("audio") if isinstance(stage1_json, dict) else None
    cs = float((audio or {}).get("clip_start_abs") or 0.0)
    ce = float((audio or {}).get("clip_end_abs") or 0.0)
    words_in = stage1_json.get("transcript_words") if isinstance(stage1_json, dict) else None
    words_out: List[Dict[str, object]] = []
    if isinstance(words_in, list):
        for w in words_in:
            if not isinstance(w, dict):
                continue
            try:
                ts = float(w.get("t_start") or 0.0)
                te = float(w.get("t_end") or 0.0)
            except Exception:
                continue
            if ts >= cs - 1e-6 and te <= ce + 1e-6:
                words_out.append(w)

    ctx = {
        "audio": stage1_json.get("audio"),
        "draft_blocks": stage1_json.get("draft_blocks"),
        "transcript_words": words_out,
        "lyrics_text": str(stage1_json.get("lyrics_text") or ""),
        "target_fragment": str(stage1_json.get("target_fragment") or ""),
        "fragment_analytics": stage1_json.get("fragment_analytics"),
    }
    if resolved_mode == SUBTITLES_MODE_IMPULSE_2ND:
        from mlcore.subtitles_flow.impulse_adapter import build_impulse_raw_context

        ctx["impulse_raw_context"] = build_impulse_raw_context(stage1_json)
        ctx["impulse_raw_context"]["required_output_keys"] = [
            "anchor_in_abs",
            "word_timings",
            "segments",
        ]

    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "SUBTITLES_MODE:\n"
        + json.dumps({"mode": resolved_mode}, ensure_ascii=False)
        + "\n\n"
        "STAGE1_SUBTITLES_CONTEXT_JSON:\n"
        + json.dumps(ctx, ensure_ascii=False)
    )


def build_stage2_footage_system_instruction() -> str:
    return (
        "You are a footage style picker for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE2_FOOTAGE_STYLE.strip()
        + "\n"
    )


def build_stage2_footage_user_prompt(
    *,
    stage1_json: Dict[str, object],
    style_groups: List[Dict[str, object]],
    schema_name: str = "FootageStyleRawPayload",
) -> str:
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "STAGE1_CONTEXT_JSON:\n"
        + json.dumps(stage1_json, ensure_ascii=False)
        + "\n\n"
        "NOTE:\n"
        "Return raw style filters only (`theme/mood/filters`).\n"
        "Do not resolve inventory genre/tag at this stage.\n"
    )


def _timing_semantic_context_from_subtitles(subtitles_json: Dict[str, object]) -> Dict[str, object]:
    out_segments: List[Dict[str, object]] = []

    segments = subtitles_json.get("segments") if isinstance(subtitles_json, dict) else None
    if isinstance(segments, list) and segments:
        for idx, seg in enumerate(segments, start=1):
            if not isinstance(seg, dict):
                continue
            where = str(seg.get("segment_id") or seg.get("id") or f"segment_{idx}")
            phrase = str(seg.get("text") or "").strip()
            if not phrase:
                phrase = " ".join(str(x).strip() for x in (seg.get("words") or []) if str(x).strip())
            start_abs = seg.get("in_point")
            if start_abs is None:
                start_abs = seg.get("start")
            try:
                start = float(start_abs or 0.0)
            except Exception:
                start = 0.0
            out_segments.append(
                {
                    "where": where,
                    "phrase": phrase,
                    "start_abs": start,
                }
            )

        out_segments.sort(key=lambda x: (float(x.get("start_abs") or 0.0), str(x.get("where") or "")))
        return {"segments": out_segments}

    for key in ["block_1", "block_3", "block_6"]:
        seg = subtitles_json.get(key)
        if not isinstance(seg, dict):
            continue
        toks = seg.get("tokens")
        if not isinstance(toks, list) or not toks:
            continue
        first = toks[0] if isinstance(toks[0], dict) else {}
        out_segments.append(
            {
                "where": key,
                "phrase": str(seg.get("phrase") or ""),
                "start_abs": float((first or {}).get("t_start") or 0.0),
            }
        )

    for key, sub_key in [
        ("block_2", "p1"),
        ("block_2", "p2"),
        ("block_4", "p1"),
        ("block_4", "p2"),
        ("block_5", "slowly_in"),
        ("block_5", "fast_reveal"),
        ("block_5", "glitch_peak"),
        ("block_5", "mine"),
        ("block_7", "part1"),
        ("block_7", "part2"),
    ]:
        seg_root = subtitles_json.get(key)
        if not isinstance(seg_root, dict):
            continue
        seg = seg_root.get(sub_key)
        if not isinstance(seg, dict):
            continue
        toks = seg.get("tokens")
        if not isinstance(toks, list) or not toks:
            continue
        first = toks[0] if isinstance(toks[0], dict) else {}
        out_segments.append(
            {
                "where": f"{key}.{sub_key}",
                "phrase": str(seg.get("phrase") or ""),
                "start_abs": float((first or {}).get("t_start") or 0.0),
            }
        )

    out_segments.sort(key=lambda x: (float(x.get("start_abs") or 0.0), str(x.get("where") or "")))
    return {"segments": out_segments}


def _build_stage2_timing_modules(*, timing_mode: str) -> str:
    mode = str(timing_mode or "").strip()
    if mode not in {"prompts", "hybrid"}:
        raise ValueError(f"Unsupported timing_mode for prompt assembly: {mode!r}")
    parts = [STAGE2_TIMING_SEMANTIC_AFTER.strip()]
    if mode == "hybrid":
        parts.insert(0, STAGE2_TIMING_FAST_START.strip())
    return "\n\n".join(parts)


def build_stage2_timing_analysis_system_instruction(*, timing_mode: str) -> str:
    return (
        "You are an audio timing analyst for an After Effects pipeline.\n"
        + STAGE2_TIMING_BASE_JSON.strip()
        + "\n\n"
        + _build_stage2_timing_modules(timing_mode=timing_mode)
        + "\n\n"
        + STAGE2_TIMING_ANALYSIS.strip()
        + "\n"
    )


def build_stage2_timing_analysis_user_prompt(
    *,
    stage1_json: Dict[str, object],
    subtitles_json: Dict[str, object],
    bpm: float | None,
    fast_start_seconds: float,
    timing_mode: str,
    schema_name: str = "Stage2TimingAnalysisPayload",
) -> str:
    clip = ((stage1_json or {}).get("audio") or {}) if isinstance(stage1_json, dict) else {}
    semantic_ctx = _timing_semantic_context_from_subtitles(subtitles_json)
    clip_ctx = {
        "clip_start_abs": float(clip.get("clip_start_abs") or 0.0),
        "clip_end_abs": float(clip.get("clip_end_abs") or 0.0),
        "fast_start_seconds": float(fast_start_seconds),
    }
    if bpm is not None:
        clip_ctx["bpm_librosa"] = float(bpm)
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "TIMING_MODE:\n"
        + json.dumps({"mode": str(timing_mode)}, ensure_ascii=False)
        + "\n\nAUDIO_CLIP_JSON:\n"
        + json.dumps(clip_ctx, ensure_ascii=False)
        + "\n\nSEMANTIC_SUBTITLES_CONTEXT_JSON:\n"
        + json.dumps(semantic_ctx, ensure_ascii=False)
    )


def build_stage2_timing_cuts_system_instruction(*, timing_mode: str) -> str:
    return (
        "You are an editing timing director for an After Effects pipeline.\n"
        + STAGE2_TIMING_BASE_JSON.strip()
        + "\n\n"
        + _build_stage2_timing_modules(timing_mode=timing_mode)
        + "\n\n"
        + STAGE2_TIMING_CUTS.strip()
        + "\n"
    )


def build_stage2_timing_cuts_user_prompt(
    *,
    stage1_json: Dict[str, object],
    timing_analysis_json: Dict[str, object],
    bpm: float | None,
    fast_start_seconds: float,
    timing_mode: str,
    schema_name: str = "Stage2TimingCutsPayload",
) -> str:
    clip = ((stage1_json or {}).get("audio") or {}) if isinstance(stage1_json, dict) else {}
    clip_ctx = {
        "clip_start_abs": float(clip.get("clip_start_abs") or 0.0),
        "clip_end_abs": float(clip.get("clip_end_abs") or 0.0),
        "fast_start_seconds": float(fast_start_seconds),
    }
    if bpm is not None:
        clip_ctx["bpm_librosa"] = float(bpm)
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "TIMING_MODE:\n"
        + json.dumps({"mode": str(timing_mode)}, ensure_ascii=False)
        + "\n\nAUDIO_CLIP_JSON:\n"
        + json.dumps(clip_ctx, ensure_ascii=False)
        + "\n\nTIMING_ANALYSIS_JSON:\n"
        + json.dumps(timing_analysis_json, ensure_ascii=False)
    )
