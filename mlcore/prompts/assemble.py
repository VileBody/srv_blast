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
      - Keep the allow-list catalog directly in the prompt (small + deterministic).
      - Detailed descriptions (summary/tags/camera/visuals/etc) are provided as ONE attached file
        (e.g., descriptions_bundle.json). The model should use that file for semantic matching.
    """
    assets_json = json.dumps(assets, ensure_ascii=False, separators=(",", ":"))

    return (
        f"Return ONLY JSON matching schema: {schema_name}\n\n"
        "ALLOWED FOOTAGE CATALOG (you MUST choose file_name ONLY from this list; do not invent new names):\n"
        f"{assets_json}\n\n"
        "You will ALSO receive an attached descriptions bundle file (JSON) containing per-file metadata.\n"
        "Use it to pick footage semantically.\n\n"
        "Notes:\n"
        "- Use the same audio track for all steps.\n"
        "- Token times MUST be ABSOLUTE seconds on full track.\n"
        "- Footage clip times MUST be ABSOLUTE seconds on full track (inside the chosen audio window).\n"
        "- For footage planning: you output only file_name + timings; file_path/src_w/src_h will be resolved later.\n"
    )
