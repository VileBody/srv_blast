from __future__ import annotations

import importlib

import pytest


def _reload_public_config():
    import services.tg_bot_public.config as cfg

    return importlib.reload(cfg)


def test_public_telegram_test_mode_uses_test_bot_token_and_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_API_ENV", "test")
    monkeypatch.setenv("TG_DELIVERY_MODE", "webhook")
    monkeypatch.setenv("TG_WEBHOOK_URL", "https://example.test")
    monkeypatch.setenv("TG_TEST_BOT_TOKEN", "999:test")
    monkeypatch.setenv("TG_TEST_BOT_USERNAME", "blast_test_bot")
    monkeypatch.setenv("TG_TEST_CREDITS_DB_URL", "postgresql://test:test@localhost:5432/blast_test")
    monkeypatch.setenv("TG_TEST_BYPASS_SUBSCRIPTION", "1")
    monkeypatch.setenv("CREDITS_DB_URL", "postgresql://prod:prod@localhost:5432/prod")

    cfg = _reload_public_config()

    assert cfg.SETTINGS.tg_bot_api_env == "test"
    assert cfg.SETTINGS.tg_bot_token == "999:test"
    assert cfg.SETTINGS.tg_bot_username == "blast_test_bot"
    assert cfg.SETTINGS.credits_db_url.endswith("/blast_test")
    assert cfg.SETTINGS.tg_test_bypass_subscription is True

    monkeypatch.delenv("TG_BOT_API_ENV", raising=False)
    monkeypatch.delenv("TG_DELIVERY_MODE", raising=False)
    monkeypatch.delenv("TG_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TG_TEST_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_TEST_BOT_USERNAME", raising=False)
    monkeypatch.delenv("TG_TEST_CREDITS_DB_URL", raising=False)
    monkeypatch.delenv("TG_TEST_BYPASS_SUBSCRIPTION", raising=False)
    monkeypatch.delenv("CREDITS_DB_URL", raising=False)
    _reload_public_config()
