# -*- coding: utf-8 -*-
"""The F3 effect block must bind to the comp that actually holds the photos.

The block decorates cuts, and it finds cuts by scanning the layers of one comp.
For the footage render that comp is MAIN_COMP. The photo render nests a
TRANSPARENT subtitle comp over a separate "Photo Render" comp — and MAIN_COMP is
the subtitle one. Pointed there, __f3_detectCuts finds zero footage layers, so
every per-cut effect quietly does nothing while the build still reports success.
That silent no-op is why 4:3 ended up with a bespoke effect set instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore.hooks.f3_effect.overlay import build_overlay_jsx


def test_the_block_binds_to_the_comp_it_is_given() -> None:
    js = build_overlay_jsx(transition="snap_wipe", drop_time=8.0, comp_var="PHOTO_COMP")

    assert "var __f3_comp = PHOTO_COMP;" in js
    assert 'typeof PHOTO_COMP === "undefined"' in js
    # the TARGET must not be the footage comp, or the photo build would decorate
    # the subtitle comp and produce no cuts. MAIN_COMP may still be referenced —
    # it is measured as the scale reference (see test_f3_effect_scaling).
    assert "var __f3_comp = MAIN_COMP;" not in js


def test_the_footage_render_is_unchanged_by_default() -> None:
    js = build_overlay_jsx(transition="snap_wipe", drop_time=8.0)
    assert "var __f3_comp = MAIN_COMP;" in js


def test_comp_var_must_be_an_identifier() -> None:
    """It is interpolated straight into JSX, so anything else is an injection."""
    with pytest.raises(ValueError):
        build_overlay_jsx(transition="snap_wipe", drop_time=1.0, comp_var='x; alert("hi")')


def test_the_photo_template_publishes_that_comp() -> None:
    tpl = (Path(__file__).resolve().parents[1] / "templates" / "photo_template.j2").read_text(
        encoding="utf-8"
    )
    assert "$.global.PHOTO_COMP = comp;" in tpl
    # published AFTER the comp exists, otherwise the global would be undefined
    assert tpl.index("var comp = project.items.addComp") < tpl.index("$.global.PHOTO_COMP")


def test_the_photo_build_appends_the_block_after_the_template() -> None:
    """Order matters: the block reads PHOTO_COMP, which the template defines."""
    import inspect

    from app import project_builder

    src = inspect.getsource(project_builder.build_photo_project)
    assert "f3_overlay_js" in src
    assert src.index("photo_jsx = tpl.render") < src.index("f3_overlay_js).strip()")


def test_run_py_rebuilds_the_block_for_the_photo_comp() -> None:
    """build_full_project already emitted a MAIN_COMP-bound copy for subtitles;
    the photo branch needs its own copy or the effects never reach the photos."""
    src = (Path(__file__).resolve().parents[1] / "run.py").read_text(encoding="utf-8")
    assert 'comp_var="PHOTO_COMP"' in src
    assert "f3_overlay_js=photo_f3_overlay_js" in src


def test_effects_are_placed_under_the_subtitle_layer_of_the_target_comp() -> None:
    """The scripts move themselves under a layer found BY NAME. The 4:3 comp has
    no "Текст" layer, so with the footage ref they found nothing, skipped the
    move, and stayed at the top — crystal_glow blurred the subtitles instead of
    the photos."""
    photo = build_overlay_jsx(extra="crystal_glow", drop_time=8.0, comp_var="PHOTO_COMP")
    assert 'var __f3_place = "below:SUBTITLES_OVERLAY";' in photo

    footage = build_overlay_jsx(extra="crystal_glow", drop_time=8.0)
    assert 'var __f3_place = "below:Текст";' in footage


def test_the_place_ref_matches_the_layer_the_template_creates() -> None:
    """Ties the two files together: renaming the overlay layer without updating
    the ref would silently put every effect back on top of the subtitles."""
    from mlcore.hooks.f3_effect import overlay as f3

    tpl = (Path(__file__).resolve().parents[1] / "templates" / "photo_template.j2").read_text(
        encoding="utf-8"
    )
    assert f'overlay.name = "{f3._PHOTO_PLACE_REF}";' in tpl


def test_the_photo_build_forces_the_subtitles_back_on_top() -> None:
    """Belt and braces: a script that cannot find the ref still leaves its layer
    on top, so the invariant is restored after the whole block runs."""
    import inspect

    from app import project_builder

    js = project_builder._PHOTO_SUBTITLES_ON_TOP_JS
    assert 'L.name === "SUBTITLES_OVERLAY"' in js
    assert "moveToBeginning()" in js
    # must not throw when the comp or the layer is absent
    assert 'typeof PHOTO_COMP === "undefined"' in js

    src = inspect.getsource(project_builder.build_photo_project)
    assert src.index("f3_overlay_js).strip()") < src.index("_PHOTO_SUBTITLES_ON_TOP_JS")


def test_the_orchestrator_no_longer_defaults_the_bespoke_flash_on() -> None:
    """With F3 owning the cuts, a defaulted 4:3 flash would stack a second one."""
    src = (
        Path(__file__).resolve().parents[1]
        / "services" / "orchestrator" / "tasks.py"
    ).read_text(encoding="utf-8")
    assert 'req.get("photo_transition") or "none"' in src
    assert 'req.get("photo_transition") or "flash"' not in src
