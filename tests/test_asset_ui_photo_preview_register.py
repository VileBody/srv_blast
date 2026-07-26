from services.orchestrator.asset_routes import _photo_preview_targets


def test_photo_preview_registration_accepts_exact_active_catalog() -> None:
    targets = _photo_preview_targets()
    assert len(targets) == 17
    assert set(bucket.bucket_id for bucket in targets.values()) == {
        "photo:nature_golden_warm", "photo:forest_fog_dark", "photo:warm_field_flowers",
        "photo:urban_rain_night", "photo:urban_decay_dark", "photo:neon_night_city",
        "photo:digital_silhouette_cold", "photo:digital_glitch", "photo:lone_figure_scene",
        "photo:solitary_person_dark", "photo:girl_portrait_light", "photo:girl_golden_outdoor",
        "photo:couple_light_warm", "photo:couple_moody_dark", "photo:coastal_couple_warm",
        "photo:performance_crowd", "photo:car_night",
    }
    assert targets["photo__car_night.mp4"].bucket_id == "photo:car_night"
