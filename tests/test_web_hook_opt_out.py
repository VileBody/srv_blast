from __future__ import annotations

from web_app.backend.app import effect_map as em
from web_app.backend.app.render_job import _resolve_hook


def test_no_glue_and_no_style_do_not_fall_back_to_background() -> None:
    cfg = {"effectGlue": em.NO_GLUE_LABEL, "effectStyle": em.NO_STYLE_LABEL, "effectStyleFull": True}
    resolved, _ = _resolve_hook("object", cfg, bg_glue_id="snap", bg_style_id="xerox")
    assert resolved["transition"] is None
    assert resolved["extra"] is None
    assert resolved["extraFull"] is False


def test_no_style_on_no_hook_kind_is_not_full_window() -> None:
    resolved, _ = _resolve_hook("none", {"effectGlue": em.NO_GLUE_LABEL, "effectStyle": em.NO_STYLE_LABEL}, None, None)
    assert resolved["transition"] is None
    assert resolved["extra"] is None
    assert resolved["extraFull"] is False


def test_empty_values_still_fall_back_to_background() -> None:
    resolved, _ = _resolve_hook("object", {}, bg_glue_id="snap", bg_style_id="xerox")
    assert resolved["transition"] == "snap"
    assert resolved["extra"] == "xerox"
