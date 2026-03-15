from __future__ import annotations

import importlib.util
from pathlib import Path


def test_stage2_footage_prompt_uses_reference_and_exclude_field() -> None:
    mod_path = (
        Path(__file__).resolve().parents[1]
        / "mlcore"
        / "prompts"
        / "stage2_footage_style_only.py"
    )
    spec = importlib.util.spec_from_file_location("stage2_footage_style_only_test", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module spec from: {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    system_part = str(getattr(mod, "SYSTEM_PART"))

    src = (
        Path(__file__).resolve().parents[1]
        / "2nd_footage_selection_prompt"
        / "ai_studio_code.py"
    )
    text = src.read_text(encoding="utf-8")

    # Ensure content comes from reference body and contract is wrapped for raw payload.
    assert "STAGE 2B — VIDEO METADATA ARCHITECT" in system_part
    assert "OUTPUT JSON FORMAT:" in system_part
    assert "filters" in system_part
    assert "exclude_people" in text
    assert "exclude_people" not in system_part
    assert '"exclude"' in system_part
    assert "Return ONLY raw JSON matching Stage2FootageStyleRawPayload." in system_part
