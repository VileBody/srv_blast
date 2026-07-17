# -*- coding: utf-8 -*-
"""Photo-only bucket contracts: themes that must stay apart in the stills pool.

Each test pins ONE separation the product requires, and each also asserts the
FOOTAGE verdict is unchanged — the photo pool is denser and flatter, but the
video contracts are load-bearing for the main flow and must not move.
"""
from __future__ import annotations

import pytest

from mlcore.footage_visual_catalog import evaluate_asset, load_visual_catalog


@pytest.fixture(scope="module")
def contracts():
    return {c.bucket_id: c for c in load_visual_catalog()}


def _asset(tags, *, color="dark", people="present", clip_id="123456789012"):
    return {
        "file_name": f"{clip_id}.jpg",
        "meta_theme_tags": list(tags),
        "meta_color_tone": color,
        "meta_people_type": people,
    }


def _verdicts(contract, asset):
    video_ok, video_stage, _ = evaluate_asset(contract, asset, media_type="video")
    photo_ok, photo_stage, _ = evaluate_asset(contract, asset, media_type="photo")
    return (video_ok, video_stage), (photo_ok, photo_stage)


def test_generic_portrait_never_lands_in_digital_silhouette(contracts):
    """A still tagged both 'portrait' and 'silhouette' is a portrait, not a
    digital silhouette — the silhouette anchor alone does not settle it."""
    c = contracts["visual:digital_human_silhouette_cold"]
    asset = _asset(["portrait", "silhouette", "neon", "glowing"], color="dark")

    _, (photo_ok, photo_stage) = _verdicts(c, asset)
    assert not photo_ok
    assert photo_stage == "photo_semantic_exclude"

    # A real digital silhouette (no portrait framing) still qualifies.
    clean = _asset(["human silhouette", "neon", "glowing"], color="dark")
    _, (ok, _) = _verdicts(c, clean)
    assert ok


def test_nightlife_bucket_does_not_absorb_decay(contracts):
    """'nightlife' on a still is often just an empty decaying facade at night."""
    c = contracts["visual:active_life_dark_cold"]
    asset = _asset(["nightlife", "abandoned", "urban decay"], color="dark")

    (video_ok, _), (photo_ok, photo_stage) = _verdicts(c, asset)
    assert not photo_ok
    assert photo_stage == "photo_semantic_exclude"
    # Footage keeps its (motion-disambiguated) verdict.
    assert video_ok

    alive = _asset(["nightlife", "dancing"], color="dark")
    _, (ok, _) = _verdicts(c, alive)
    assert ok


def test_warm_silhouette_requires_a_digital_anchor_on_photos(contracts):
    """Calibrated on the real photo snapshot: without a digital anchor the warm
    silhouette bucket became a beach/sunset-silhouette dumping ground (96 of 118).
    A beach-sunset silhouette must not land here; a digital-warm one must."""
    c = contracts["visual:digital_human_silhouette_warm"]

    beach = _asset(["silhouette", "sunset", "beach", "golden hour", "ocean"], color="warm")
    (video_ok, _), (photo_ok, photo_stage) = _verdicts(c, beach)
    assert not photo_ok
    assert photo_stage == "photo_missing_anchor"
    # Footage keeps its verdict (a moving silhouette clip reads unambiguously).
    assert video_ok

    digital = _asset(["silhouette", "glowing", "neon", "abstract"], color="warm")
    _, (ok, _) = _verdicts(c, digital)
    assert ok


def test_crowd_performance_does_not_absorb_decay(contracts):
    c = contracts["visual:performance_crowd_dark"]
    asset = _asset(["club", "derelict", "ruins"], color="dark")

    _, (photo_ok, photo_stage) = _verdicts(c, asset)
    assert not photo_ok
    assert photo_stage == "photo_semantic_exclude"


def test_romance_and_solitude_never_absorb_each_other(contracts):
    """Opposite briefs. On stills the tag sets overlap constantly."""
    solitude = contracts["visual:solitary_person_dark_cold"]
    romance = contracts["visual:couple_intimacy_light_warm"]

    # A romantic still must not be served as "solitude".
    romantic = _asset(["man", "romance", "kiss", "alone", "indoor"], color="dark")
    _, (photo_ok, photo_stage) = _verdicts(solitude, romantic)
    assert not photo_ok
    assert photo_stage == "photo_semantic_exclude"

    # A lonely still must not be served as "couple intimacy".
    lonely = _asset(["love", "solitude", "lonely"], color="warm")
    _, (photo_ok2, photo_stage2) = _verdicts(romance, lonely)
    assert not photo_ok2
    assert photo_stage2 == "photo_semantic_exclude"

    # Each still admits its own brief.
    ok_solitude = _asset(["alone", "solitude", "man", "indoor"], color="dark")
    _, (a, _) = _verdicts(solitude, ok_solitude)
    assert a

    ok_romance = _asset(["couple", "romance", "hug"], color="warm")
    _, (b, _) = _verdicts(romance, ok_romance)
    assert b


def test_city_weather_never_absorbs_destruction(contracts):
    """Already enforced for both media — pinned so it cannot regress."""
    c = contracts["visual:urban_weather_dark"]

    for tag in ("destruction", "disaster", "tornado", "fire", "explosion", "abandoned"):
        asset = _asset(["city", "rain", tag], color="dark", people="none")
        (video_ok, _), (photo_ok, _) = _verdicts(c, asset)
        assert not video_ok, tag
        assert not photo_ok, tag

    clean = _asset(["city", "rain"], color="dark", people="none")
    (video_ok, _), (photo_ok, _) = _verdicts(c, clean)
    assert video_ok and photo_ok


def test_photo_exclusions_never_loosen_the_footage_pool(contracts):
    """The photo gate may only ever SUBTRACT from what footage admits: any asset
    the photo gate accepts must also be acceptable to the footage gate."""
    samples = [
        _asset(["nightlife", "dancing"], color="dark"),
        _asset(["human silhouette", "neon", "glowing"], color="dark"),
        _asset(["alone", "solitude", "man", "indoor"], color="dark"),
        _asset(["couple", "romance", "hug"], color="warm"),
        _asset(["city", "rain"], color="dark", people="none"),
        _asset(["urban", "night city"], color="dark", people="none"),
    ]
    for contract in contracts.values():
        for asset in samples:
            photo_ok, _, _ = evaluate_asset(contract, asset, media_type="photo")
            if photo_ok:
                video_ok, _, _ = evaluate_asset(contract, asset, media_type="video")
                assert video_ok, (contract.bucket_id, asset["meta_theme_tags"])


def test_photo_exclude_ids_are_real_buckets(contracts):
    """A typo'd bucket id would silently disable a separation."""
    from mlcore.footage_visual_catalog import PHOTO_EXCLUDE_TERMS, PHOTO_REQUIRE_GROUPS

    assert set(PHOTO_EXCLUDE_TERMS) <= set(contracts)
    assert set(PHOTO_REQUIRE_GROUPS) <= set(contracts)


# --------------------------------------------------------------------------- #
# The report that replaces manual review of the base
# --------------------------------------------------------------------------- #
def _photo_asset(cid, tags, *, color="dark", people="present"):
    return {
        "file_name": f"{cid}.jpg",
        "clip_id": cid,
        "meta_theme_tags": list(tags),
        "meta_color_tone": color,
        "meta_people_type": people,
        "meta_mood": "minor",
        "genre": "photo",
        "tag": "x",
    }


def test_report_classifies_pool_size_and_explains_rejections(monkeypatch):
    """The report must (a) size every bucket through the PHOTO gate and (b) say
    WHY assets were dropped — that pair is what makes a bucket decision possible
    without opening images."""
    monkeypatch.setenv("MODE", "dev")
    from scripts.photo_bucket_report import build_report

    mapped = (
        [_photo_asset(f"10000000{i:03d}", ["urban", "city", "night city"], people="none")
         for i in range(30)]
        # portraits that must be excluded from digital silhouette on photos
        + [_photo_asset(f"20000000{i:03d}", ["portrait", "silhouette", "neon", "glowing"])
           for i in range(12)]
    )
    rep = build_report(mapped=mapped, unmapped=0, thin=15, target_min=100, target_max=150)
    rows = {r["bucket_id"]: r for r in rep["rows"]}

    night = rows["visual:urban_solitude_dark"]
    assert night["pool_size"] == 30
    assert night["status"] == "small"  # below the 100-150 working band
    assert "night city" in [t["tag"] for t in night["top_tags"]]

    silhouette = rows["visual:digital_human_silhouette_cold"]
    assert silhouette["pool_size"] == 0
    assert silhouette["status"] == "empty"
    # The 12 portraits were dropped by the photo-only separation, and the report
    # names that stage rather than just showing a zero.
    assert silhouette["reject_stages"].get("photo_semantic_exclude") == 12

    assert rep["summary"]["buckets"] == len(rows)


def test_report_status_bands_match_the_target_working_size():
    from scripts.photo_bucket_report import _status

    band = dict(thin=15, target_min=100, target_max=150)
    assert _status(0, **band) == "empty"
    assert _status(9, **band) == "thin"
    assert _status(60, **band) == "small"
    assert _status(120, **band) == "healthy"
    assert _status(400, **band) == "oversized"
