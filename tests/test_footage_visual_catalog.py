import json
from dataclasses import replace
from pathlib import Path

from mlcore import footage_picker
from mlcore.footage_bucket_ranker import rank_buckets
from mlcore.footage_style_resolver import resolve_style_rotation
from mlcore.footage_visual_catalog import (
    CATALOG_VERSION,
    evaluate_asset,
    load_theme_buckets,
    load_visual_catalog,
)


def _asset(clip_id, tags, *, color="dark", people="none"):
    return {
        "file_name": f"{clip_id}.mp4",
        "meta_theme_tags": tags,
        "meta_color_tone": color,
        "meta_people_type": people,
    }


def test_final_catalog_is_complete_and_theme_mapped():
    catalog = load_visual_catalog()
    assert CATALOG_VERSION
    assert len(catalog) == 23
    ids = {x.bucket_id for x in catalog}
    assert len(ids) == 23
    assert all(x.startswith("visual:") for x in ids)
    mapping = load_theme_buckets()
    assert len(mapping) == 25
    assert all(set(bucket_ids) <= ids for bucket_ids in mapping.values())


def test_global_text_gate_runs_before_semantic_match():
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:urban_weather_dark")
    ok, stage, _ = evaluate_asset(
        contract,
        _asset("new", ["night city", "rain", "empty street", "text overlay"]),
    )
    assert not ok
    assert stage == "visible_text"


def test_reviewed_positive_controls_survive_noisy_metadata():
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:digital_human_silhouette_warm")
    ok, stage, _ = evaluate_asset(
        contract,
        _asset(contract.sources[0], ["silhouette", "golden light"], color="warm", people="girls"),
    )
    assert ok
    assert stage == "reviewed_source"


def test_reviewed_source_cannot_bypass_palette_gate():
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:couple_intimacy_light_warm")
    ok, stage, _ = evaluate_asset(
        contract, _asset(contract.sources[0], ["couple", "love"], color="dark", people="couple")
    )
    assert not ok
    assert stage == "color"


def test_reviewed_couple_source_cannot_bypass_water_conflict():
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:couple_intimacy_light_warm")
    ok, stage, _ = evaluate_asset(
        contract,
        _asset(contract.sources[0], ["couple hug", "beach", "ocean view"], color="light", people="couple"),
    )
    assert not ok
    assert stage == "hard_semantic_exclude"


def test_user_reviewed_text_and_people_mismatch_is_quality_rejected():
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:couple_intimacy_light_warm")
    ok, stage, _ = evaluate_asset(
        contract,
        _asset("728457308528976739", ["intimate", "indoor setting"], color="light", people="couple"),
    )
    assert not ok
    assert stage == "quality_override"


def test_girls_portrait_requires_indoor_and_rejects_vehicle_context():
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:girls_portrait_dark_cold")

    ok, stage, _ = evaluate_asset(
        contract, _asset("outdoor", ["girl", "portrait", "city street"], people="girls")
    )
    assert not ok
    assert stage == "hard_semantic_exclude"

    reviewed_vehicle = replace(contract, sources=contract.sources + ("reviewed_vehicle",))
    ok, stage, _ = evaluate_asset(
        reviewed_vehicle, _asset("reviewed_vehicle", ["girl", "close-up", "car interior"], people="girls")
    )
    assert not ok
    assert stage == "hard_semantic_exclude"

    ok, stage, _ = evaluate_asset(
        contract, _asset("indoor", ["girl", "close-up", "indoor setting"], people="girls")
    )
    assert ok
    assert stage == "eligible"


def test_visual_ranker_ignores_mood_and_returns_only_live_ids():
    catalog = load_visual_catalog()
    major = rank_buckets(lyrics="ночной город дождь", mood="major", catalog=catalog)
    minor = rank_buckets(lyrics="ночной город дождь", mood="minor", catalog=catalog)
    assert major == minor
    assert set(major) == {x.bucket_id for x in catalog}


def test_visual_id_resolves_without_llm_or_mood_gate():
    rotation = resolve_style_rotation("visual", "urban_weather_dark")
    subgroup = rotation.subgroups[0]
    assert subgroup.theme == "visual"
    assert subgroup.tags_group == "urban_weather_dark"
    assert subgroup.filters.priority_theme_tags


def test_visual_picker_uses_contract_as_admission_gate():
    rotation = resolve_style_rotation("visual", "night_sky_space_dark_cold")
    contract = next(x for x in load_visual_catalog() if x.bucket_id == "visual:night_sky_space_dark_cold")
    reviewed = _asset(contract.sources[-1], ["night sky", "silhouette", "stars"], people="girls")
    reviewed.update({"genre": "g", "tag": "t"})
    text_overlay = _asset("unsafe", ["night sky", "stars", "text overlay"])
    text_overlay.update({"genre": "g", "tag": "t"})
    pool = footage_picker._build_raw_pool(
        rotation.subgroups[0], [reviewed, text_overlay], style_genre="g", style_tag="t"
    )
    assert [x["file_name"] for x in pool] == [reviewed["file_name"]]
    assert pool[0]["_visual_contract"]["stage"] == "reviewed_source"
