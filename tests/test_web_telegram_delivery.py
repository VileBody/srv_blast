from __future__ import annotations

import importlib
from pathlib import Path


def _bot(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web_app" / "backend"))
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("BLAST_BACKEND_MODE", "mock")
    return importlib.import_module("web_app.backend.app.telegram_bot")


def test_every_successful_login_sends_confirmation_and_generation_bot(monkeypatch):
    bot = _bot(monkeypatch)
    sent = []
    monkeypatch.setattr(bot.auth_store, "confirm_token", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(bot, "_api", lambda method, params: sent.append((method, params)) or {"ok": True})
    for token in ("first-login", "repeat-login"):
        bot._handle_start(123, token, {})
    assert len(sent) == 2
    for method, params in sent:
        assert method == "sendMessage"
        assert "Вход выполнен" in params["text"]
        assert "@blast808bot" in params["text"]
        assert "reply_markup" in params


def test_rejected_message_is_reported_as_failure(monkeypatch, caplog):
    bot = _bot(monkeypatch)
    monkeypatch.setattr(bot, "_api", lambda *args: {"ok": False, "error_code": 403, "description": "Forbidden"})
    assert bot._send(123, "message") is False
    assert "sendMessage rejected code=403" in caplog.text


def test_uncertain_delivery_is_logged_without_token_or_blind_resend(monkeypatch, caplog):
    bot = _bot(monkeypatch)
    calls = []

    def fail(*args):
        calls.append(args)
        raise TimeoutError("https://api.telegram.org/botSECRET/sendMessage")

    monkeypatch.setattr(bot, "_api", fail)
    assert bot._send(123, "message") is False
    assert len(calls) == 1
    assert "delivery unknown error=TimeoutError" in caplog.text
    assert "SECRET" not in caplog.text
