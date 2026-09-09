# -*- coding: utf-8 -*-
"""The chosen bucket decides the output geometry.

The preset machinery existed and reached the orchestrator, but nothing in the
bot ever sent one — so every job rendered vertical, and the «16:9» group came out
centre-cropped to a third of its width. The button said 16:9 and the render did
not.

Geometry is a property of the BUCKET, not of a separate question to the user: a
film collection is delivered vertical, a 16:9 collection horizontally, and the
vibe catalog stays exactly as it always was.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _mod(name: str):
    return importlib.import_module(name)


@pytest.mark.parametrize("bot", BOTS)
def test_a_16x9_collection_renders_wide(bot: str) -> None:
    app = _mod(bot)
    assert app._render_preset_for_bucket("collection", "cine16x9__New_York") == "wide"


@pytest.mark.parametrize("bot", BOTS)
def test_a_film_collection_renders_vertical(bot: str) -> None:
    # Frames handle the aspect mismatch for films; they are not re-framed here.
    app = _mod(bot)
    assert app._render_preset_for_bucket("collection", "films__брат") == "vertical"


@pytest.mark.parametrize("bot", BOTS)
def test_the_vibe_catalog_is_untouched(bot: str) -> None:
    app = _mod(bot)
    assert app._render_preset_for_bucket("visual", "night_city") == "vertical"
    assert app._render_preset_for_bucket("romance_minor", "lonely_paths") == "vertical"
    assert app._render_preset_for_bucket("", "") == "vertical"


@pytest.mark.parametrize("bot", BOTS)
def test_an_unreadable_bucket_falls_back_to_vertical(bot: str) -> None:
    # A wrong geometry is worse than the historical one, so the fallback is not
    # "guess" but "what every job did before".
    app = _mod(bot)
    assert app._render_preset_for_bucket("collection", "does__not__exist") == "vertical"


@pytest.mark.parametrize("bot", BOTS)
def test_the_preset_is_actually_sent_at_enqueue(bot: str) -> None:
    # The whole defect was a resolved value nobody transmitted.
    src = inspect.getsource(_mod(bot))
    assert "render_preset=_render_preset_for_bucket(rotation_theme, rotation_group)" in src


@pytest.mark.parametrize("bot", BOTS)
def test_the_client_forwards_it_and_defaults_vertical(bot: str) -> None:
    client = importlib.import_module(bot.rsplit(".", 1)[0] + ".orchestrator_client")
    sig = inspect.signature(client.OrchestratorClient.send_audio_s3)
    assert "render_preset" in sig.parameters
    assert sig.parameters["render_preset"].default == "vertical"
    assert '"render_preset"' in inspect.getsource(client.OrchestratorClient.send_audio_s3)


def test_the_registry_and_the_api_agree_on_the_allowed_values() -> None:
    # A format the API rejects would fail the job at enqueue, after the user has
    # already picked and waited.
    from mlcore.footage_collection_catalog import load_collection_catalog
    from services.orchestrator.schemas import SendAudioS3Request

    allowed = set(SendAudioS3Request.model_fields["render_preset"].annotation.__args__)
    for bucket in load_collection_catalog():
        assert bucket.default_format in allowed, bucket.slug


def test_the_geometry_reaches_the_comp_not_just_the_footage() -> None:
    # Comp 1 must take the new size too, or the footage is scaled for one frame
    # and composited into another.
    from app.render_presets import get_preset

    wide = get_preset("wide")
    assert (wide.width, wide.height) == (1920, 1080)
    assert get_preset("vertical").width == 1080


# --------------------------------------------------------------------------- #
# hooks and horizontal output cannot coexist
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bot", BOTS)
def test_the_selected_bucket_decides_the_slot(bot: str) -> None:
    app = _mod(bot)
    store = importlib.import_module(bot.rsplit(".", 1)[0] + ".state_store")
    st = store.ChatState(chat_id=1)
    # Reads only the state, so it can be exercised unbound.
    slot = app.BlastBotApp._selected_bucket_slot
    # No selection -> legacy artist path, which is vertical.
    assert slot(None, st) == ("", "")
    st.vibe_selected_ids = ["collection:cine16x9__New_York"]
    assert slot(None, st) == ("collection", "cine16x9__New_York")
    # A multi-select is single-plane, so the first pick answers for the batch.
    st.vibe_selected_ids = ["collection:films__брат", "collection:films__бумер"]
    assert slot(None, st) == ("collection", "films__брат")


@pytest.mark.parametrize("bot", BOTS)
def test_the_hook_question_is_not_asked_when_the_render_is_horizontal(bot: str) -> None:
    # Offering the drop-anchored hooks there and having the API reject them at
    # enqueue would waste the choices after the user had already made them, so
    # the branch is taken before the question is ever put.
    src = inspect.getsource(_mod(bot).BlastBotApp._ask_hook_choice)
    assert '_render_preset_for_bucket(*self._selected_bucket_slot(st)) != "vertical"' in src
    guard, _, question = src.partition("STAGE_WAIT_HOOK_CHOICE")
    if bot == "services.tg_bot_public.app":
        assert "await self._ask_visual_transition(message, st)" in guard
    else:
        assert "await self._ask_effect_transition(message, st)" in guard
    assert question, "the vertical path must still ask the hook question"


@pytest.mark.parametrize("bot", BOTS)
def test_skipping_clears_every_hook_field(bot: str) -> None:
    # A leftover from an earlier pass would be sent and rejected all the same.
    src = inspect.getsource(_mod(bot).BlastBotApp._ask_hook_choice)
    for field in ("hook_enabled", "hook_drop_t", "hook_category", "hook_device",
                  "f2_shape", "f1_sound_url", "battery_mode"):
        assert field in src, field


def test_the_api_would_indeed_reject_that_combination() -> None:
    # Pins WHY the step is skipped, so removing the skip fails loudly here.
    import pytest as _pytest
    from pydantic import ValidationError
    from services.orchestrator.schemas import SendAudioS3Request

    with _pytest.raises(ValidationError, match="authored for the vertical"):
        SendAudioS3Request(
            audio_s3_url="s3://b/a.mp3",
            user_clip_start_sec=0.0,
            user_clip_end_sec=30.0,
            user_drop_t=10.0,
            f2_shape="rhomb",
            render_preset="wide",
        )


# --------------------------------------------------------------------------- #
# what the horizontal flow keeps and what it drops
# --------------------------------------------------------------------------- #
def test_the_public_horizontal_flow_uses_the_visual_picker_once() -> None:
    """Only the DROP-ANCHORED hooks are unavailable outside vertical.

    Transitions and grades are bound to the comp and scale with it.  The public
    bot must enter its shared visual picker directly so those two questions are
    kept but never repeated by the legacy effect flow.
    """
    src = inspect.getsource(_mod("services.tg_bot_public.app").BlastBotApp._ask_hook_choice)
    assert 'st.hook_category = ""' in src
    assert 'st.effect_hook = ""' in src
    assert "await self._ask_visual_transition(message, st)" in src
    assert "await self._ask_effect_transition(message, st)" not in src


@pytest.mark.parametrize("bot", BOTS)
def test_the_frame_step_is_vertical_only(bot: str) -> None:
    # The frame is a fixed 1080x1920 PNG mask over every layer; on a wide render
    # it would letterbox the picture with a portrait mask.
    src = inspect.getsource(_mod(bot).BlastBotApp._ask_versions)
    assert '_render_preset_for_bucket(*self._selected_bucket_slot(st)) == "vertical"' in src
    assert "vertical and not st.frame_id" in src


@pytest.mark.parametrize("bot", BOTS)
def test_the_drop_anchored_hooks_are_still_cleared(bot: str) -> None:
    # Keeping the effect branch must not leave a drop-anchored choice behind: the
    # API rejects those outside vertical, and a leftover would be sent anyway.
    src = inspect.getsource(_mod(bot).BlastBotApp._ask_hook_choice)
    for field in ("st.hook_drop_t = None", 'st.f2_shape = ""', 'st.f1_sound_url = ""',
                  'st.hook_device = ""', "st.battery_mode = False"):
        assert field in src, field


def test_transitions_and_grades_are_not_refused_by_the_api() -> None:
    # Pins the split the flow relies on: the guard names the drop-anchored hooks
    # only, so a wide job may still carry a transition and a grade.
    from services.orchestrator.schemas import SendAudioS3Request

    req = SendAudioS3Request(
        audio_s3_url="s3://b/a.mp3",
        user_clip_start_sec=0.0,
        user_clip_end_sec=30.0,
        render_preset="wide",
        effect_transition="snap_wipe",
        effect_extra="analog_glitch",
    )
    assert req.render_preset == "wide"
    assert req.effect_transition == "snap_wipe"
