from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.orchestrator import app as orchestrator_app


def test_rust_gen_api_gate_requires_enabled_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        SimpleNamespace(
            rust_gen_enabled=False,
            rust_gen_manager_url="",
            rust_gen_canary_enabled=False,
            rust_gen_canary_subtitle_modes=(),
        ),
    )

    with pytest.raises(HTTPException, match="rust-gen renderer is disabled") as exc:
        orchestrator_app._ensure_render_engine_available({"render_engine": "rust-gen"})

    assert exc.value.status_code == 503


def test_rust_gen_api_gate_honors_subtitle_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        SimpleNamespace(
            rust_gen_enabled=True,
            rust_gen_manager_url="http://rust-gen:8090",
            rust_gen_canary_enabled=True,
            rust_gen_canary_subtitle_modes=("brat_5th",),
        ),
    )

    orchestrator_app._ensure_render_engine_available(
        {"render_engine": "rust-gen", "subtitles_mode": "brat_5th"}
    )
    with pytest.raises(HTTPException, match="canary does not include"):
        orchestrator_app._ensure_render_engine_available(
            {"render_engine": "rust-gen", "subtitles_mode": "impulse_2nd"}
        )
