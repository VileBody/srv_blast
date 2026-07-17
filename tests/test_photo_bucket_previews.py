# -*- coding: utf-8 -*-
"""Photo bucket previews — offline pieces (montage spec + JSX injection + media-
aware selection). The AE render itself needs the node, so it's out of scope here;
these lock the deterministic inputs the node consumes.
"""
from __future__ import annotations

import json
from pathlib import Path

from mlcore import footage_bucket_previews as bp
from mlcore.footage_visual_catalog import load_visual_catalog

_ROOT = Path(__file__).resolve().parents[1]
_PHOTO_TEMPLATE = _ROOT / "templates" / "bucket_preview" / "photo_montage_template.jsx"


def _contract(bucket_id: str):
    return next(c for c in load_visual_catalog() if c.bucket_id == bucket_id)


def _clips(n: int):
    return [{"file_name": f"{100000000 + i}.jpg"} for i in range(n)]


def test_photo_montage_spec_is_horizontal_and_carries_the_anim():
    spec = bp.build_photo_montage_spec(_contract("visual:urban_solitude_dark"), _clips(5))

    assert spec["width"] == 1920 and spec["height"] == 1440
    # the founder's cover-fit scale animation must ride along (footage spec omits it)
    assert spec["anim"]["grow"] == 10
    assert spec["anim"]["punch"] == 20
    assert spec["anim"]["punch_frames"] == 4
    # photos are downloaded to media/video/ on the node (same as the real render)
    assert all(c["relpath"].startswith("media/video/") for c in spec["clips"])


def test_footage_montage_spec_stays_vertical_and_animless():
    spec = bp.build_montage_spec(_contract("visual:urban_solitude_dark"), _clips(5))
    assert spec["width"] == 1080 and spec["height"] == 1920
    assert "anim" not in spec  # footage montage is static cover-fit


def test_photo_template_has_marker_and_injection_is_valid_json():
    template = _PHOTO_TEMPLATE.read_text(encoding="utf-8")
    assert "/*__MONTAGE_DATA__*/" in template

    spec = bp.build_photo_montage_spec(_contract("visual:nature_sunset_light_warm"), _clips(4))
    rendered = bp.render_montage_jsx(spec, template)

    assert "/*__MONTAGE_DATA__*/" not in rendered
    # the exact blob render_montage_jsx injects — valid JSON object literal, so a
    # broken spec can't slip through as a silent AE failure
    blob = "var MONTAGE = " + json.dumps(spec, ensure_ascii=False) + ";"
    assert blob in rendered
    assert json.loads(json.dumps(spec))["width"] == 1920
    assert spec["anim"]["punch"] == 20


def test_photo_selection_applies_the_photo_gate():
    """A preview must draw the SAME stills the real photo flow would — i.e. through
    the photo gate, not the video gate. A beach silhouette must not represent the
    digital-silhouette bucket."""
    c = _contract("visual:digital_human_silhouette_warm")

    def asset(cid, tags):
        return {
            "file_name": f"{cid}.jpg",
            "clip_id": str(cid),
            "meta_theme_tags": tags,
            "meta_color_tone": "warm",
            "meta_people_type": "present",
            "meta_mood": "minor",
        }

    beach = asset(200000001, ["silhouette", "sunset", "beach", "golden hour"])
    digital = asset(200000002, ["silhouette", "glowing", "neon", "abstract"])
    mapped = [beach, digital]

    photo_pick = bp.select_bucket_clips(c, mapped, seed="s", top_n=5, media_type="photo")
    picked = {p["file_name"] for p in photo_pick}
    assert digital["file_name"] in picked
    assert beach["file_name"] not in picked  # photo gate rejects the beach silhouette

    # video gate (default) does NOT apply the photo-only anchor -> both eligible
    video_pick = bp.select_bucket_clips(c, mapped, seed="s", top_n=5)
    assert beach["file_name"] in {p["file_name"] for p in video_pick}


def test_photo_previews_have_a_distinct_default_store():
    assert bp.DEFAULT_PHOTO_PREVIEWS_PATH != bp.DEFAULT_PREVIEWS_PATH
    assert "photo" in bp.DEFAULT_PHOTO_PREVIEWS_PATH


def test_visual_contracts_are_selectable_as_buckets():
    """The photo mode iterates VisualContracts through the same bucket helpers as
    footage Buckets — a missing duck-typed attribute would break the sweep."""
    c = _contract("visual:urban_solitude_dark")
    # attributes the preview pipeline reads off a "bucket"
    assert c.bucket_id and c.label and c.theme == "visual"
    assert isinstance(c.priority_tags, list)
    spec = bp.build_photo_montage_spec(c, _clips(3))
    assert spec["label"]  # display label resolved without error
