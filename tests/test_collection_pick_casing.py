# -*- coding: utf-8 -*-
"""The resolved pick must name the folder the way the INVENTORY spells it.

Third live failure, and the one I had already looked at and waved through:

    RuntimeError("Gemini style pick is not present in style pool:
                  genre='films' tag='реквием по мечте'")

The registry spells the folders lowercase; the re-uploaded S3 folders are
capitalised ("Реквием по мечте"). Membership is matched case-insensitively, so
the POOL was built correctly — but the pick carried the registry's spelling, and
both the pool filter and validate_style_pick_in_groups compare exact strings
against groups built from the assets themselves. Correct pool, unusable pick.

Casing therefore must not be something the registry has to get right: an operator
renaming a folder in S3 must never break selection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mlcore import footage_picker as fp
from mlcore.footage_style_resolver import resolve_style_rotation
from mlcore.models.footage_style import FootageStylePickPayload


@pytest.fixture()
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "footage_collections.json"
    p.write_text(
        json.dumps(
            {
                "collections": [
                    {
                        "kind": "films",
                        # lowercase here, capitalised in S3 — exactly the live shape
                        "folder": "реквием по мечте",
                        "label": "Реквием по мечте",
                        "formats": ["vertical"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(p))


def _assets(tag: str, n: int = 4) -> List[Dict[str, Any]]:
    return [
        {
            "file_name": f"clip_{i:03d}.mp4",
            "genre": "films",
            "tag": tag,
            "duration_sec": 3.5,
            "src_w": 1920,
            "src_h": 1080,
        }
        for i in range(n)
    ]


def _resolve(assets):
    raw = resolve_style_rotation("collection", "films__реквием по мечте").subgroups[0]
    pick, _diag = fp.resolve_style_pick_from_raw_filters(
        raw_pick=raw, mapped_assets=assets, seed_key="seed"
    )
    return pick


def test_the_pick_uses_the_inventory_casing(registry) -> None:
    assets = _assets("Реквием по мечте")
    pick = _resolve(assets)
    assert (pick.genre, pick.tag) == ("films", "Реквием по мечте")


def test_the_pick_passes_the_pool_validation_it_previously_failed(registry) -> None:
    # This is the exact assertion the live job died on.
    assets = _assets("Реквием по мечте")
    pick = _resolve(assets)
    groups = fp.build_style_groups_from_assets(assets)
    fp.validate_style_pick_in_groups(pick, groups)


def test_the_pool_filter_accepts_the_pick(registry) -> None:
    # Same string has to survive the second exact comparison too.
    assets = _assets("Реквием по мечте")
    pick = _resolve(assets)
    raw = resolve_style_rotation("collection", "films__реквием по мечте").subgroups[0]
    pool = fp._build_raw_pool(raw, assets, style_genre=pick.genre, style_tag=pick.tag)
    assert len(pool) == len(assets)


@pytest.mark.parametrize(
    "s3_tag",
    ["Реквием по мечте", "реквием по мечте", "РЕКВИЕМ ПО МЕЧТЕ"],
)
def test_any_casing_in_s3_resolves(registry, s3_tag: str) -> None:
    # Renaming a folder in S3 must not require a registry edit to keep working.
    pick = _resolve(_assets(s3_tag))
    assert pick.tag == s3_tag
    fp.validate_style_pick_in_groups(pick, fp.build_style_groups_from_assets(_assets(s3_tag)))


def test_the_error_no_longer_blames_gemini_and_points_at_the_casing() -> None:
    # The pick is deterministic on this path; naming an LLM sent the reader
    # looking in the wrong place entirely.
    pick = FootageStylePickPayload.model_validate({"genre": "films", "tag": "реквием по мечте"})
    groups = [{"genre": "films", "tag": "Реквием по мечте"}]
    with pytest.raises(RuntimeError) as exc:
        fp.validate_style_pick_in_groups(pick, groups)
    message = str(exc.value)
    assert "Gemini" not in message
    assert "films/Реквием по мечте" in message
