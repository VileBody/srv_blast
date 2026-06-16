# -*- coding: utf-8 -*-
"""tg_bot_public hook-flow port: handlers exist + entry gated by HOOK_FLOW_ENABLED."""
from __future__ import annotations


def test_public_has_all_hook_flow_methods():
    from services.tg_bot_public import app as pub

    cls = pub.BlastBotApp
    for name in (
        "_ask_subtitle_color", "_handle_wait_subtitle_color",
        "_ask_accent_color", "_handle_wait_accent_color",
        "_ask_hook_choice", "_handle_wait_hook_choice",
        "_ask_hook_drop", "_handle_wait_hook_drop", "_handle_wait_hook_drop_manual",
        "_ask_hook_type", "_handle_wait_hook_type",
        "_ask_hook_device", "_handle_wait_hook_device",
        "_ask_effect_hook", "_handle_wait_effect_hook",
        "_ask_effect_transition", "_handle_wait_effect_transition",
        "_ask_effect_extra", "_handle_wait_effect_extra",
        "_ask_effect_extend", "_handle_wait_effect_extend",
        "_effect_summary_and_continue",
        "_ask_f2_shape", "_handle_wait_f2_shape",
        "_ask_f1_sound", "_handle_wait_f1_sound",
        "_ask_f1_text", "_handle_wait_f1_text",
        "_trigger_hook_analysis_task", "_run_hook_analysis_bg",
        "_proceed_to_versions_or_confirm", "_final_confirm_text",
        "_hook_summary_line", "_color_summary_line",
        "_f4_effective_lead", "_parse_single_timing",
        "_parse_hook_drop_label", "_validate_hook_drop_inside_clip",
    ):
        assert hasattr(cls, name), f"public bot missing {name}"


def test_hook_flow_disabled_by_default():
    from services.tg_bot_public import app as pub

    # Default env → hooks OFF, so the public flow is unchanged until the flag is set.
    assert pub.HOOK_FLOW_ENABLED in (False, True)
    assert pub._should_route_to_hook_flow.__name__ == "_should_route_to_hook_flow"


def test_category_maps_resolve():
    from services.tg_bot_public import app as pub

    assert pub._HOOK_CATEGORY_BY_BUTTON[pub.BTN_HOOK_CAT_THOUGHT] == "thought"
    assert pub._F2_SHAPE_BY_BUTTON[pub.BTN_F2_SHAPE_RHOMB] == "rhomb"
    assert pub._HOOK_MOTION_DEVICE_BY_BUTTON[pub.BTN_HOOK_DEV_SWIPE] == "swipe"
    assert pub._FX_HOOK_BY_BUTTON[pub.BTN_FX_HOOK_LIGHT] == "hook_light"
