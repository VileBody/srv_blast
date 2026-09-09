"""Regression guard for Telegram bot entrypoint imports."""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    ("services.tg_bot_botapi.app", "services.tg_bot_public.app"),
)
def test_bot_entrypoint_imports_from_scratch(module: str) -> None:
    """Catch missing rollout modules before either bot reaches production."""
    sys.modules.pop(module, None)
    assert importlib.import_module(module) is not None
