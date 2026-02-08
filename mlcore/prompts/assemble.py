# mlcore/prompts/assemble.py
from __future__ import annotations

from typing import Dict, List
import json

from .step1_audio_window import SYSTEM_PART as S1
from .step2_subtitles import SYSTEM_PART as S2
from .step3_footage import SYSTEM_PART as S3
from .step1_asr_scenario import SYSTEM_PART as STAGE1
from .step1a_asr_only import SYSTEM_PART as STAGE1A_ASR
from .step1b_scenario_only import SYSTEM_PART as STAGE1B_SCENARIO
from .step2_subtitles_only import SYSTEM_PART as STAGE2_SUBS
from .step2_footage_only import SYSTEM_PART as STAGE2_FOOTAGE


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
        "- It MAY contain: duration_sec, summary, tags, objects, camera, visuals, composition.\n\n"
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


def build_stage1a_asr_user_prompt(*, schema_name: str = "Stage1AsrPayload") -> str:
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "Use the attached audio as the source of truth.\n"
        "Output transcript_words for the full track and optional srt_items.\n"
    )


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
    schema_name: str = "Stage1ScenarioPayload",
) -> str:
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "STAGE1A_ASR_JSON:\n"
        + json.dumps(asr_json, ensure_ascii=False)
    )


def build_stage2_subtitles_system_instruction() -> str:
    return (
        "You are a subtitle alignment assistant for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE2_SUBS.strip()
        + "\n"
    )


def build_stage2_subtitles_user_prompt(
    *,
    stage1_json: Dict[str, object],
    schema_name: str = "BlocksTokensPayload",
) -> str:
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
    }

    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "STAGE1_SUBTITLES_CONTEXT_JSON:\n"
        + json.dumps(ctx, ensure_ascii=False)
    )


def build_stage2_footage_system_instruction() -> str:
    return (
        "You are a footage planner for an After Effects pipeline.\n"
        "Return ONLY valid JSON matching the provided schema. No markdown. No comments. No extra keys.\n\n"
        + STAGE2_FOOTAGE.strip()
        + "\n"
    )


def build_stage2_footage_user_prompt(
    *,
    stage1_json: Dict[str, object],
    assets_with_duration: List[Dict[str, object]],
    schema_name: str = "FootageSelectionPayload",
) -> str:
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "STAGE1_JSON:\n"
        + json.dumps(stage1_json, ensure_ascii=False)
        + "\n\nASSETS_ALLOW_LIST_JSON:\n"
        + json.dumps(assets_with_duration, ensure_ascii=False)
    )
