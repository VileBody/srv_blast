# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the alignment-window guards of tg_bot_botapi.

Three guards ship together because they all protect the same failure — the user
asks the aligner to fit a text into a window that cannot hold it:

* the CTC frame budget, applied before the render is paid for;
* fractional timing input, so the window can be pinned to a tenth of a second;
* an actionable user notice when the window failure happens anyway.

The density helpers must agree between the two bots verbatim: a fragment the
team bot accepts and the public bot rejects (or vice versa) means one surface is
sending the alignment service work the other already knows is impossible.
"""
from __future__ import annotations

import pytest


def _apps():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    return team, pub


def test_frame_budget_constants_match_the_alignment_service() -> None:
    from mlcore.alignment.runtime import AlignmentSettings

    team, pub = _apps()

    # 20 ms per emission frame: wav2vec2 inputs_to_logits_ratio 320 at 16 kHz.
    assert team._ALIGNMENT_FRAME_SEC == pytest.approx(0.02)
    assert pub._ALIGNMENT_FRAME_SEC == team._ALIGNMENT_FRAME_SEC
    assert pub._ALIGNMENT_FRAME_BUDGET_RATIO == team._ALIGNMENT_FRAME_BUDGET_RATIO
    # The bot budget must not be looser than the service default, otherwise the
    # bot waves through requests the aligner will reject mid-build.
    service_default = AlignmentSettings.from_env().max_reference_frame_budget_ratio
    assert pub._ALIGNMENT_FRAME_BUDGET_RATIO <= service_default


def test_required_seconds_estimate_is_identical() -> None:
    team, pub = _apps()

    fragment = "раз два три, четыре!"
    # 15 letters + 3 word gaps = 18 tokens * 20 ms; punctuation is dropped.
    assert team.BlastBotApp._alignment_required_sec(fragment) == pytest.approx(0.36)
    assert pub.BlastBotApp._alignment_required_sec(fragment) == pytest.approx(
        team.BlastBotApp._alignment_required_sec(fragment)
    )
    assert team.BlastBotApp._alignment_required_sec("") == 0.0
    assert pub.BlastBotApp._alignment_required_sec("   ") == 0.0


def test_density_gate_rejects_a_fragment_that_cannot_fit() -> None:
    team, pub = _apps()

    # ~1200 characters of lyrics dropped into a 15s window: the exact shape of
    # the production ALIGNMENT_WINDOW_MISMATCH failure.
    fragment = " ".join(["словоо"] * 200)
    for module in (team, pub):
        error = module.BlastBotApp._alignment_density_error(
            fragment=fragment,
            clip_start_sec=30.0,
            clip_end_sec=45.0,
        )
        assert error, f"{module.__name__} accepted an impossible fragment"
        assert "не поместится" in error
        assert "15.0 с" in error


def test_density_gate_passes_a_realistic_chorus() -> None:
    team, pub = _apps()

    fragment = (
        "я не сплю по ночам и смотрю в потолок "
        "этот город меня никогда не найдёт"
    )
    for module in (team, pub):
        assert (
            module.BlastBotApp._alignment_density_error(
                fragment=fragment,
                clip_start_sec=30.0,
                clip_end_sec=45.0,
            )
            is None
        )


def test_density_gate_is_inert_without_both_inputs() -> None:
    team, pub = _apps()

    for module in (team, pub):
        assert (
            module.BlastBotApp._alignment_density_error(
                fragment="какой-то текст",
                clip_start_sec=0.0,
                clip_end_sec=0.0,
            )
            is None
        )
        assert (
            module.BlastBotApp._alignment_density_error(
                fragment="",
                clip_start_sec=30.0,
                clip_end_sec=45.0,
            )
            is None
        )


def test_timing_input_accepts_fractional_seconds() -> None:
    team, pub = _apps()

    for module in (team, pub):
        parse = module.BlastBotApp._parse_timing
        assert parse("1:20.5-1:33.25") == pytest.approx((80.5, 93.25))
        assert parse("1:20,5-1:33") == pytest.approx((80.5, 93.0))
        assert parse("80.5-93.25") == pytest.approx((80.5, 93.25))
        # Unchanged behaviour for the formats users already send.
        assert parse("1:20-1:50") == pytest.approx((80.0, 110.0))
        assert parse("нет") is None


def test_precise_formatter_keeps_whole_seconds_stable() -> None:
    team, pub = _apps()

    for module in (team, pub):
        app_cls = module.BlastBotApp
        assert app_cls._fmt_timing_precise(80.0) == app_cls._fmt_timing(80.0)
        assert app_cls._fmt_timing_precise(80.5) == "1:20.5"
        assert app_cls._fmt_timing_precise(93.25) == "1:33.25"
        # Sub-millisecond residue must roll the minute over, not print ":60".
        assert app_cls._fmt_timing_precise(119.9999) == "2:00"
        assert app_cls._fmt_timing_precise(59.9999) == "1:00"
        # _fmt_timing itself must not gain fractions: hook drop buttons are
        # matched back by string equality against it.
        assert app_cls._fmt_timing(80.5) == "1:20"


def test_window_failures_get_their_own_user_notice() -> None:
    _, pub = _apps()

    mismatch = pub.BlastBotApp._alignment_failure_user_text(
        "celery_failed stage=build exc=AlignmentServiceError("
        "'ALIGNMENT_WINDOW_MISMATCH', 'dynamic window search found no ...')"
    )
    assert mismatch == pub._ALIGNMENT_WINDOW_MISMATCH_USER_TEXT
    assert "Использовать прошлый трек" in mismatch

    too_long = pub.BlastBotApp._alignment_failure_user_text(
        "ALIGNMENT_TEXT_TOO_LONG_FOR_WINDOW: reference text cannot fit"
    )
    assert too_long == pub._ALIGNMENT_TEXT_TOO_LONG_USER_TEXT

    assert pub.BlastBotApp._alignment_failure_user_text("") is None
    assert (
        pub.BlastBotApp._alignment_failure_user_text("RenderNodeTimeout: ae hung")
        is None
    )


def test_inline_batch_failure_uses_the_specific_notice() -> None:
    """The outbox dispatcher is only the retry path.

    The first notice is sent inline from the batch-poll handler, so the
    alignment branch has to be wired there too — otherwise every real user
    still sees the generic "менеджер свяжется".
    """
    import inspect

    from services.tg_bot_public import app as pub

    source = inspect.getsource(pub.BlastBotApp)
    inline_send = source[source.index('kind="generation_failed_user_notice"'):]
    inline_send = inline_send[: inline_send.index("_runtime_mark_outbox_sent")]

    assert "_alignment_failure_user_text(failed_error)" in inline_send
    # …and the retry path must be able to reach the same verdict.
    assert '"error_text": failed_error' in inline_send
