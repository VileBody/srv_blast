from __future__ import annotations

from pathlib import Path


def test_template_contains_final_text_layout_pass_and_safe_margin() -> None:
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "function applyFinalTextLayoutPass(layer, lData, targetComp)" in tpl
    assert "sourceRectAtTime" in tpl
    assert "var marginX = 0.05;" in tpl


def test_template_contains_audio_levels_envelope_hook() -> None:
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "function applyAudioEnvelopeFromBlueprint(layer, lData)" in tpl
    assert "ADBE Audio Levels" in tpl
    assert "Math.sin(x*Math.PI*0.5)" in tpl


def test_template_contains_text_animator_expressible_and_layout_skip_hooks() -> None:
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "cfg.expressible_selector" in tpl
    assert "ADBE Text Expressible Selector" in tpl
    assert "if (td && td.no_layout_pass) return;" in tpl


def test_template_contains_screen_blending_mapping() -> None:
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert 'String(code).toLowerCase() === "screen"' in tpl
    assert "BlendingMode.SCREEN" in tpl


def test_template_uses_strict_audio_file_match_no_auto_fallback() -> None:
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "audio strict missing expected file" in tpl
    assert "audio fallback by single media/audio file" not in tpl


def test_template_flash_on_cuts_defined_but_not_auto_invoked() -> None:
    # Base auto flash-on-cuts is disabled: the F3 «Эффект» overlay
    # (transitions/flash_on_cuts.jsx) is now the sole source of cut flashes to
    # avoid doubling when the user picks it. The function stays defined for easy
    # revert, but it must NOT be auto-invoked from the build flow.
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert '=== "template_4th" || m === "impulse_2nd"' in tpl
    assert "function addFlashOnCuts()" in tpl
    assert "BlendingMode.ADD" in tpl
    assert "addFlashOnCuts();" not in tpl
