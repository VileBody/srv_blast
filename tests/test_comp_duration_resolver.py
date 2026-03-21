from __future__ import annotations

import logging

import pytest

from app.footage_comp import resolve_text_duration_sec


def test_comp_duration_prefers_explicit_composition_duration() -> None:
    got = resolve_text_duration_sec(
        composition_dur=12.5,
        footage_cfg={"text_dur_hint": 20.0},
        layers_cfg=[{"out_point": 30.0}],
    )
    assert abs(float(got) - 12.5) <= 1e-6


def test_comp_duration_fallback_to_text_dur_hint_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.footage_comp"):
        got = resolve_text_duration_sec(
            composition_dur=None,
            footage_cfg={"text_dur_hint": 15.25},
            layers_cfg=[{"out_point": 9.0}],
        )
    assert abs(float(got) - 15.25) <= 1e-6
    assert "comp_duration_fallback used=text_dur_hint" in caplog.text


def test_comp_duration_fallback_to_layers_max_out_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.footage_comp"):
        got = resolve_text_duration_sec(
            composition_dur=None,
            footage_cfg={},
            layers_cfg=[{"out_point": 7.0}, {"out_point": 11.75}, {"out_point": 3.0}],
        )
    assert abs(float(got) - 11.75) <= 1e-6
    assert "comp_duration_fallback used=max_out_point" in caplog.text


def test_comp_duration_raises_when_all_sources_missing() -> None:
    with pytest.raises(RuntimeError, match="Unable to resolve composition duration"):
        resolve_text_duration_sec(
            composition_dur=None,
            footage_cfg={},
            layers_cfg=[{"name": "x"}, {}],
        )
