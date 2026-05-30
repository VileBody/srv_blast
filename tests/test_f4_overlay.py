"""Unit tests for F4 «Движение» motion-hook overlay builder (Phase: AE-FX).

Pure string-assembly tests — no AE, no render. Verify:
 - build_overlay_jsx bakes bpm in, leaves no unsubstituted tokens
 - the produced block targets MAIN_COMP and is an IIFE
 - unknown device / unwired device / bad bpm raise (no-fallback)
 - LEAD_BY_DEVICE carries the per-template cover-layer durations
"""

from __future__ import annotations

import pytest

from mlcore.hooks.f4_motion.overlay import (
    LEAD_BY_DEVICE,
    F4_DEVICES,
    build_overlay_jsx,
)


def test_lead_table_has_all_five_devices():
    assert set(LEAD_BY_DEVICE) == {"swipe", "tap", "holdfinger", "pinch", "head"}
    # swipe/tap/holdfinger share the 4;09 cover; pinch/head are shorter.
    assert LEAD_BY_DEVICE["swipe"] == pytest.approx(4.3043043, abs=1e-4)
    assert LEAD_BY_DEVICE["tap"] == pytest.approx(4.3043043, abs=1e-4)
    assert LEAD_BY_DEVICE["holdfinger"] == pytest.approx(4.3043043, abs=1e-4)
    assert LEAD_BY_DEVICE["pinch"] == pytest.approx(4.2042042, abs=1e-4)
    assert LEAD_BY_DEVICE["head"] == pytest.approx(4.004004, abs=1e-4)


def test_swipe_is_wired():
    assert "swipe" in F4_DEVICES


def test_build_overlay_bakes_bpm_and_leaves_no_tokens():
    js = build_overlay_jsx(device="swipe", bpm=124.6)
    assert "__F4_BPM__" not in js
    assert "__F4_DEVICE__" not in js
    # bpm embedded as numeric literal (rounded to 3 dp)
    assert "124.6" in js
    # device id substituted into the log marker
    assert "[F4][swipe]" in js


def test_build_overlay_is_iife_over_main_comp():
    js = build_overlay_jsx(device="swipe", bpm=120.0)
    assert js.rstrip().endswith("(MAIN_COMP);")
    assert js.lstrip().startswith("/*") or "(function (comp)" in js
    assert "(function (comp)" in js
    # cover solid present (the layer whose end lands on the hook)
    assert "Сплошная заливка Черный 1" in js
    assert "buildFlashAdjustment" in js


def test_build_overlay_case_insensitive_device():
    js = build_overlay_jsx(device="SWIPE", bpm=100.0)
    assert "[F4][swipe]" in js


def test_unknown_device_raises():
    with pytest.raises(ValueError, match="unknown F4 device"):
        build_overlay_jsx(device="teleport", bpm=120.0)


def test_unwired_device_raises():
    # 'head' is a known device but its injectable template is not added yet.
    with pytest.raises(ValueError, match="not wired yet"):
        build_overlay_jsx(device="head", bpm=120.0)


@pytest.mark.parametrize("bad", [0.0, -10.0, float("nan"), float("inf")])
def test_bad_bpm_raises(bad):
    with pytest.raises(ValueError, match="invalid bpm"):
        build_overlay_jsx(device="swipe", bpm=bad)
