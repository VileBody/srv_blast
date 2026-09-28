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
