# -*- coding: utf-8 -*-
"""Fail-closed picker readiness gate.

These target `evaluate_pool` directly — the same pure function the deploy gate
calls on the node — so a green test here means the real gate behaves that way.
Deliberately few and strong: each one pins an invariant that a past outage broke.
"""
from __future__ import annotations

import os

import pytest

from services.orchestrator.picker_readiness import evaluate_pool


# The reference bucket used by the gate. Its footage rule is
# require_groups=(URBAN,), colors=(dark, cold), people=none, and it excludes
# vehicles/interiors — so these fixtures are what a real eligible clip looks like.
REFERENCE_BUCKET = "visual:urban_solitude_dark"


@pytest.fixture(autouse=True)
def _runtime_mode(monkeypatch):
    # footage_config.get_runtime_mode() is strict; the inventory builder needs it.
    monkeypatch.setenv("MODE", "dev")
    # Nothing in this module may depend on the ambient photo/video switch.
    monkeypatch.delenv("BG_MODE", raising=False)


def _records(n: int, *, prefix: str = "900000000", duration: float = 8.0):
    """Registry rows in footage_assets shape (clip_id is the file stem).

    Clip ids must be >=8 digits — that is what footage_picker's _CLIP_ID_RE
    extracts from a file name, and it is the join key for the whole mapping.
    """
    return [
        {
            "clip_id": f"{prefix}{i:04d}",
            "s3_key": f"pins/{prefix}{i:04d}.mp4",
            "file_name": f"{prefix}{i:04d}.mp4",
            "genre": "urban",
            "tag": "night",
            "src_w": 1080,
            "src_h": 1920,
            "duration_sec": duration,
            "dominant_color": "#101014",
            "source": "video",
        }
        for i in range(n)
    ]


def _snapshot(n: int, *, prefix: str = "900000000", tags=("urban", "city", "night city")):
    """Tags snapshot rows in the legacy video_database shape the picker reads."""
    return [
        {
            "video_key": f"{prefix}{i:04d}.mp4",
            "mood": "minor",
            "color_tone": "dark",
            "people_type": "none",
            "theme_tags": list(tags),
        }
        for i in range(n)
    ]


def _evaluate(records, snapshot, *, pool="video", pickable=None, **kwargs):
    kwargs.setdefault("reference_buckets", (REFERENCE_BUCKET,))
    kwargs.setdefault("min_pool_pickable", 10)
    kwargs.setdefault("min_bucket_candidates", 5)
    kwargs.setdefault("check_timeline", pool == "video")
    return evaluate_pool(
        pool=pool,
        records=records,
        snapshot_rows=snapshot,
        pickable_count=len(records) if pickable is None else pickable,
        **kwargs,
    )


def test_pass_on_consistent_inventory_and_snapshot():
    """A healthy, self-consistent pool must PASS — otherwise the gate is a brick
    wall that blocks every deploy."""
    rep = _evaluate(_records(40), _snapshot(40))

    assert rep.ok, rep.failures
    assert rep.failures == []
    assert rep.registry_rows == 40
    assert rep.snapshot_rows == 40
    assert rep.mapped_assets == 40
    assert rep.unmapped_assets == 0
    assert rep.buckets[REFERENCE_BUCKET] >= 5
    assert rep.timeline_covered is True


def test_fail_on_mapped_assets_zero():
    """Error #3 in prod: fresh inventory + legacy metadata => zero mapped assets.
    Counts alone looked healthy, so the gate must key on the MAPPING, not sizes."""
    # Both sides non-empty and plausible, but their clip ids do not intersect.
    rep = _evaluate(_records(40, prefix="900000000"), _snapshot(40, prefix="770000000"))

    assert not rep.ok
    assert rep.registry_rows == 40
    assert rep.snapshot_rows == 40
    assert rep.mapped_assets == 0
    assert any("mapped_assets_zero" in f for f in rep.failures), rep.failures


def test_fail_on_empty_registry_and_empty_snapshot():
    """Error #1 in prod: a zero-row scan was accepted as the new source of truth."""
    empty_registry = _evaluate([], _snapshot(40))
    assert not empty_registry.ok
    assert any("registry_empty" in f for f in empty_registry.failures)

    empty_snapshot = _evaluate(_records(40), [])
    assert not empty_snapshot.ok
    assert any("snapshot_empty" in f for f in empty_snapshot.failures)


def test_fail_on_sharp_registry_or_snapshot_shrink():
    """A pool that collapsed against its known-good baseline is a regression, not
    an edit — refuse to attach queues to it."""
    # 40 rows vs a baseline of 2400 => far below the 0.80 retain ratio.
    rep = _evaluate(
        _records(40),
        _snapshot(40),
        baseline={"registry_rows": 2400, "snapshot_rows": 2400, "pool_pickable": 2400},
    )

    assert not rep.ok
    assert any("registry_rows_shrink_guard" in f for f in rep.failures), rep.failures
    assert any("snapshot_rows_shrink_guard" in f for f in rep.failures), rep.failures

    # A pool at its baseline passes the same guard.
    healthy = _evaluate(
        _records(40),
        _snapshot(40),
        baseline={"registry_rows": 40, "snapshot_rows": 40, "pool_pickable": 40},
    )
    assert healthy.ok, healthy.failures


def test_fail_on_pool_pickable_below_floor():
    """pool_pickable=0 with a populated registry is the exact Error #1 symptom."""
    rep = _evaluate(_records(40), _snapshot(40), pickable=0, min_pool_pickable=10)

    assert not rep.ok
    assert any("pool_pickable_below_floor" in f for f in rep.failures), rep.failures


def test_fail_on_starved_reference_bucket():
    """Counts can be healthy while the contract admits nothing (e.g. a taxonomy
    change). The canary bucket is what catches that."""
    # Tags carry no urban anchor -> the visual contract rejects every asset.
    rep = _evaluate(_records(40), _snapshot(40, tags=("forest", "trees")))

    assert not rep.ok
    assert rep.mapped_assets == 40  # mapping is fine ...
    assert rep.buckets[REFERENCE_BUCKET] == 0  # ... but nothing is pickable
    assert any("bucket_starved" in f for f in rep.failures), rep.failures


def test_fail_when_timeline_cannot_be_covered():
    """Error #2 symptom: 'No footage asset can cover interval'. Clips shorter than
    every cut must fail the gate, not the user's render."""
    rep = _evaluate(_records(40, duration=0.5), _snapshot(40), timeline=((0.0, 5.0),))

    assert not rep.ok
    assert rep.timeline_covered is False
    assert any("timeline_uncovered" in f for f in rep.failures), rep.failures


def test_photo_readiness_is_independent_of_video_paths_and_env():
    """The photo gate must not read video artifacts, and must not depend on the
    ambient BG_MODE of whatever process runs it — the photo tightening has to
    apply because the pool IS photo, not because an env var happened to be set."""
    photo_records = [{**r, "source": "photo", "file_name": f"{r['clip_id']}.jpg"}
                     for r in _records(40, prefix="550000000")]
    photo_snapshot = [
        {**s, "video_key": s["video_key"].replace(".mp4", ".jpg")}
        for s in _snapshot(40, prefix="550000000")
    ]

    # Point every VIDEO path at a location that would explode if touched.
    os.environ["FOOTAGE_INVENTORY_JSON"] = "/nonexistent/video_inventory.json"
    os.environ["FOOTAGE_STYLE_METADATA_DB_PATHS_JSON"] = '["/nonexistent/video_snap.json"]'
    try:
        rep = _evaluate(photo_records, photo_snapshot, pool="photo", check_timeline=False)
    finally:
        os.environ.pop("FOOTAGE_INVENTORY_JSON", None)
        os.environ.pop("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", None)

    assert rep.ok, rep.failures
    assert rep.pool == "photo"
    assert rep.mapped_assets == 40
    # Stills carry no duration invariant.
    assert rep.timeline_covered is None
    # The photo-only anchor (night city / nighttime) is required for this bucket,
    # and these fixtures carry it -> proves the photo gate ran, with BG_MODE unset.
    assert rep.buckets[REFERENCE_BUCKET] >= 5


def test_cli_exits_nonzero_when_video_pool_is_broken(monkeypatch, capsys):
    """Closes the loop between the check and the deploy gate: an artificial
    mapped_assets=0 must make the CLI exit non-zero, because that exit code is
    the only thing standing between a broken pool and the user queues."""
    from services.orchestrator import picker_readiness as pr

    monkeypatch.setenv("CREDITS_DB_URL", "postgres://stub/stub")

    def _fake_check_pool(*, dsn, pool, baseline=None, **kwargs):
        if pool == "video":
            return pr.evaluate_pool(
                pool="video",
                records=_records(40, prefix="900000000"),
                snapshot_rows=_snapshot(40, prefix="770000000"),  # no id overlap
                pickable_count=40,
                reference_buckets=(REFERENCE_BUCKET,),
                min_pool_pickable=10,
            )
        return pr.evaluate_pool(
            pool="photo",
            records=[],
            snapshot_rows=[],
            pickable_count=0,
            reference_buckets=(REFERENCE_BUCKET,),
            min_pool_pickable=10,
            check_timeline=False,
        )

    monkeypatch.setattr(pr, "check_pool", _fake_check_pool)

    rc = pr.main(["--pools", "video,photo"])
    payload = capsys.readouterr().out

    assert rc == 1
    assert '"ok": false' in payload.lower()
    assert "mapped_assets_zero" in payload


def test_cli_photo_pool_does_not_block_the_deploy_by_default(monkeypatch, capsys):
    """The photo flow is still behind PHOTO_FLOW_ENABLED, so a cold photo pool is
    reported but must not gate a footage deploy — unless --photo-required."""
    from services.orchestrator import picker_readiness as pr

    monkeypatch.setenv("CREDITS_DB_URL", "postgres://stub/stub")

    def _fake_check_pool(*, dsn, pool, baseline=None, **kwargs):
        if pool == "video":
            return pr.evaluate_pool(
                pool="video",
                records=_records(40),
                snapshot_rows=_snapshot(40),
                pickable_count=40,
                reference_buckets=(REFERENCE_BUCKET,),
                min_pool_pickable=10,
            )
        return pr.evaluate_pool(  # photo pool is empty
            pool="photo",
            records=[],
            snapshot_rows=[],
            pickable_count=0,
            reference_buckets=(REFERENCE_BUCKET,),
            min_pool_pickable=10,
            check_timeline=False,
        )

    monkeypatch.setattr(pr, "check_pool", _fake_check_pool)

    assert pr.main(["--pools", "video,photo"]) == 0
    capsys.readouterr()
    # ... but it does block once photo is accepted and made required.
    assert pr.main(["--pools", "video,photo", "--photo-required"]) == 1


def test_photo_gate_applies_photo_only_anchors_without_bg_mode_env():
    """Same pool, tags that satisfy the FOOTAGE rule but not the PHOTO anchors:
    video passes, photo rejects. Pins that media_type is explicit, not ambient."""
    # "urban" satisfies visual:urban_solitude_dark for footage, but the photo
    # variant additionally requires a night-city anchor.
    records = _records(40, prefix="660000000")
    snapshot = _snapshot(40, prefix="660000000", tags=("urban", "city"))

    as_video = _evaluate(records, snapshot, pool="video")
    assert as_video.buckets[REFERENCE_BUCKET] >= 5, as_video.failures

    as_photo = _evaluate(records, snapshot, pool="photo", check_timeline=False)
    assert as_photo.buckets[REFERENCE_BUCKET] == 0
    assert not as_photo.ok
    assert any("bucket_starved" in f for f in as_photo.failures), as_photo.failures
