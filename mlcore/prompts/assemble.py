# mlcore/prompts/assemble.py
from __future__ import annotations

from typing import Dict, List
import json

from .step1_audio_window import SYSTEM_PART as S1
from .step2_subtitles import SYSTEM_PART as S2
from .step3_footage import SYSTEM_PART as S3


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
