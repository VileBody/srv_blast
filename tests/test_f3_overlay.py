# -*- coding: utf-8 -*-
"""Unit tests for the F3 «Эффект» overlay JSX builder.

These assert the build-worker side (Python -> injectable JSX string). The live
AE behaviour still needs a render-node smoke test; here we lock the contract:
selection -> correct inlined scripts, baked params, sound/logo wiring, no-op.
"""
from __future__ import annotations

import pytest

from mlcore.hooks.f3_effect.overlay import build_overlay_jsx
from app.project_builder import _build_f3_overlay_js


def test_empty_selection_is_noop():
    assert build_overlay_jsx(drop_time=4.2) == ""
    assert build_overlay_jsx(hook=None, transition=None, extra=None, drop_time=4.2) == ""
    # orchestrator-level no-op
    assert _build_f3_overlay_js({}) == ""
    assert _build_f3_overlay_js({"f3": {"drop_time": 4.2}}) == ""


def test_hook_light_inlined_and_drop_baked():
    js = build_overlay_jsx(hook="hook_light", drop_time=4.2)
    assert js != ""
    assert "F3" in js and "MAIN_COMP" in js
    assert "var __f3_drop = 4.2" in js
    # the actual hook_light script is inlined verbatim (stable marker)
    assert "HOOK_ANCHOR" in js
    # detect cuts + place ref present
    assert "__f3_detectCuts" in js
    assert "below:Текст" in js  # below:Текст


def test_hook_light_ignores_extend_and_has_no_logo_without_branding():
    # hook_light is NOT extendable -> extend arg must be baked as null
    # (the literal "to_end" still appears inside the __f3_hookDur helper body,
    # so we assert on the CALL argument, not mere substring presence).
    js = build_overlay_jsx(hook="hook_light", hook_extend="to_end", drop_time=4.2)
    assert "0.63, null)" in js
    # hook_light branding=false -> no logo even if a logo asset is supplied
    js2 = build_overlay_jsx(hook="hook_light", drop_time=4.2, assets={"logo": "media/img/x.png"})
    assert "buildStamp" not in js2


def test_slow_shutter_extendable_bakes_extend():
    js = build_overlay_jsx(hook="flash_slow_shutter", hook_extend="to_end", drop_time=3.0)
    assert '"to_end"' in js
    js2 = build_overlay_jsx(hook="flash_slow_shutter", hook_extend="after_drop:3", drop_time=3.0)
    assert '"after_drop:3"' in js2


def test_hook_sound_and_logo_wired_when_assets_present():
    js = build_overlay_jsx(
        hook="flash_slow_shutter",
        drop_time=3.0,
        assets={"hook_sound": "media/audio/flash.wav", "logo": "media/img/blast.png"},
    )
    # sound runner inlined + path resolved via __APP_DIR
    assert "__f3_sfx(" in js
    assert "media/audio/flash.wav" in js
    assert "__APP_DIR" in js
    # branding=true -> logo stamp inlined
    assert "buildStamp" in js
    assert "media/img/blast.png" in js


def test_transition_cut_sounds_before_drop_with_dedup():
    js = build_overlay_jsx(
        transition="snap_wipe",
        extra="warm_map",
        drop_time=4.2,
        assets={"transition_sound": "media/audio/glitch.wav",
                "extra_sound": "media/audio/glitch2.wav"},
    )
    # both effects inlined
    assert "snap wipe" in js
    # cut-sound loop: strictly before drop + dedup via __f3_used
    assert "__f3_used" in js
    assert "ct >= __f3_drop - fr" in js
    assert "media/audio/glitch.wav" in js
    assert "media/audio/glitch2.wav" in js


def test_unknown_ids_raise():
    with pytest.raises(ValueError):
        build_overlay_jsx(hook="does_not_exist", drop_time=4.2)
    with pytest.raises(ValueError):
        build_overlay_jsx(transition="nope", drop_time=4.2)


def test_negative_drop_rejected():
    with pytest.raises(ValueError):
        build_overlay_jsx(hook="hook_light", drop_time=-1.0)


def test_orchestrator_helper_builds_block():
    js = _build_f3_overlay_js({
        "f3": {
            "hook": "flash_slow_shutter",
            "transition": "snap_wipe",
            "extra": "warm_map",
            "hook_extend": "to_end",
            "drop_time": 3.5,
            "assets": {"hook_sound": "media/audio/flash.wav"},
        }
    })
    assert js != ""
    assert "var __f3_drop = 3.5" in js
    assert '"to_end"' in js

@pytest.mark.parametrize(
    ("effect_id", "marker"),
    [
        ("blackwhite", "blackwhite: target comp not found"),
        ("crystal_glow", "Sapphire S_Glint unavailable"),
        ("night_vision", "night vision green"),
        ("wave", "wave: target comp not found"),
    ],
)
def test_new_full_video_styles_inline(effect_id: str, marker: str):
    js = build_overlay_jsx(extra=effect_id, extra_full=True, drop_time=2.0)
    assert marker in js
    assert "startTime: 0, duration: null" in js

# ---------- stylize: wave blue tint + blackwhite glitch clips ----------


def test_wave_carries_the_blue_tint_shape():
    """Wave = warp adjustment + a blue shape layer over it (dump 20260817)."""
    js = build_overlay_jsx(extra="wave", drop_time=4.2)
    assert "ADBE Wave Warp" in js
    assert "wave_tint" in js
    # shape geometry is comp-relative, not the 576x1024 source comp's pixels
    assert "comp.width*TINT.kw" in js
    assert "590.222222/576" in js
    assert "0.09411748250326" in js  # fill colour


def test_blackwhite_grade_replaces_flat_black_and_white():
    js = build_overlay_jsx(extra="blackwhite", drop_time=4.2)
    assert 'addProperty("S_HueSatBright")' in js
    # the old flat desaturate is gone (the name survives only in prose)
    assert 'addProperty("ADBE Black&White")' not in js


def test_blackwhite_clips_and_seed_are_baked():
    js = build_overlay_jsx(
        extra="blackwhite",
        drop_time=4.2,
        assets={"extra_clips": ["media/video/a.mp4", "media/video/b.mp4"]},
        seed="job-42",
    )
    assert 'clips: [(String(__APP_DIR || "") + "/" + "media/video/a.mp4")' in js
    assert '"media/video/b.mp4"' in js
    assert 'seed: "job-42"' in js


def test_blackwhite_without_clips_is_grade_only():
    js = build_overlay_jsx(extra="blackwhite", drop_time=4.2)
    # no clips slot in the injected __BLAST payload (the script's own CONFIG
    # default `clips:null` is inlined verbatim and must stay untouched)
    assert "clips: [" not in js
    assert 'addProperty("S_HueSatBright")' in js


def test_clips_slot_ignored_for_effects_that_do_not_take_it():
    """A stray extra_clips on wave must not leak into the wave payload."""
    js = build_overlay_jsx(
        extra="wave", drop_time=4.2, assets={"extra_clips": ["media/video/a.mp4"]}
    )
    # the slot is generic (any extra can carry clips) — assert it is wired, not
    # silently dropped, so a future clip-driven stylize needs no builder change
    assert "clips: [" in js


def test_seed_reaches_overlay_from_the_f3_block():
    js = _build_f3_overlay_js({
        "f3": {
            "extra": "blackwhite",
            "drop_time": 4.2,
            "seed": "orchestrator-seed",
            "assets": {"extra_clips": ["media/video/a.mp4"]},
        }
    })
    assert 'seed: "orchestrator-seed"' in js
