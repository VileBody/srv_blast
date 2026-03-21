from __future__ import annotations

import time

from mlcore.llm_router import run_routed_call


def test_hedged_gemini_wins_before_delay_and_openrouter_not_started() -> None:
    state = {"openrouter_called": 0}

    def _gemini() -> str:
        time.sleep(0.01)
        return "gemini-ok"

    def _openrouter() -> str:
        state["openrouter_called"] += 1
        return "openrouter-ok"

    out = run_routed_call(
        mode="hedged",
        stage="unit",
        hedge_delay_s=0.2,
        gemini_call=_gemini,
        openrouter_call=_openrouter,
    )
    assert out.provider == "gemini"
    assert out.value == "gemini-ok"
    assert state["openrouter_called"] == 0


def test_hedged_openrouter_wins_after_delay() -> None:
    def _gemini() -> str:
        time.sleep(0.6)
        return "gemini-ok"

    def _openrouter() -> str:
        return "openrouter-ok"

    t0 = time.monotonic()
    out = run_routed_call(
        mode="hedged",
        stage="unit",
        hedge_delay_s=0.05,
        gemini_call=_gemini,
        openrouter_call=_openrouter,
    )
    elapsed = time.monotonic() - t0
    assert out.provider == "openrouter"
    assert out.value == "openrouter-ok"
    assert elapsed < 0.25


def test_hedged_first_failure_second_success() -> None:
    def _gemini() -> str:
        raise RuntimeError("invalid_schema")

    def _openrouter() -> str:
        return "openrouter-ok"

    out = run_routed_call(
        mode="hedged",
        stage="unit",
        hedge_delay_s=1.0,
        gemini_call=_gemini,
        openrouter_call=_openrouter,
    )
    assert out.provider == "openrouter"
    assert out.value == "openrouter-ok"


def test_hedged_both_fail_reports_both_errors() -> None:
    def _gemini() -> str:
        raise RuntimeError("gemini_boom")

    def _openrouter() -> str:
        raise RuntimeError("openrouter_boom")

    try:
        run_routed_call(
            mode="hedged",
            stage="unit",
            hedge_delay_s=0.01,
            gemini_call=_gemini,
            openrouter_call=_openrouter,
        )
        assert False, "expected failure"
    except RuntimeError as e:
        msg = str(e)
        assert "llm_hedged_all_failed" in msg
        assert "gemini" in msg
        assert "openrouter" in msg
