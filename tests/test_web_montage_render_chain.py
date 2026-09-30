"""Правки монтажного стола на стороне рендера: схема приёма, env сборки, оверлей F3 и
round-trip через visual ops. Переход, поставленный человеком на конкретную склейку, обязан
встать именно на неё — или джоба падает явно, а не рендерится с чужими переходами."""
from __future__ import annotations

import json

import pytest

from mlcore.hooks.f3_effect.overlay import build_overlay_jsx, normalize_cut_transitions, normalize_extra_ranges


def _base(**kw):
    req = {
        "audio_s3_url": "s3://raw-audio/raw/t.mp3",
        "user_clip_start_sec": 10.0,
        "user_clip_end_sec": 25.0,
        "pinned_cuts": {"version": 1, "clip_start_abs": 10.0, "clip_end_abs": 25.0, "switch_points_abs": [12.0, 15.0, 18.0]},
    }
    req.update(kw)
    return req


# ── схема приёма ──────────────────────────────────────────────────────────────

def test_schema_accepts_transitions_on_pinned_cuts():
    schemas = pytest.importorskip("services.orchestrator.schemas")
    req = schemas.SendAudioS3Request.model_validate(_base(
        effect_cut_transitions=[{"t_abs": 12.0, "transition": "snap_wipe"}, {"t_abs": 18.0, "transition": "minimax"}],
        effect_extra_ranges=[{"extra": "xerox", "start_abs": 10.0, "end_abs": 15.0}],
    ))
    assert [c.transition for c in req.effect_cut_transitions] == ["snap_wipe", "minimax"]


@pytest.mark.parametrize("override, message", [
    ({"effect_cut_transitions": [{"t_abs": 13.0, "transition": "snap_wipe"}]}, "not a pinned cut"),
    ({"effect_cut_transitions": [{"t_abs": 12.0, "transition": "snap_wipe"}], "pinned_cuts": None}, "require footage_plan or pinned_cuts"),
    ({"effect_cut_transitions": [], "effect_transition": "minimax"}, "mutually exclusive"),
    ({"effect_extra_ranges": [], "effect_extra": "xerox"}, "mutually exclusive"),
    ({"effect_extra_ranges": [{"extra": "wave", "start_abs": 20.0, "end_abs": 30.0}]}, "inside the clip window"),
    ({"effect_cut_transitions": [{"t_abs": 12.0, "transition": "layer_shake"}]}, "transition"),
])
def test_schema_refuses_inconsistent_montage(override, message):
    schemas = pytest.importorskip("services.orchestrator.schemas")
    with pytest.raises(Exception, match=message):
        schemas.SendAudioS3Request.model_validate(_base(**override))


# ── env сборки ────────────────────────────────────────────────────────────────

def test_env_keeps_absolute_seconds_and_explicit_empty_lists():
    tasks = pytest.importorskip("services.orchestrator.tasks")
    env = tasks.montage_fx_env_values({
        "effect_cut_transitions": [{"t_abs": 12.0, "transition": "snap_wipe"}],
        "effect_extra_ranges": [],
    })
    assert json.loads(env["F3_CUT_TRANSITIONS"]) == [{"t_abs": 12.0, "id": "snap_wipe"}]
    assert json.loads(env["F3_EXTRA_RANGES"]) == []
    assert tasks.montage_fx_env_values({}) == {}
    # in-process оркестратор видит только env из этого списка
    assert {"F3_CUT_TRANSITIONS", "F3_EXTRA_RANGES"} <= set(tasks._LLM_ENV_KEYS)


# ── оверлей ───────────────────────────────────────────────────────────────────

def test_overlay_groups_cuts_per_transition_and_windows_the_styles():
    js = build_overlay_jsx(
        hook="hook_light", drop_time=5.0,
        cut_transitions=[{"t": 2.0, "id": "snap_wipe"}, {"t": 7.5, "id": "minimax"}, {"t": 9.1, "id": "snap_wipe"}],
        extra_ranges=[{"id": "xerox", "start": 0.0, "end": 5.0}, {"id": "wave", "start": 5.0, "end": 12.0}],
        assets={"transition_sounds": {"minimax": "media/audio/m.mp3"}, "extra_sounds": {}, "extra_clips_by": {}},
    )
    assert "var __f3_pc_snap_wipe = __f3_pick([2.0, 9.1], __f3_cuts, 0.15);" in js
    assert "var __f3_pc_minimax = __f3_pick([7.5], __f3_cuts, 0.15);" in js
    assert "startTime: 0.0, duration: 5.0" in js and "startTime: 5.0, duration: 7.0" in js
    assert '"media/audio/m.mp3"' in js
    # переход не найден в компе — явная ошибка, а не молча пропавший переход
    assert 'throw new Error("F3: cut at "' in js


def test_overlay_empty_table_list_means_no_transitions():
    assert build_overlay_jsx(drop_time=5.0, cut_transitions=[], extra_ranges=[]) == ""
    js = build_overlay_jsx(hook="hook_light", drop_time=5.0, cut_transitions=[])
    assert "PER-CUT" not in js and "-- TRANSITION --" not in js


def test_overlay_refuses_double_or_uncuttable_transitions():
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_overlay_jsx(transition="minimax", drop_time=5.0, cut_transitions=[])
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_overlay_jsx(extra="xerox", drop_time=5.0, extra_ranges=[])
    with pytest.raises(ValueError, match="single cut"):
        normalize_cut_transitions([{"t": 2.0, "id": "layer_shake"}])
    with pytest.raises(ValueError, match="same cut"):
        normalize_cut_transitions([{"t": 2.0, "id": "minimax"}, {"t": 2.05, "id": "snap_wipe"}])
    with pytest.raises(ValueError, match="0 <= start < end"):
        normalize_extra_ranges([{"id": "xerox", "start": 3.0, "end": 3.0}])


# ── round-trip через visual ops ───────────────────────────────────────────────

def test_render_plan_round_trip_keeps_table_edits():
    render_plan = pytest.importorskip("app.render_plan")
    block = {
        "hook": "hook_light", "transition": None, "extra": None, "extra_full": False, "hook_extend": None,
        "drop_time": 5.0, "assets": {},
        "cut_transitions": [{"t": 2.0, "id": "snap_wipe"}],
        "extra_ranges": [{"id": "xerox", "start": 0.0, "end": 5.0}],
    }
    op = render_plan._f3_operation({"f3": block}, [])
    assert op is not None
    assert op.params["cut_transitions"] == block["cut_transitions"]
    assert op.params["extra_ranges"] == block["extra_ranges"]
    assert {"snap_wipe", "xerox", "hook_light"} <= set(op.params["detected_effect_ids"])


# ── цвет фона: шкала сайта даёт любой цвет — рендер принимает точный hex ──────

def test_solid_background_accepts_names_and_exact_hex():
    tasks = pytest.importorskip("services.orchestrator.tasks")
    assert tasks.solid_background_hex("white") == "#FFFFFF"
    assert tasks.solid_background_hex("#8b6fe6") == "#8B6FE6"
    with pytest.raises(RuntimeError, match="RRGGBB"):
        tasks.solid_background_hex("purple")
    # на светлой плоскости белый текст не читается — субтитры темнеют
    assert tasks._hex_is_light("#FFFFFF") and tasks._hex_is_light("#FFF36B")
    assert not tasks._hex_is_light("#8B6FE6") and not tasks._hex_is_light("#000000")


def test_comp_window_conversion_drops_only_what_the_render_trimmed():
    from mlcore.hooks.f3_effect.overlay import montage_items_for_comp
    cuts, ranges, dropped_c, dropped_r = montage_items_for_comp(
        [{"t_abs": 11.0, "id": "snap_wipe"}, {"t_abs": 15.0, "id": "minimax"}],
        [{"id": "xerox", "start_abs": 10.0, "end_abs": 11.5}, {"id": "wave", "start_abs": 12.0, "end_abs": 30.0}],
        clip_start=12.0, clip_len=10.0,
    )
    # окно компа начинается в 12.0: склейка 11.0 и стиль до 11.5 срезаны вместе с окном
    assert cuts == [{"t": 3.0, "id": "minimax"}] and dropped_c == 1
    assert ranges == [{"id": "wave", "start": 0.0, "end": 10.0}] and dropped_r == 1
    assert montage_items_for_comp(None, None, clip_start=0, clip_len=5) == (None, None, 0, 0)
