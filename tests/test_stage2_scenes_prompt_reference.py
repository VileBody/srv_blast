from __future__ import annotations

from pathlib import Path

from mlcore.prompts.stage2_subtitles_scenes_3rd import (
    SCENES_REFERENCE_PROMPT_BODY,
    SYSTEM_PART,
)


def test_scenes_prompt_keeps_reference_sections_and_wrapper_contract() -> None:
    ref = Path("3rd_template/prompt_jakson.md").read_text(encoding="utf-8")
    for marker in [
        "### Step 4 — Decision logic (priority order)",
        "### Step 5 — Variety",
        "### Step 6 — Verify before writing output",
        "TYPE_4",
        "TYPE_5",
    ]:
        assert marker in ref
        assert marker in SCENES_REFERENCE_PROMPT_BODY

    assert "Return ONLY raw JSON matching Scenes3rdPayload." in SYSTEM_PART
    assert "clip.start MUST equal stage1.audio.clip_start_abs EXACTLY." in SYSTEM_PART
    assert "All scene/start/end/word_timings values are ABSOLUTE full-track seconds." in SYSTEM_PART
    assert "Never use TYPE_5 for 1–2 word repeated hooks." in SYSTEM_PART
    assert "Subsequent occurrences -> TYPE_5" not in SYSTEM_PART
    assert "```json" not in SYSTEM_PART
    assert "No markdown." in SYSTEM_PART
