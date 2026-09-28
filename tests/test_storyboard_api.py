"""Orchestrator storyboard service (cuts / pick / alternatives) with the S3,
Postgres and audio loaders replaced by fakes, plus the footage_plan schema."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mlcore import storyboard_plan as sp
from services.orchestrator.schemas import FootagePlan, SendAudioS3Request
from services.orchestrator.storyboard_api import LoadedBucket, StoryboardService
from tests.test_storyboard_plan import GROUP, THEME, _analysis, _assets


def _loader(n=30):
    assets = _assets(n)
    raw = {a["file_name"]: {"file_path": f"s3://pool/clips/{a['file_name']}"} for a in assets}
    raw[assets[0]["file_name"]]["segment_base_sec"] = 12.5

    def load(*, theme, tags_group, seed_key):
        assert (theme, tags_group) == (THEME, GROUP)
        ctx = sp.resolve_bucket(theme=theme, tags_group=tags_group, mapped_assets=assets, seed_key=seed_key)
        return LoadedBucket(ctx=ctx, raw_by_name=raw)

    return load


def _service(n=30):
    return StoryboardService(
        bucket_loader=_loader(n),
        analyzer=lambda **_kw: _analysis(drop=8.0),
        signer=lambda path: "https://signed/" + path.rsplit("/", 1)[-1],
    )


def test_cuts_return_every_pace_for_the_window():
    out = _service().cuts(audio_s3_url="s3://a/t.mp3", clip_start_abs=0.0, clip_end_abs=20.0, user_drop_t=None)
    assert set(out["cuts_by_pace"]) == {"sparse", "auto", "dense"}
    assert out["drop_t"] == 8.0 and out["bpm"] == 120.0


def test_cuts_reject_an_empty_window():
    with pytest.raises(sp.StoryboardPlanError):
        _service().cuts(audio_s3_url="s3://a/t.mp3", clip_start_abs=5.0, clip_end_abs=5.0, user_drop_t=None)


def test_pick_returns_previews_and_a_plan_per_video():
    out = _service().pick(
        theme=THEME, tags_group=GROUP, clip_start_abs=0.0, clip_end_abs=10.0,
        switch_points_abs=[2.0, 4.0, 6.0, 8.0],
        videos=[{"seed_key": "v0"}, {"seed_key": "v1", "pins": {"1": "3005.mp4"}}],
    )
    assert len(out["videos"]) == 2
    v0, v1 = out["videos"]
    assert all(c["preview_url"].startswith("https://signed/") for c in v0["clips"])
    assert v1["clips"][1]["file_name"] == "3005.mp4"
    # the plan is exactly what the render will validate
    FootagePlan.model_validate(v1["plan"])
    points, sel = sp.validate_plan(v1["plan"], clip_start_abs=0.0, clip_end_abs=10.0,
                                   known_file_names=[c["file_name"] for c in v1["clips"]])
    assert points == [2.0, 4.0, 6.0, 8.0] and sel.clips[1].file_name == "3005.mp4"


def test_segment_base_offsets_the_preview():
    out = _service().alternatives(
        theme=THEME, tags_group=GROUP, clip_start_abs=0.0, clip_end_abs=10.0,
        switch_points_abs=[2.0, 4.0, 6.0, 8.0], interval_idx=0, seed_key="v0",
        exclude_file_names=[], limit=100,
    )
    by_name = {c["file_name"]: c for c in out["candidates"]}
    assert by_name["3000.mp4"]["preview_offset_sec"] == 12.5


def test_pick_needs_seed_keys():
    with pytest.raises(sp.StoryboardPlanError):
        _service().pick(theme=THEME, tags_group=GROUP, clip_start_abs=0.0, clip_end_abs=10.0,
                        switch_points_abs=[5.0], videos=[{"seed_key": ""}])


def test_send_audio_request_accepts_a_footage_plan():
    plan = {
        "version": 1, "clip_start_abs": 10.0, "clip_end_abs": 14.0, "switch_points_abs": [12.0],
        "clips": [
            {"file_name": "a.mp4", "in_point": 10.0, "out_point": 12.0, "start_time": 10.0},
            {"file_name": "b.mp4", "in_point": 12.0, "out_point": 14.0, "start_time": 12.0},
        ],
    }
    req = SendAudioS3Request.model_validate({"audio_s3_url": "s3://a/t.mp3", "footage_plan": plan})
    assert req.footage_plan is not None and len(req.footage_plan.clips) == 2


def test_footage_plan_rejects_unknown_versions_and_empty_clips():
    with pytest.raises(ValidationError):
        FootagePlan.model_validate({"version": 2, "clip_start_abs": 0, "clip_end_abs": 1, "clips": []})
