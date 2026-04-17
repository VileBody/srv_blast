from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_tg_public_config_declares_maintenance_bypass_envs() -> None:
    src = _read("services/tg_bot_public/config.py")
    assert "SYSTEM_MAINTENANCE_BYPASS_USERNAMES" in src
    assert "SYSTEM_MAINTENANCE_BYPASS_TOKEN" in src


def test_tg_public_app_uses_bypass_allowlist_and_token_on_enqueue() -> None:
    src = _read("services/tg_bot_public/app.py")
    assert "_allow_maintenance_bypass_for_message" in src
    assert "self.settings.system_maintenance_bypass_token" in src
    assert "maintenance_bypass_token=maintenance_bypass_token" in src


def test_orchestrator_client_payload_contains_maintenance_bypass_token() -> None:
    src = _read("services/tg_bot_public/orchestrator_client.py")
    assert '"maintenance_bypass_token"' in src
