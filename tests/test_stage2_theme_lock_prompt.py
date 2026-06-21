from __future__ import annotations

from mlcore.prompts.assemble import build_stage2_footage_user_prompt

_STAGE1 = {"scenario": {"theme": "x", "mood": "minor"}, "audio": {"clip_start_abs": 0.0, "clip_end_abs": 10.0}}


def test_no_override_has_no_rotation_block() -> None:
    p = build_stage2_footage_user_prompt(stage1_json=_STAGE1, style_groups=[], artist_id="rock_grunge")
    assert "ROTATION_OVERRIDE" not in p
    assert "THEME_LOCK" not in p


def test_theme_only_emits_theme_lock_not_exact_group() -> None:
    p = build_stage2_footage_user_prompt(
        stage1_json=_STAGE1, style_groups=[], artist_id="rock_grunge",
        rotation_theme="aggression_minor",
    )
    assert "THEME_LOCK" in p
    assert "aggression_minor" in p
    assert "EXACTLY ONE subgroup" in p
    assert "PICK THE SINGLE BEST tags_group" in p
    # must NOT pin a specific group
    assert "ROTATION_OVERRIDE" not in p


def test_theme_plus_group_emits_exact_override() -> None:
    p = build_stage2_footage_user_prompt(
        stage1_json=_STAGE1, style_groups=[], artist_id="rock_grunge",
        rotation_theme="aggression_minor", rotation_tags_group="chaos_elements",
    )
    assert "ROTATION_OVERRIDE" in p
    assert "chaos_elements" in p
    assert "THEME_LOCK" not in p
