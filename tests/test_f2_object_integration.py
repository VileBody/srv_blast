"""Integration smoke for F2 «Объект» wiring (template token + builder dispatch).

Verifies that the template carries the f2 token and that the project_builder
helper correctly turns a full_edit_config["f2"] block into JSX.
"""
from __future__ import annotations

from pathlib import Path

from app.project_builder import _build_f2_overlay_js


def test_template_contains_f2_overlay_token() -> None:
    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "{{ f2_overlay_js }}" in tpl
    # Token sits in its own labeled section next to f3/f4.
    assert "F2 «Объект» packaged-combo overlay" in tpl


def test_project_builder_no_f2_block_returns_empty() -> None:
    # No "f2" key → empty string → zero impact on regular jobs.
    assert _build_f2_overlay_js({}) == ""
    assert _build_f2_overlay_js({"f2": None}) == ""
    assert _build_f2_overlay_js({"unrelated": True}) == ""


def test_project_builder_f2_block_emits_overlay() -> None:
    cfg = {
        "f2": {
            "shape": "rhomb",
            "drop_time": 4.5,
            "seed": 12345,
        }
    }
    js = _build_f2_overlay_js(cfg)
    assert "F2 «Объект» combo overlay" in js
    assert "var __f2_drop = 4.5" in js
    assert "var __f2_seed = 12345" in js
    assert 'name: "rhomb"' in js  # shape script body inlined


def test_project_builder_f2_block_missing_fields_raise() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="shape"):
        _build_f2_overlay_js({"f2": {"drop_time": 4.5, "seed": 1}})
    with pytest.raises(RuntimeError, match="drop_time"):
        _build_f2_overlay_js({"f2": {"shape": "rhomb", "seed": 1}})
    with pytest.raises(RuntimeError, match="seed"):
        _build_f2_overlay_js({"f2": {"shape": "rhomb", "drop_time": 4.5}})


def test_schema_f2_shape_requires_drop() -> None:
    # F2 combo pivots on the drop (pre/post split + hook_light) — schema must
    # reject f2_shape without user_drop_t.
    import pytest

    from services.orchestrator.schemas import SendAudioS3Request

    with pytest.raises(ValueError, match="f2_shape requires user_drop_t"):
        SendAudioS3Request(
            audio_s3_url="https://example.com/a.mp3",
            mode="with_gemini",
            lyrics_text="x",
            target_fragment="x",
            f2_shape="rhomb",
            user_drop_t=None,
        )
    # Valid with drop set.
    ok = SendAudioS3Request(
        audio_s3_url="https://example.com/a.mp3",
        mode="with_gemini",
        lyrics_text="x",
        target_fragment="x",
        f2_shape="rhomb",
        user_drop_t=4.5,
    )
    assert ok.f2_shape == "rhomb"


def test_schema_f2_shape_literal_rejects_unknown() -> None:
    import pydantic
    import pytest

    from services.orchestrator.schemas import SendAudioS3Request

    with pytest.raises(pydantic.ValidationError):
        SendAudioS3Request(
            audio_s3_url="https://example.com/a.mp3",
            mode="with_gemini",
            lyrics_text="x",
            target_fragment="x",
            f2_shape="triangle",  # not in Literal
            user_drop_t=4.5,
        )
