"""Render side of the pinned storyboard plan: the build-env guard in tasks.py and
the gemini_orchestrator step that turns FOOTAGE_PLAN_JSON into the job's cuts and
clips. These need the runtime image deps (celery, google-genai), so they run in CI."""
from __future__ import annotations

import json
import logging

import pytest

from mlcore import storyboard_plan as sp
from tests.test_storyboard_plan import _ctx, _plan

LOG = logging.getLogger("test.storyboard_render")


def _req(**kw):
    ctx = _ctx(20)
    base = {
        "footage_plan": _plan(ctx),
        "bg_mode": "footage",
        "rotation_theme": "romance_major",
        "rotation_tags_group": "nature_sunset",
    }
    base.update(kw)
    return base


# ── tasks.py: FOOTAGE_PLAN_JSON guard ─────────────────────────────────────────

def test_plan_env_is_the_validated_plan():
    tasks = pytest.importorskip("services.orchestrator.tasks")
    req = _req()
    assert json.loads(tasks.footage_plan_env_value(req))["switch_points_abs"] == [12.0, 15.0, 17.5]
    assert tasks.footage_plan_env_value({"bg_mode": "footage"}) is None


@pytest.mark.parametrize("override, reason", [
    ({"bg_mode": "photo"}, "bg_mode"),
    ({"rotation_tags_group": ""}, "exact slot"),
    ({"custom_footage_sources": [{"url": "s3://a/b.mp4", "width": 720, "height": 1280, "duration": 5}]}, "custom"),
])
def test_plan_env_refuses_what_it_cannot_honour(override, reason):
    tasks = pytest.importorskip("services.orchestrator.tasks")
    with pytest.raises(RuntimeError, match=reason):
        tasks.footage_plan_env_value(_req(**override))


# ── gemini_orchestrator: plan → switch points + selection ─────────────────────

def _orch():
    return pytest.importorskip("mlcore.gemini_orchestrator")


def test_pinned_plan_becomes_the_jobs_cuts_and_clips(monkeypatch):
    orch = _orch()
    ctx = _ctx(20)
    plan = _plan(ctx)
    monkeypatch.setenv("FOOTAGE_PLAN_JSON", json.dumps(plan))
    monkeypatch.delenv("FOOTAGE_BLACKLIST_PATH", raising=False)
    monkeypatch.delenv("FOOTAGE_EXCLUDE_FILE_NAMES_JSON", raising=False)
    switch, selection = orch.pinned_footage_plan_from_env(
        clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=3.0, bpm=120.0,
        style_rotation_payload=ctx.rotation, mapped_assets=ctx.assets, logger=LOG,
    )
    assert list(switch.switch_points_abs) == [12.0, 15.0, 17.5]
    assert [c.file_name for c in selection.clips] == [c["file_name"] for c in plan["clips"]]


def test_no_plan_means_the_normal_pipeline(monkeypatch):
    orch = _orch()
    monkeypatch.delenv("FOOTAGE_PLAN_JSON", raising=False)
    ctx = _ctx(20)
    assert orch.pinned_footage_plan_from_env(
        clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=3.0, bpm=None,
        style_rotation_payload=ctx.rotation, mapped_assets=ctx.assets, logger=LOG,
    ) is None


def test_blacklisted_clip_in_a_plan_fails_the_job(monkeypatch, tmp_path):
    orch = _orch()
    ctx = _ctx(20)
    plan = _plan(ctx)
    blacklist = tmp_path / "blacklist.json"
    blacklist.write_text(json.dumps([plan["clips"][0]["file_name"]]), encoding="utf-8")
    monkeypatch.setenv("FOOTAGE_PLAN_JSON", json.dumps(plan))
    monkeypatch.setenv("FOOTAGE_BLACKLIST_PATH", str(blacklist))
    with pytest.raises(sp.StoryboardPlanError, match="blacklisted"):
        orch.pinned_footage_plan_from_env(
            clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=3.0, bpm=None,
            style_rotation_payload=ctx.rotation, mapped_assets=ctx.assets, logger=LOG,
        )


def test_plan_without_an_exact_slot_fails_the_job(monkeypatch):
    orch = _orch()
    ctx = _ctx(20)
    monkeypatch.setenv("FOOTAGE_PLAN_JSON", json.dumps(_plan(ctx)))
    with pytest.raises(RuntimeError, match="exact-slot"):
        orch.pinned_footage_plan_from_env(
            clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=3.0, bpm=None,
            style_rotation_payload=None, mapped_assets=ctx.assets, logger=LOG,
        )


# ── pinned cuts (timeline pace for videos the build picks clips for) ─────────

def _cuts(**kw):
    base = {"version": 1, "clip_start_abs": 10.0, "clip_end_abs": 20.0, "switch_points_abs": [12.0, 15.0, 17.5]}
    base.update(kw)
    return base


def test_cuts_validate_and_trim_to_a_narrower_job_window():
    assert sp.validate_cuts(_cuts(), clip_start_abs=10.0, clip_end_abs=20.0) == [12.0, 15.0, 17.5]
    assert sp.validate_cuts(_cuts(), clip_start_abs=12.5, clip_end_abs=19.0) == [15.0, 17.5]
    with pytest.raises(sp.StoryboardPlanError, match="cover"):
        sp.validate_cuts(_cuts(), clip_start_abs=9.0, clip_end_abs=20.0)
    with pytest.raises(sp.StoryboardPlanError, match="increasing"):
        sp.validate_cuts(_cuts(switch_points_abs=[15.0, 12.0]), clip_start_abs=10.0, clip_end_abs=20.0)


def test_pinned_cuts_env_guard():
    tasks = pytest.importorskip("services.orchestrator.tasks")
    assert json.loads(tasks.pinned_cuts_env_value({"pinned_cuts": _cuts()}))["switch_points_abs"] == [12.0, 15.0, 17.5]
    assert tasks.pinned_cuts_env_value({}) is None
    with pytest.raises(RuntimeError, match="footage_plan"):
        tasks.pinned_cuts_env_value({"pinned_cuts": _cuts(), "footage_plan": {"version": 1}})
    with pytest.raises(RuntimeError, match="custom"):
        tasks.pinned_cuts_env_value({"pinned_cuts": _cuts(), "custom_footage_sources": [{"url": "s3://a"}]})


def test_pinned_cuts_become_the_jobs_switch_points(monkeypatch):
    orch = _orch()
    monkeypatch.setenv("PINNED_CUTS_JSON", json.dumps(_cuts()))
    switch = orch.pinned_cuts_from_env(clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=3.0,
                                       bpm=120.0, interval_cap_sec=0.0, logger=LOG)
    assert list(switch.switch_points_abs) == [12.0, 15.0, 17.5]
    monkeypatch.delenv("PINNED_CUTS_JSON")
    assert orch.pinned_cuts_from_env(clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=3.0,
                                     bpm=None, interval_cap_sec=0.0, logger=LOG) is None


def test_collection_cap_splits_a_shot_no_clip_could_cover(monkeypatch):
    orch = _orch()
    monkeypatch.setenv("PINNED_CUTS_JSON", json.dumps(_cuts(switch_points_abs=[16.0])))
    switch = orch.pinned_cuts_from_env(clip_start_abs=10.0, clip_end_abs=20.0, fast_start_seconds=0.0,
                                       bpm=None, interval_cap_sec=2.5, logger=LOG)
    points = [10.0, *switch.switch_points_abs, 20.0]
    assert 16.0 in switch.switch_points_abs and max(b - a for a, b in zip(points, points[1:])) <= 2.5 + 1e-6
    assert orch.collection_interval_cap([{"duration_sec": 3.0}, {"duration_sec": 5.0}]) == pytest.approx(2.95)
