from __future__ import annotations

import pytest

from app.project_builder import _resolve_text_preset


def test_text_preset_env_default_classic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXT_SUBTITLE_PRESET", raising=False)
    assert _resolve_text_preset() == "classic"


def test_text_preset_env_accepts_impulse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_SUBTITLE_PRESET", "impulse")
    assert _resolve_text_preset() == "impulse"


def test_text_preset_env_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_SUBTITLE_PRESET", "legacy")
    with pytest.raises(RuntimeError, match="Invalid TEXT_SUBTITLE_PRESET"):
        _resolve_text_preset()
