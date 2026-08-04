# -*- coding: utf-8 -*-
"""Pixel-valued effect constants follow the comp the block is bound to.

The F3 constants (wipe travel, dilate/blur radii, mosaic block counts) were
authored against the footage comp. Re-targeted at the 4:3 photo comp they read at
the wrong physical size: a 340px wipe crosses 31% of a 1080-wide frame but 18% of
a 1920-wide one, and a 500-block mosaic goes from 2.2px to 3.8px blocks.

ISOLATION IS THE POINT HERE. The footage render must be untouched, so:
  * no scale factor is emitted on the footage path at all — the key is absent
    from the payload, and the scripts' helper then returns the raw value rather
    than multiplying by 1.0 (no float round-trip, no drift),
  * the reference is measured from MAIN_COMP at RUNTIME, not from a constant.
    The repo disagrees with itself about the footage comp height — project_config
    says 1920, the f4 device scripts say 1960 after hitting it visually — and a
    wrong constant would silently rescale footage, which is exactly what must
    not happen.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from mlcore.hooks.f3_effect.overlay import build_overlay_jsx


ROOT = Path(__file__).resolve().parents[1] / "mlcore" / "hooks" / "f3_effect"

SCALED_SCRIPTS = (
    "transitions/snap_wipe.jsx",
    "transitions/minimax.jsx",
    "transitions/layer_shake.jsx",
    "extra/xerox.jsx",
    "extra/rebuild_analog_glitch.jsx",
    "extra/rebuild_crystal_glow.jsx",
    "extra/rebuild_night_vision.jsx",
    "extra/rebuild_old_camera.jsx",
    "extra/rebuild_wave.jsx",
)


def _src(rel: str) -> str:
    return io.open(ROOT / rel, encoding="utf-8").read()


def test_footage_path_is_never_handed_a_scale_factor() -> None:
    """The scripts are inlined verbatim, so their helper DEFINITIONS appear on
    both paths — that is inert. What must never appear on the footage path is the
    payload key or the probe: without them CONFIG.fxScale stays undefined and
    every helper returns the authored value untouched."""
    js = build_overlay_jsx(
        hook="hook_light", transition="snap_wipe", extra="xerox", drop_time=8.0
    )
    assert "fxScale: __f3_fxs" not in js
    assert "__f3_fxs" not in js
    assert "MAIN_COMP.width" not in js
    # no payload may declare the key by any route
    for payload in re.findall(r"\$\.global\.__BLAST = \{[^\n]*\};", js):
        assert "fxScale" not in payload, payload


def test_photo_path_measures_the_footage_comp_at_runtime() -> None:
    js = build_overlay_jsx(
        transition="snap_wipe", extra="xerox", drop_time=8.0, comp_var="PHOTO_COMP"
    )
    assert "fxScale: __f3_fxs" in js
    assert "__f3_comp.width / r.width" in js
    assert "__f3_comp.height / r.height" in js
    # isotropic features use the area-preserving mean: a pure width factor would
    # inflate radii by 78% on a frame that is also much shorter
    assert "Math.sqrt(x * y)" in js
    # an unmeasurable reference must disable scaling, never guess
    assert "if (!r || !r.width || !r.height) { return null; }" in js


@pytest.mark.parametrize("rel", SCALED_SCRIPTS)
def test_every_scaling_helper_is_identity_without_a_factor(rel: str) -> None:
    """This is what keeps footage byte-exact: the raw value is RETURNED, not
    multiplied by 1.0."""
    src = _src(rel)
    for axis in ("sx", "sy", "si"):
        m = re.search(rf"function {axis}\(v\)\{{[^\n]*\}}", src)
        assert m, (rel, axis)
        body = m.group(0)
        assert "? v*s." in body and ": v;" in body, (rel, axis, body)
    assert "CONFIG.fxScale && CONFIG.fxScale.i>0" in src, rel


@pytest.mark.parametrize("rel", SCALED_SCRIPTS)
def test_scripts_actually_use_the_helpers(rel: str) -> None:
    src = _src(rel)
    assert re.search(r"\bs[xyi]\(", src.split("function si(")[-1]), rel


def test_mosaic_block_counts_scale_per_axis() -> None:
    """Mosaic takes a COUNT of blocks per axis, so keeping the block SIZE fixed
    means more blocks across a wider frame and fewer down a shorter one — an
    isotropic factor here would visibly stretch the blocks."""
    for rel in ("extra/rebuild_night_vision.jsx", "extra/rebuild_old_camera.jsx"):
        src = _src(rel)
        assert re.search(r'Mosaic-0001",Math\.round\(sx\(500\)\)', src), rel
        assert re.search(r'Mosaic-0002",Math\.round\(sy\(500\)\)', src), rel


def test_frame_independent_parameters_are_left_alone() -> None:
    """Percentages, levels and Transform Scale already follow the comp. Scaling
    them would change the look rather than preserve it."""
    nv = _src("extra/rebuild_night_vision.jsx")
    assert 'setP(n,"ADBE Noise2-0001",15)' in nv          # percent
    assert 'setP(u,"ADBE Unsharp Mask2-0001",400)' in nv  # percent amount
    assert 'setP(tr,"ADBE Geometry2-0004",115)' in nv     # overscan, comp-relative
    ef = _src("transitions/extract_flash.jsx")
    assert 'setP(ex,"ADBE Extract-0004",125)' in ef       # 0-255 histogram level
    sw = _src("transitions/snap_wipe.jsx")
    assert 'setP(db,"ADBE Motion Blur-0001",90)' in sw    # shutter angle


def test_the_horizontal_wipe_travel_scales_on_x() -> None:
    src = _src("transitions/snap_wipe.jsx")
    assert "cx+sx(140)" in src and "cx+sx(340)" in src
    assert "cx+140" not in src and "cx+340" not in src
