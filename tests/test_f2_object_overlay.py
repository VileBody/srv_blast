"""Unit tests for F2 «Объект» packaged-combo overlay builder.

Mirrors the test style of f3/f4 overlay tests. We don't run AE — just verify
the emitted JSX text contains the right script bodies and structure.
"""
from __future__ import annotations

import pytest

from mlcore.hooks.f2_object.overlay import (
    F2_POST_DROP_TRANSITION_POOL,
    F2_SHAPES,
    build_overlay_jsx,
)


def test_f2_shapes_set_matches_files() -> None:
    # Sanity: F2_SHAPES must correspond 1:1 to mlcore/hooks/f2_object/shapes/<id>.jsx
    from pathlib import Path

    shapes_dir = (
        Path(__file__).resolve().parent.parent / "mlcore" / "hooks" / "f2_object" / "shapes"
    )
    on_disk = sorted(p.stem for p in shapes_dir.glob("*.jsx"))
    assert on_disk == sorted(F2_SHAPES), (
        f"F2_SHAPES={list(F2_SHAPES)} drift from files={on_disk}"
    )


def test_build_overlay_minimal_structure() -> None:
    js = build_overlay_jsx(shape="rhomb", drop_time=4.0, seed=42)

    # Header + IIFE
    assert "F2 «Объект» combo overlay" in js
    assert js.strip().startswith("/* ===== F2")

    # MAIN_COMP gate (zero-impact on missing comp)
    assert 'typeof MAIN_COMP === "undefined"' in js

    # Drop value emitted
    assert "var __f2_drop = 4.0" in js

    # Cut detection + pre/post split
    assert "function __f2_detectCuts" in js
    assert "__f2_pre" in js and "__f2_post" in js

    # Phase 1: shape script body (rhomb-specific marker)
    assert 'name: "rhomb"' in js
    # Phase 2: F3 hook_light script body (use unique marker from rebuild_light.jsx)
    assert "buildVspyshka" in js
    assert "buildBolt" in js
    # Phase 3: seeded random + transition groups
    assert "function __f2_rng" in js
    assert "var __f2_seed = 42" in js
    assert "__f2_groups" in js


def test_build_overlay_inlines_all_pool_transitions() -> None:
    js = build_overlay_jsx(shape="square", drop_time=10.5, seed=1)
    # Each transition has a script body marker. Pull a tiny unique substring
    # per transition file to confirm it was inlined.
    markers = {
        "snap_wipe": "snap_wipe",  # script header comment + group key
        "minimax": "minimax",
        "invert_flash": "invert_flash",
        "extract_flash": "extract_flash",
        "flash_on_cuts": "flash_on_cuts",
        "layer_shake": "layer_shake",
    }
    for tid, marker in markers.items():
        # group dispatch reference: __f2_groups["<tid>"]
        assert f'__f2_groups[{repr(marker).replace(chr(39), chr(34))}]' in js, (
            f"missing group dispatch for transition {tid!r}"
        )


def test_build_overlay_layer_shake_invoked_globally_if_in_pool() -> None:
    js = build_overlay_jsx(shape="star1", drop_time=2.0, seed=7)
    # When layer_shake's group is non-empty, the BLAST passes __f2_cuts (global,
    # all cuts) — NOT just the group subset. This is the per-clip exception.
    shake_branch_idx = js.find('__f2_groups["layer_shake"]')
    assert shake_branch_idx >= 0
    # The very next __BLAST assignment after this branch opens must use __f2_cuts.
    blast_after = js.find("$.global.__BLAST", shake_branch_idx)
    next_close = js.find("})();", shake_branch_idx)
    assert blast_after >= 0 and blast_after < next_close
    snippet = js[blast_after : blast_after + 200]
    assert "cuts: __f2_cuts" in snippet


def test_build_overlay_pool_subset() -> None:
    # Pool subset (e.g. user wanted only snap_wipe + minimax) → only those 2
    # branches are emitted in the post-drop section.
    js = build_overlay_jsx(
        shape="star2", drop_time=3.3, seed=5,
        post_drop_pool=("snap_wipe", "minimax"),
    )
    assert '__f2_groups["snap_wipe"]' in js
    assert '__f2_groups["minimax"]' in js
    assert '__f2_groups["layer_shake"]' not in js
    assert '__f2_groups["flash_on_cuts"]' not in js


def test_build_overlay_deterministic_for_same_seed() -> None:
    a = build_overlay_jsx(shape="elipse", drop_time=5.0, seed=99)
    b = build_overlay_jsx(shape="elipse", drop_time=5.0, seed=99)
    assert a == b


@pytest.mark.parametrize("shape", list(F2_SHAPES))
def test_build_overlay_all_shapes_load(shape: str) -> None:
    js = build_overlay_jsx(shape=shape, drop_time=4.4, seed=1)
    # Each shape script has a `name: "<id>"` field in its SHAPE block.
    assert f'name: "{shape}"' in js


def test_build_overlay_rejects_unknown_shape() -> None:
    with pytest.raises(ValueError, match="unknown F2 shape"):
        build_overlay_jsx(shape="triangle", drop_time=4.0, seed=1)


def test_build_overlay_rejects_non_positive_drop() -> None:
    with pytest.raises(ValueError, match="drop_time"):
        build_overlay_jsx(shape="rhomb", drop_time=0.0, seed=1)
    with pytest.raises(ValueError, match="drop_time"):
        build_overlay_jsx(shape="rhomb", drop_time=-1.0, seed=1)


def test_pool_default_is_all_six_f3_transitions() -> None:
    assert sorted(F2_POST_DROP_TRANSITION_POOL) == sorted(
        ["snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts", "layer_shake"]
    )
