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
    USER prompt strategy:
      - Keep prompt SMALL.
      - Allow-list (assets catalog) is provided as an attached JSON file.
      - Detailed per-file descriptions are provided as an attached JSON file.
    """
    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "You will receive TWO attached JSON files:\n"
        "1) ASSETS_CATALOG_JSON: array of assets with file_name (+ optional duration_sec/src_w/src_h)\n"
        "2) DESCRIPTIONS_BUNDLE_JSON: array of per-file metadata (summary/tags/etc)\n\n"
        "Hard rule:\n"
        "- For footage planning you MUST choose file_name ONLY from ASSETS_CATALOG_JSON.\n"
        "- Do NOT invent new file_name.\n\n"
        "Notes:\n"
        "- Use the same audio track for all steps.\n"
        "- Token times MUST be ABSOLUTE seconds on full track.\n"
        "- Footage clip times MUST be ABSOLUTE seconds on full track (inside the chosen audio window).\n"
        "- For footage planning: you output only file_name + timings; file_path/src_w/src_h will be resolved later.\n"
    )
