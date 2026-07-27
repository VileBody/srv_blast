from __future__ import annotations

import pytest

from mlcore.photo_framing import (
    center_framing,
    choose_subject_detection,
    clamp_focus_for_cover,
    cover_visible_fraction,
    framing_from_bbox,
    normalize_framing,
)


def test_portrait_cover_exposes_only_middle_height() -> None:
    visible_w, visible_h = cover_visible_fraction(src_w=1080, src_h=1920)
    assert visible_w == pytest.approx(1.0)
    assert visible_h == pytest.approx(0.421875)


def test_focus_is_clamped_inside_cover_safe_bounds() -> None:
    x, y = clamp_focus_for_cover(0.5, 0.9, src_w=1080, src_h=1920)
    assert x == pytest.approx(0.5)
    assert y == pytest.approx(1.0 - 0.421875 / 2.0)


def test_car_bbox_moves_portrait_focus_down_without_empty_canvas() -> None:
    framing = framing_from_bbox(
        [0.1, 0.68, 0.9, 0.92],
        src_w=1080,
        src_h=1920,
        subject_class="car",
        confidence=0.91,
    )
    assert framing["subject_class"] == "car"
    assert framing["focus_y"] > 0.5
    assert framing["focus_y"] <= 1.0 - 0.421875 / 2.0 + 1e-9


def test_people_tags_prefer_person_over_incidental_car() -> None:
    chosen = choose_subject_detection(
        [
            {"class": "car", "confidence": 0.94, "bbox": [0.1, 0.4, 0.9, 0.9]},
            {"class": "person", "confidence": 0.72, "bbox": [0.35, 0.1, 0.7, 0.95]},
        ],
        theme_tags=["lone silhouette"],
        people_type="guys",
    )
    assert chosen is not None
    assert chosen["class"] == "person"


def test_car_tags_prefer_vehicle() -> None:
    chosen = choose_subject_detection(
        [
            {"class": "person", "confidence": 0.91, "bbox": [0.3, 0.1, 0.7, 0.8]},
            {"class": "car", "confidence": 0.65, "bbox": [0.05, 0.55, 0.95, 0.95]},
        ],
        theme_tags=["night drive", "car"],
    )
    assert chosen is not None
    assert chosen["class"] == "car"


def test_invalid_framing_falls_back_to_compact_center_shape() -> None:
    assert normalize_framing(None) == {}
    framing = normalize_framing(
        {"focus_x": 2, "focus_y": -1, "subject_bbox": [0.8, 0.2, 0.1, 0.9]}
    )
    assert framing["focus_x"] == 1.0
    assert framing["focus_y"] == 0.0
    assert "subject_bbox" not in framing


def test_center_framing_contract() -> None:
    framing = center_framing(src_w=1200, src_h=900)
    assert framing == {
        "version": "photo-framing-v1",
        "strategy": "center",
        "subject_class": "",
        "focus_x": 0.5,
        "focus_y": 0.5,
        "confidence": 0.0,
    }


def test_semantic_preference_does_not_focus_unrelated_object() -> None:
    chosen = choose_subject_detection(
        [{"class": "remote", "confidence": 0.95, "bbox": [0.2, 0.1, 0.8, 0.9]}],
        theme_tags=["car", "night drive"],
    )
    assert chosen is None
