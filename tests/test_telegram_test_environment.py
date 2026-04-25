from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core.telegram_api import build_aiogram_session, make_telegram_api, normalize_telegram_api_env
from scripts.telegram_test_botfather import BotFatherConfig
from scripts.telegram_test_control import _configure_env_file, _init_env_file, _merged_env, render_remote_env
from scripts.telegram_test_load import TestLoadConfig as LoadConfig


def test_telegram_api_urls_prod_and_test() -> None:
    prod = make_telegram_api("prod")
    test = make_telegram_api("test")

    assert prod.method_url(token="123:abc", method="sendMessage") == "https://api.telegram.org/bot123:abc/sendMessage"
    assert test.method_url(token="123:abc", method="sendMessage") == "https://api.telegram.org/bot123:abc/test/sendMessage"
    assert prod.file_url(token="123:abc", path="voice/file 1.mp3") == "https://api.telegram.org/file/bot123:abc/voice/file%201.mp3"
    assert test.file_url(token="123:abc", path="voice/file 1.mp3") == "https://api.telegram.org/file/bot123:abc/test/voice/file%201.mp3"


def test_telegram_api_invalid_mode_and_token_fail_fast() -> None:
    with pytest.raises(RuntimeError, match="TG_BOT_API_ENV"):
        normalize_telegram_api_env("stage")

    with pytest.raises(RuntimeError, match="Telegram bot token"):
        make_telegram_api("prod").method_url(token="", method="getMe")


def test_aiogram_session_uses_test_api_server() -> None:
    session = build_aiogram_session(api_env="test")
    api = getattr(session, "kwargs", {}).get("api") or getattr(session, "api", None)
    assert "/test/{method}" in api.base
    assert "/test/{path}" in api.file


def test_aiogram_session_uses_prod_api_server_without_prod_constant() -> None:
    from aiogram.client.telegram import TelegramAPIServer

    old_production = getattr(TelegramAPIServer, "PRODUCTION", None)
    if hasattr(TelegramAPIServer, "PRODUCTION"):
        delattr(TelegramAPIServer, "PRODUCTION")
    try:
        session = build_aiogram_session(api_env="prod")
    finally:
        if old_production is not None:
            TelegramAPIServer.PRODUCTION = old_production
    api = getattr(session, "kwargs", {}).get("api") or getattr(session, "api", None)
    assert api.base == "https://api.telegram.org/bot{token}/{method}"
    assert api.file == "https://api.telegram.org/file/bot{token}/{path}"


def _reload_public_config(monkeypatch: pytest.MonkeyPatch):
    import services.tg_bot_public.config as cfg

    return importlib.reload(cfg)


def test_public_config_rejects_test_bypass_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_API_ENV", "prod")
    monkeypatch.setenv("TG_TEST_BYPASS_SUBSCRIPTION", "1")

    with pytest.raises(RuntimeError, match="TG_TEST_BYPASS_SUBSCRIPTION"):
        _reload_public_config(monkeypatch)

    monkeypatch.delenv("TG_TEST_BYPASS_SUBSCRIPTION", raising=False)
    _reload_public_config(monkeypatch)


def test_public_config_requires_test_token_in_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_API_ENV", "test")
    monkeypatch.setenv("TG_DELIVERY_MODE", "webhook")
    monkeypatch.setenv("TG_WEBHOOK_URL", "https://example.test")
    monkeypatch.setenv("TG_TEST_BOT_USERNAME", "testbot")
    monkeypatch.setenv("TG_TEST_CREDITS_DB_URL", "postgresql://test:test@localhost:5432/test")

    with pytest.raises(RuntimeError, match="TG_TEST_BOT_TOKEN"):
        _reload_public_config(monkeypatch)

    monkeypatch.delenv("TG_BOT_API_ENV", raising=False)
    monkeypatch.delenv("TG_DELIVERY_MODE", raising=False)
    monkeypatch.delenv("TG_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TG_TEST_BOT_USERNAME", raising=False)
    monkeypatch.delenv("TG_TEST_CREDITS_DB_URL", raising=False)
    _reload_public_config(monkeypatch)


def test_public_config_uses_test_token_username_and_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_API_ENV", "test")
    monkeypatch.setenv("TG_DELIVERY_MODE", "webhook")
    monkeypatch.setenv("TG_WEBHOOK_URL", "https://example.test")
    monkeypatch.setenv("TG_TEST_BOT_TOKEN", "999:test")
    monkeypatch.setenv("TG_TEST_BOT_USERNAME", "blast_test_bot")
    monkeypatch.setenv("TG_TEST_CREDITS_DB_URL", "postgresql://test:test@localhost:5432/blast_test")
    monkeypatch.setenv("TG_TEST_BYPASS_SUBSCRIPTION", "1")
    monkeypatch.setenv("CREDITS_DB_URL", "postgresql://prod:prod@localhost:5432/prod")

    cfg = _reload_public_config(monkeypatch)

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
    _reload_public_config(monkeypatch)


def test_botfather_requires_explicit_test_api_credentials() -> None:
    with pytest.raises(SystemExit, match="TG_TEST_API_ID"):
        BotFatherConfig.from_env({"TG_API_ID": "123", "TG_API_HASH": "hash"}, bot_name="", bot_username="")

    cfg = BotFatherConfig.from_env(
        {
            "TG_TEST_API_ID": "123",
            "TG_TEST_API_HASH": "hash",
            "TG_TEST_DC_ID": "2",
            "TG_TEST_OWNER_SESSION_STRING": "session-string",
        },
        bot_name="Blast Test Bot",
        bot_username="blasttestbot",
    )

    assert cfg.owner_session_string == "session-string"
    assert cfg.login_codes == ["22222", "222222"]


def test_test_load_config_login_codes_include_five_and_six_digit_variants(tmp_path) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    cfg = LoadConfig.from_env(
        {
            "TG_TEST_API_ID": "123",
            "TG_TEST_API_HASH": "hash",
            "TG_TEST_BOT_USERNAME": "blasttestbot",
            "TG_TEST_DC_ID": "3",
            "TG_TEST_AUDIO_PATH": str(audio),
            "TG_TEST_FOOTAGE_GENRE_LABEL": "genre",
            "TG_TEST_FOOTAGE_ARTIST_LABEL": "artist",
        },
        run_id="unit",
        require_scenario=True,
    )

    assert cfg.phone_for_index(7) == "9996631007"
    assert cfg.login_codes == ["33333", "333333"]


def test_control_remote_env_excludes_blast_ops_only_secrets() -> None:
    content = render_remote_env(
        {
            "TG_BOT_API_ENV": "test",
            "TG_DELIVERY_MODE": "webhook",
            "TG_WEBHOOK_URL": "https://blast808.com",
            "TG_WEBHOOK_SECRET": "secret",
            "TG_TEST_BOT_TOKEN": "123:test-token",
            "TG_TEST_BOT_USERNAME": "blasttestbot",
            "TG_TEST_CREDITS_DB_URL": "postgresql://test",
            "TG_TEST_API_ID": "123",
            "TG_TEST_API_HASH": "hash",
            "TG_TEST_OWNER_SESSION_STRING": "session",
        }
    )

    assert "TG_TEST_BOT_TOKEN=123:test-token" in content
    assert "TG_TEST_API_ID" not in content
    assert "TG_TEST_API_HASH" not in content
    assert "TG_TEST_OWNER_SESSION_STRING" not in content


def test_control_env_can_be_initialized_from_example(tmp_path) -> None:
    env_file = tmp_path / ".env"

    _init_env_file(env_file)

    content = env_file.read_text(encoding="utf-8")
    assert "TG_BOT_API_ENV=test" in content
    assert "TG_TEST_CREDITS_DB_URL=<postgresql://" in content


def test_control_env_allows_workflow_env_to_override_placeholders(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TG_TEST_NODE0_HOST=<fill-orchestrator-0-host>\n", encoding="utf-8")
    monkeypatch.setenv("TG_TEST_NODE0_HOST", "10.0.0.10")

    merged = _merged_env(env_file)

    assert merged["TG_TEST_NODE0_HOST"] == "10.0.0.10"


def test_control_configure_env_writes_secret_backed_values(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.setenv("TG_TEST_CONFIG_TG_TEST_API_ID", "123")
    monkeypatch.setenv("TG_TEST_CONFIG_TG_TEST_API_HASH", "hash")
    monkeypatch.setenv("TG_TEST_CONFIG_TG_TEST_CREDITS_DB_URL", "postgresql://test")
    monkeypatch.setenv("TG_TEST_CONFIG_TG_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TG_TEST_CONFIG_TG_TEST_AUDIO_PATH", str(tmp_path / "sample.wav"))
    monkeypatch.setenv("TG_TEST_CONFIG_TG_TEST_FOOTAGE_GENRE_LABEL", "Поп")
    monkeypatch.setenv("TG_TEST_CONFIG_TG_TEST_FOOTAGE_ARTIST_LABEL", "Романтический поп")
    monkeypatch.setenv("TG_TEST_CONFIG_CREATE_SAMPLE_AUDIO", "1")
    monkeypatch.setenv("TG_TEST_NODE0_HOST", "10.0.0.10")

    _configure_env_file(env_file, dry_run=False)

    merged = _merged_env(env_file)
    assert merged["TG_TEST_API_ID"] == "123"
    assert merged["TG_TEST_CREDITS_DB_URL"] == "postgresql://test"
    assert merged["TG_WEBHOOK_SECRET"] == "secret"
    assert merged["TG_TEST_NODE0_HOST"] == "10.0.0.10"
    assert Path(merged["TG_TEST_AUDIO_PATH"]).exists()
