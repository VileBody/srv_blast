"""Hooks are authored for 1080x1920 and must not be accepted at other geometries.

Every hook overlay carries baked pixel coordinates — the F4 devices position
artwork absolutely, the F2 shapes and the F3 hook_light are drawn against a
1080-wide frame. Rendering one into a wide or square comp does not fail; it puts
the effect in the wrong place, which is harder to notice and worse to ship. The
combination is refused at the door until those scripts are reviewed.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.orchestrator.schemas import SendAudioS3Request

BASE = {
    "audio_s3_url": "s3://bucket/track.mp3",
    "user_clip_start_sec": 0.0,
    "user_clip_end_sec": 30.0,
    "user_drop_t": 12.0,
}

HOOKS = [
    {"f1_sound_url": "s3://bucket/hook.mp3"},
    {"f2_shape": "rhomb"},
    {"effect_hook": "hook_light"},
    {"f4_device": "tap"},
    {"hook_device": "punchline"},
]


def _ids(cases):
    return [next(iter(c)) for c in cases]


@pytest.mark.parametrize("hook", HOOKS, ids=_ids(HOOKS))
@pytest.mark.parametrize("preset", ["wide", "square"])
def test_hook_is_refused_outside_the_vertical_frame(hook: dict, preset: str) -> None:
    with pytest.raises(ValidationError, match="authored for the vertical"):
        SendAudioS3Request(**BASE, **hook, render_preset=preset)


@pytest.mark.parametrize("hook", HOOKS, ids=_ids(HOOKS))
def test_the_same_hook_is_fine_vertically(hook: dict) -> None:
    req = SendAudioS3Request(**BASE, **hook, render_preset="vertical")
    assert req.render_preset == "vertical"


def test_vertical_is_the_default_so_existing_callers_are_untouched() -> None:
    req = SendAudioS3Request(**BASE, f2_shape="rhomb")
    assert req.render_preset == "vertical"


def test_a_hookless_job_may_use_any_geometry() -> None:
    for preset in ("vertical", "wide", "square"):
        assert SendAudioS3Request(**BASE, render_preset=preset).render_preset == preset


def test_the_error_names_every_offending_hook() -> None:
    with pytest.raises(ValidationError) as exc:
        SendAudioS3Request(
            **BASE, render_preset="wide", f2_shape="rhomb", f4_device="tap"
        )
    message = str(exc.value)
    assert "f2_shape" in message and "f4_device" in message
