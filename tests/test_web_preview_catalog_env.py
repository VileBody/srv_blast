from __future__ import annotations

import pytest

from scripts.validate_web_preview_catalog_env import validate
from scripts.migrate_web_preview_catalog_env import migrate_values


def test_production_preview_catalog_requires_all_visible_sections() -> None:
    footage = [
        {"id": "v", "plane": "vibes"},
        {
            "id": "collection:cine16x9__NY",
            "plane": "cine16x9",
            "selector": {"rotationTheme": "collection", "rotationTagsGroup": "cine16x9__NY"},
        },
        {
            "id": "collection:films__drive",
            "plane": "films",
            "selector": {"rotationTheme": "collection", "rotationTagsGroup": "films__drive"},
        },
    ]
    fx = [
        {"id": "effect_hook__a"},
        {"id": "effect_transition__a"},
        {"id": "effect_extra__a"},
        {"id": "motion__a"},
        {"id": "shape__a"},
    ]
    import json

    validate({
        "WEB_FOOTAGE_CATALOG_JSON": json.dumps(footage),
        "WEB_FX_CATALOG_JSON": json.dumps(fx),
    })

    with pytest.raises(ValueError, match="cine16x9"):
        validate({
            "WEB_FOOTAGE_CATALOG_JSON": json.dumps([footage[0], footage[2]]),
            "WEB_FX_CATALOG_JSON": json.dumps(fx),
        })

    with pytest.raises(ValueError, match="motion__"):
        validate({
            "WEB_FOOTAGE_CATALOG_JSON": json.dumps(footage),
            "WEB_FX_CATALOG_JSON": json.dumps([item for item in fx if not item["id"].startswith("motion__")]),
        })

    broken = [dict(item) for item in footage]
    broken[1] = {**broken[1], "selector": {"rotationTheme": "collection", "rotationTagsGroup": "NY"}}
    with pytest.raises(ValueError, match="cine16x9__NY"):
        validate({
            "WEB_FOOTAGE_CATALOG_JSON": json.dumps(broken),
            "WEB_FX_CATALOG_JSON": json.dumps(fx),
        })


def test_catalog_migration_restores_planes_and_s3_fx_records() -> None:
    import json

    values = {
        "S3_BUCKET_ASSET_STORAGE": "assets",
        "S3_WEB_ASSET_PREFIX": "app/blast808/media/v1",
        "WEB_FOOTAGE_CATALOG_JSON": json.dumps([
            {"id": "visual:forest"},
            {"id": "collection:cine16x9__NY"},
            {"id": "collection:films__drive"},
        ]),
        "WEB_FX_CATALOG_JSON": json.dumps([
            {"id": "effect_hook__a", "previewUrl": "s3://assets/h.mp4"},
            {"id": "effect_transition__a", "previewUrl": "s3://assets/t.mp4"},
            {"id": "effect_extra__a", "previewUrl": "s3://assets/e.mp4"},
        ]),
    }
    previews = {
        "motion:swipe": {"label": "Swipe"},
        "shape:square": {"label": "Square"},
    }

    updated = migrate_values(values, previews)
    footage = json.loads(updated["WEB_FOOTAGE_CATALOG_JSON"])
    fx = json.loads(updated["WEB_FX_CATALOG_JSON"])

    assert [item["plane"] for item in footage] == ["vibes", "cine16x9", "films"]
    assert footage[1]["selector"] == {
        "rotationTheme": "collection",
        "rotationTagsGroup": "cine16x9__NY",
    }
    assert footage[2]["selector"] == {
        "rotationTheme": "collection",
        "rotationTagsGroup": "films__drive",
    }
    assert next(item for item in fx if item["id"] == "motion__swipe")["selector"] == {"f4Device": "swipe"}
    assert next(item for item in fx if item["id"] == "shape__square")["previewUrl"].endswith("/shape__square.mp4")
