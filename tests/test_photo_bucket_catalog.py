from mlcore.photo_bucket_catalog import RETIRED_THIN_BUCKET_IDS, _matches, evaluate, load_photo_catalog, representative_score


BUCKETS = {bucket.bucket_id: bucket for bucket in load_photo_catalog()}


def asset(*tags, color="dark", people="none"):
    return {
        "meta_theme_tags": list(tags),
        "meta_color_tone": color,
        "meta_people_type": people,
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
    assert eligible("photo:warm_field_flowers", asset("meadow", "wildflowers", color="warm"))
    assert not eligible("photo:warm_field_flowers", asset("meadow", "grass", color="warm"))

    assert eligible("photo:urban_rain_night", asset("city", "rain", "night"))
    assert not eligible("photo:urban_rain_night", asset("city", "rain"))
    assert not eligible("photo:urban_rain_night", asset("city", "train tracks", "night"))


def test_performance_requires_both_crowd_and_stage_theme():
    assert eligible("photo:performance_crowd", asset("crowd", "concert", people="crowd"))
    assert not eligible("photo:performance_crowd", asset("crowd", "city street", "night", people="crowd"))


def test_neon_city_and_night_car_have_explicit_ownership():
    city = asset("neon city", "cityscape", "night")
    car = asset("car", "night drive", "night", "neon lights")

    assert eligible("photo:neon_night_city", city)
    assert not eligible("photo:neon_night_city", car)
    assert eligible("photo:car_night", car)
    assert not eligible("photo:car_night", asset("car", "night"))


def test_solitude_requires_explicit_solitude_and_rejects_portraits():
    solitude = asset("single person", "dark forest", people="guys")
    portrait = asset("portrait", "man", "moody", people="guys")

    assert eligible("photo:solitary_person_dark", solitude)
    assert not eligible("photo:solitary_person_dark", portrait)



def test_representative_score_caps_synonym_bags_per_facet():
    bucket = BUCKETS["photo:urban_rain_night"]
    clean = asset("city", "rain", "night")
    noisy = asset(
        "city", "urban", "cityscape", "rain", "rainy", "wet street", "night",
        "nighttime", "dark atmosphere", "dramatic lighting", "architecture",
    )

    assert eligible(bucket.bucket_id, clean)
    assert eligible(bucket.bucket_id, noisy)
    assert representative_score(bucket, clean) > representative_score(bucket, noisy)
