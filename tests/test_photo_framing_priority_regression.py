from mlcore.photo_framing import choose_subject_detection


DETECTIONS = [
    {"class": "person", "confidence": 0.72, "bbox": [0.35, 0.1, 0.7, 0.95]},
    {"class": "car", "confidence": 0.94, "bbox": [0.01, 0.65, 0.16, 0.75]},
]


def test_couple_on_wet_road_focuses_people_not_car() -> None:
    chosen = choose_subject_detection(
        DETECTIONS,
        theme_tags=["couple", "wet road", "night", "city street"],
        people_type="couple",
    )
    assert chosen is not None
    assert chosen["class"] == "person"


def test_scene_without_semantic_subject_keeps_safe_center() -> None:
    chosen = choose_subject_detection(
        [{"class": "bench", "confidence": 0.96, "bbox": [0.1, 0.5, 0.9, 0.8]}],
        theme_tags=["forest", "fog", "dark atmosphere"],
        people_type="none",
    )
    assert chosen is None


def test_explicit_car_theme_still_focuses_car() -> None:
    chosen = choose_subject_detection(
        DETECTIONS,
        theme_tags=["car", "night drive"],
        people_type="none",
    )
    assert chosen is not None
    assert chosen["class"] == "car"
