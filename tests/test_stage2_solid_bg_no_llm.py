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


def test_orchestrator_gates_solid_bg_off_the_llm():
    # Guard: the style step branches on BG_MODE=solid/solid_strobe BEFORE the
    # call_footage_style_once LLM path.
    import mlcore.gemini_orchestrator as go

    src = inspect.getsource(go.build_all_via_gemini_one_call)
    assert 'stage2_style_solid_bg_deterministic' in src
    assert 'BG_MODE' in src
    # the solid branch appears before the LLM call in the style resolver
    assert src.index('stage2_style_solid_bg_deterministic') < src.index('call_footage_style_once(')
