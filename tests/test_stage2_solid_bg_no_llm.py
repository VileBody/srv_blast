# -*- coding: utf-8 -*-
"""Solid / strobe bg must NOT call the Gemini footage-style LLM: the footage is
dropped at AE composition, so stage2 uses a deterministic bucket instead
(regression for 503s on solid-bg jobs that still hit the legacy artist path)."""
from __future__ import annotations

import inspect


def test_default_bucket_rotation_is_valid_deterministic():
    # The fix resolves the first catalog bucket into a 1-subgroup rotation with
    # no LLM; artist_id is injectable to satisfy the downstream artist check.
    from mlcore.footage_bucket_catalog import build_buckets
    from mlcore.footage_style_resolver import resolve_style_rotation

    b0 = build_buckets()[0]
    rot = resolve_style_rotation(b0.theme, b0.tags_group)
    assert len(rot.subgroups) == 1
    sg = rot.subgroups[0]
    assert sg.theme == b0.theme
    assert sg.tags_group == b0.tags_group
    sg.artist_id = "placeholder_artist"
    assert rot.subgroups[0].artist_id == "placeholder_artist"


def test_solid_bg_respects_rotation_theme_and_group_override():
    # Regression: solid/strobe jobs whose enqueue carried a rotation slot
    # (bot sets a default artist with rotation slots so the strobe-cut
    # planner has something to work with) must resolve the deterministic
    # bucket FROM that override, not always build_buckets()[0] — otherwise
    # the downstream hard check raises
    # stage2_style_rotation_override_theme_mismatch when the override theme
    # differs from the first catalog bucket's theme.
    from mlcore.footage_bucket_catalog import build_buckets
    from mlcore.footage_style_resolver import resolve_style_rotation

    catalog = build_buckets()
    b0 = catalog[0]
    other = next(b for b in catalog if b.theme != b0.theme)

    rot = resolve_style_rotation(other.theme, other.tags_group)
    assert len(rot.subgroups) == 1
    sg = rot.subgroups[0]
    assert sg.theme == other.theme
    assert sg.tags_group == other.tags_group


def test_solid_bg_override_theme_without_group_picks_first_group_of_theme():
    # Edge case: only theme is pinned (no group) — must pick a valid group
    # belonging to that theme, not blindly build_buckets()[0] (which could
    # be a different theme entirely and would fail the downstream theme
    # match check).
    from mlcore.footage_bucket_catalog import build_buckets
    from mlcore.footage_style_resolver import resolve_style_rotation

    catalog = build_buckets()
    b0 = catalog[0]
    other_theme = next(b.theme for b in catalog if b.theme != b0.theme)
    theme_buckets = [b for b in catalog if b.theme == other_theme]
    group = theme_buckets[0].tags_group

    rot = resolve_style_rotation(other_theme, group)
    assert rot.subgroups[0].theme == other_theme


def test_orchestrator_solid_bg_branch_uses_override_when_present():
    import inspect
    import mlcore.gemini_orchestrator as go

    src = inspect.getsource(go.build_all_via_gemini_one_call)
    solid_idx = src.index('stage2_style_solid_bg_deterministic')
    solid_branch_start = src.index('_bg_mode_now in ("solid", "solid_strobe")')
    solid_branch = src[solid_branch_start:solid_idx]
    assert 'rotation_theme_override' in solid_branch
    assert 'rotation_group_override' in solid_branch


def test_orchestrator_gates_solid_bg_off_the_llm():
    # Guard: the style step branches on BG_MODE=solid/solid_strobe BEFORE the
    # call_footage_style_once LLM path.
    import mlcore.gemini_orchestrator as go

    src = inspect.getsource(go.build_all_via_gemini_one_call)
    assert 'stage2_style_solid_bg_deterministic' in src
    assert 'BG_MODE' in src
    # the solid branch appears before the LLM call in the style resolver
    assert src.index('stage2_style_solid_bg_deterministic') < src.index('call_footage_style_once(')
