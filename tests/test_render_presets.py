"""Output geometries — and the guarantee that adding them left vertical alone."""
from __future__ import annotations

import pytest

from app.project_config import AE_PROJECT
from app.render_presets import (
    RENDER_PRESETS,
    TEXT_COMP_H,
    TEXT_COMP_W,
    active_preset,
    get_preset,
    text_precomp_placement,
)


def test_the_three_shapes_the_product_offers() -> None:
    assert {(p.width, p.height) for p in RENDER_PRESETS.values()} == {
        (1080, 1920),
        (1920, 1080),
        (1080, 1080),
    }


def test_default_is_vertical_so_an_unset_env_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RENDER_PRESET", raising=False)
    preset = active_preset()
    assert (preset.name, preset.width, preset.height) == ("vertical", 1080, 1920)
    # ...and that IS the geometry the AE project has always been built at.
    assert (preset.width, preset.height) == (
        int(AE_PROJECT["main_comp"]["w"]),
        int(AE_PROJECT["main_comp"]["h"]),
    )


def test_vertical_placement_is_unchanged_from_the_committed_project_config() -> None:
    # The nested subtitle comp must land exactly where it always has, or every
    # existing job shifts. Compare against the committed vertical placement.
    committed = AE_PROJECT["root_precomp_placement"]
    got = text_precomp_placement(get_preset("vertical"))
    assert got["anchor"] == list(committed["anchor"])
    assert got["position"] == list(committed["position"])
    assert got["scale"] == list(committed["scale"])


def test_unknown_preset_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_PRESET", "portrait_4x5")
    with pytest.raises(RuntimeError, match="unknown render preset"):
        active_preset()


# --------------------------------------------------------------------------- #
# subtitle re-framing (NOT re-authoring)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(RENDER_PRESETS))
def test_subtitles_keep_their_authored_size_in_every_geometry(name: str) -> None:
    placement = text_precomp_placement(get_preset(name))
    # Scale 100 + the text comp's own centre as anchor: re-framed, never resized.
    assert placement["scale"] == [100, 100, 100]
    assert placement["anchor"] == [TEXT_COMP_W / 2.0, TEXT_COMP_H / 2.0, 0.0]


@pytest.mark.parametrize("name", sorted(RENDER_PRESETS))
def test_subtitles_are_centred_in_the_frame(name: str) -> None:
    preset = get_preset(name)
    assert text_precomp_placement(preset)["position"] == [
        preset.width / 2.0,
        preset.height / 2.0,
        0.0,
    ]


def test_only_a_wider_than_1080_frame_collapses_the_nested_comp() -> None:
    # Collapsing exists to stop the 1080-wide text comp clipping wide text inside
    # a wider frame. At 1080 the frame is the limit anyway, so it stays off and
    # vertical/square keep the exact rasterization they always had.
    assert get_preset("wide").collapse_text_precomp is True
    assert get_preset("vertical").collapse_text_precomp is False
    assert get_preset("square").collapse_text_precomp is False


def _footage_cfg(comp_w: int, comp_h: int) -> dict:
    # A 16:9 source — the shape the new collections are shot in.
    return {
        "main_comp_w": comp_w,
        "main_comp_h": comp_h,
        "text_dur_hint": 4.0,
        "layers": [
            {
                "type": "footage",
                "name": "a.mp4",
                "in_point": 0.0,
                "out_point": 4.0,
                "start_time": 0.0,
                "file_name": "a.mp4",
                "file_path": "s3://b/a.mp4",
                "src_w": 1920,
                "src_h": 1080,
            }
        ],
    }


def _precomp_layer(placement: dict, comp_w: int = 1080, comp_h: int = 1920) -> dict:
    from pathlib import Path

    from app.footage_comp import build_footage_layers

    layers = build_footage_layers(
        repo_root=Path("."),
        footage_cfg=_footage_cfg(comp_w, comp_h),
        main_comp_name="Comp 1",
        text_comp_name="Текст",
        precomp_placement=placement,
    )
    return next(x for x in layers if x["type"] == "precomp")


def test_vertical_layers_are_identical_to_the_pre_preset_build() -> None:
    # The guarantee behind the whole change: routing vertical through the preset
    # registry must produce the SAME layer the committed config always produced.
    from_config = _precomp_layer(AE_PROJECT["root_precomp_placement"])
    from_preset = _precomp_layer(text_precomp_placement(get_preset("vertical")))
    assert from_preset == from_config


def test_wide_frame_stops_cropping_a_16x9_source() -> None:
    # This is the point of the horizontal output: in the vertical comp a 1920x1080
    # clip has to be blown up ~1.8x (only ~a third of the frame width survives);
    # in the wide comp it lands about 1:1.
    from app.footage_comp import _compute_cover_transform

    _, _, vertical_scale = _compute_cover_transform(1080, 1920, 1920, 1080)
    _, _, wide_scale = _compute_cover_transform(1920, 1080, 1920, 1080)
    assert vertical_scale[0] > 175.0
    assert 100.0 <= wide_scale[0] < 102.0


def test_square_crops_a_16x9_source_only_on_the_sides() -> None:
    from app.footage_comp import _compute_cover_transform

    _, _, scale = _compute_cover_transform(1080, 1080, 1920, 1080)
    # Cover on the short axis => scale by height only, moderate side crop.
    assert 100.0 <= scale[0] < 102.0


def test_square_text_behaves_exactly_like_vertical() -> None:
    # Same frame width as the authored text comp => identical wrapping/clipping.
    square, vertical = get_preset("square"), get_preset("vertical")
    assert square.width == vertical.width == TEXT_COMP_W
    sq, ver = text_precomp_placement(square), text_precomp_placement(vertical)
    assert sq["anchor"] == ver["anchor"]
    assert sq["scale"] == ver["scale"]
    assert sq["collapseTransformation"] == ver["collapseTransformation"]
