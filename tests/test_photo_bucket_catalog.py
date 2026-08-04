from mlcore.photo_bucket_catalog import (
    ACTIVE_PHOTO_BUCKET_IDS,
    PHOTO_BUCKETS,
    RETIRED_THIN_BUCKET_IDS,
    _matches,
    evaluate,
    load_photo_catalog,
    representative_score,
)


BUCKETS = {bucket.bucket_id: bucket for bucket in PHOTO_BUCKETS}
ACTIVE_BUCKETS = {bucket.bucket_id: bucket for bucket in load_photo_catalog()}


def asset(*tags, color="dark", people="none", detected_subject="", clip_id="", quality=None):
    framing = {"subject_class": detected_subject} if detected_subject else {}
    if quality is not None:
        framing["quality"] = quality
    return {
        "meta_theme_tags": list(tags),
        "meta_color_tone": color,
        "meta_people_type": people,
        "video_key": f"{clip_id}.jpg" if clip_id else "",
        "framing": framing,
    }


def eligible(bucket_id, row):
    return evaluate(BUCKETS[bucket_id], row)[0]


def test_matcher_uses_token_boundaries_not_arbitrary_substrings():
    assert not _matches(("modern office",), "ice")
    assert not _matches(("train tracks",), "rain")
    assert not _matches(("elegant mansion",), "man")
    assert not _matches(("streetwear",), "street")
    assert _matches(("rainy night",), "night")
    assert _matches(("night city",), "night city")
    assert _matches(("\u043d\u043e\u0447\u043d\u043e\u0439 \u0433\u043e\u0440\u043e\u0434",), "\u043d\u043e\u0447\u043d\u043e\u0439")


def test_field_and_urban_rain_require_independent_facets():
    assert eligible("photo:warm_field_flowers", asset("meadow", "golden hour", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("meadow", "grass", color="warm"))

    assert eligible("photo:urban_rain_night", asset("city", "rain", color="cold"))
    assert not eligible("photo:urban_rain_night", asset("city", "night", color="cold"))
    assert not eligible("photo:urban_rain_night", asset("city", "train tracks", color="cold"))


def test_performance_requires_both_crowd_and_stage_theme():
    assert eligible("photo:performance_crowd", asset("crowd", "concert", people="crowd"))
    assert not eligible("photo:performance_crowd", asset("crowd", "city street", "night", people="crowd"))


def test_neon_city_and_night_car_have_explicit_ownership():
    city = asset("neon city", "cityscape", "night")
    car = asset("car", "night drive", "night", "neon lights")

    assert eligible("photo:neon_night_city", city)
    assert not eligible("photo:neon_night_city", car)
    assert eligible("photo:car_night", car)
    assert eligible("photo:car_night", asset("car", "night"))
    assert not eligible("photo:car_night", asset("car", "night", "car interior"))


def test_solitude_requires_explicit_solitude_and_rejects_portraits():
    solitude = asset("single person", "dark forest", people="guys")
    portrait = asset("portrait", "man", "moody", people="guys")

    assert eligible("photo:solitary_person_dark", solitude)
    assert not eligible("photo:solitary_person_dark", portrait)


def test_final_review_exclusions_are_hard_contracts():
    assert not eligible("photo:car_night", asset("car", "car interior", "night"))
    assert not eligible("photo:digital_dark", asset("silhouette", "neon lights", "red glow"))
    assert not eligible("photo:digital_dark", asset("silhouette", "neon lights", "intimacy"))
    assert not eligible("photo:lone_figure_scene", asset("silhouette", "forest", "dog"))
    assert not eligible("photo:nature_golden_warm", asset("forest", "golden hour", "urban setting", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("flower field", "white car", color="warm"))


def test_golden_nature_accepts_clean_landscapes_but_not_transport_or_solitude():
    assert eligible(
        "photo:nature_golden_warm",
        asset("landscape", "grass", "golden hour", color="warm"),
    )
    assert not eligible(
        "photo:nature_golden_warm",
        asset("landscape", "sunset", "train", color="warm"),
    )
    assert not eligible(
        "photo:nature_golden_warm",
        asset("landscape", "sunlight", "solo", color="warm"),
    )

def test_nature_buckets_reject_built_environment_and_people_leaks():
    assert not eligible("photo:forest_fog_dark", asset("foggy forest", "country house", color="cold"))
    assert not eligible("photo:forest_fog_dark", asset("forest", "mist", "road", color="cold"))
    assert not eligible("photo:nature_golden_warm", asset("trees", "golden hour", "cottage", color="warm"))
    assert not eligible("photo:nature_golden_warm", asset("forest", "sunlight", detected_subject="person", color="warm"))
    assert not eligible("photo:nature_golden_warm", asset("trees", "sunset", "neon glow", color="warm"))
    assert not eligible("photo:nature_golden_warm", asset("trees", "sunset", "american flag", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("flower field", "wildflowers", detected_subject="person", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("meadow", "wildflowers", "mountain", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("flower field", "wildflowers", "lake", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("flower field", "wildflowers", people="girls", color="warm"))


def test_lone_figure_requires_detected_human_and_rejects_deer_variants():
    assert eligible("photo:lone_figure_scene", asset("silhouette", "outdoor", detected_subject="person"))
    assert not eligible("photo:lone_figure_scene", asset("lonely figure", "forest"))
    assert not eligible("photo:lone_figure_scene", asset("silhouette", "forest", "stag", detected_subject="person"))
    assert not eligible("photo:lone_figure_scene", asset("silhouette", "forest", "wildlife", detected_subject="person"))


def test_known_qwen_false_negatives_are_bucket_local_exclusions():
    assert not eligible("photo:forest_fog_dark", asset("forest", "fog", color="cold", clip_id="720716746681049424"))
    assert not eligible("photo:nature_golden_warm", asset("trees", "golden hour", color="warm", clip_id="1151373460999954766"))
    assert not eligible("photo:warm_field_flowers", asset("flower field", "flowers", color="warm", clip_id="955185402232149163"))

def test_thin_buckets_are_not_exposed_by_active_catalog():
    assert RETIRED_THIN_BUCKET_IDS.isdisjoint(ACTIVE_BUCKETS)
    assert set(ACTIVE_BUCKETS) == set(ACTIVE_PHOTO_BUCKET_IDS)
    # Policy (iter3): many small setting-specific groups beat a few broad ones, so
    # the count is no longer pinned — but every selectable bucket must still hold
    # enough stills that a reel does not repeat itself (calibrated >= 20 on the
    # 2026-07-27 snapshot; thinner themes stay retired until the base grows).
    assert len(ACTIVE_BUCKETS) >= 15



def test_representative_score_caps_synonym_bags_per_facet():
    bucket = ACTIVE_BUCKETS["photo:urban_rain_night"]
    clean = asset("city", "rain", "night")
    noisy = asset(
        "city", "urban", "cityscape", "rain", "rainy", "wet street", "night",
        "nighttime", "dark atmosphere", "dramatic lighting", "architecture",
    )

    assert eligible(bucket.bucket_id, clean)
    assert eligible(bucket.bucket_id, noisy)
    assert representative_score(bucket, clean) > representative_score(bucket, noisy)


def test_photo_theme_mapping_covers_every_active_bucket():
    from mlcore.photo_bucket_catalog import load_photo_theme_buckets

    mapped = {bucket_id for bucket_ids in load_photo_theme_buckets().values() for bucket_id in bucket_ids}
    assert set(ACTIVE_BUCKETS) <= mapped


def test_photo_catalog_uses_theme_first_ranker_and_returns_only_photo_ids():
    from mlcore.footage_bucket_ranker import rank_buckets

    catalog = list(ACTIVE_BUCKETS.values())
    ranked = rank_buckets(lyrics="ночной город неон клуб", catalog=catalog, llm_call=None)
    assert set(ranked) == set(ACTIVE_BUCKETS)
    assert all(bucket_id.startswith("photo:") for bucket_id in ranked)
    assert set(ranked[:3]) & {"photo:neon_night_city", "photo:digital_dark", "photo:urban_night_skyline"}


def test_photo_exact_slot_resolver_and_production_pool_use_strict_contract():
    from mlcore.footage_picker import _build_raw_pool
    from mlcore.footage_style_resolver import resolve_style_raw

    raw = resolve_style_raw("photo", "forest_fog_dark")
    clean = {"file_name": "clean.jpg", **asset("forest", "fog", color="cold")}
    city = {"file_name": "city.jpg", **asset("city street", "fog", color="cold")}
    pool = _build_raw_pool(raw, [clean, city], media_type="photo")

    assert [row["file_name"] for row in pool] == ["clean.jpg"]
    assert pool[0]["_photo_contract"]["bucket_id"] == "photo:forest_fog_dark"

def test_quality_rejected_photo_is_ineligible_for_every_bucket():
    row = asset(
        "forest",
        "fog",
        color="cold",
        quality={"version": "photo-quality-v1", "reject": True, "reasons": ["severe_blur"]},
    )
    assert evaluate(ACTIVE_BUCKETS["photo:forest_fog_dark"], row) == (False, "quality")
