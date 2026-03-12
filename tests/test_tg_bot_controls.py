from __future__ import annotations

from services.tg_bot_botapi.app import (
    _is_control_button_text,
    _is_username_allowed,
    _parse_subtitles_mode_choice,
    _parse_versions_choice,
)
from services.tg_bot_botapi.config import _normalize_username, _username_allowlist_env


def test_versions_choice_accepts_1_to_5_only() -> None:
    assert _parse_versions_choice("1") == 1
    assert _parse_versions_choice("5") == 5
    assert _parse_versions_choice(" 3 ") == 3
    assert _parse_versions_choice("0") is None
    assert _parse_versions_choice("6") is None
    assert _parse_versions_choice("abc") is None


def test_username_allowlist_normalizes_and_deduplicates(monkeypatch) -> None:
    monkeypatch.setenv(
        "ARTIFACTS_ALLOWLIST",
        "NikitaImpulse, @nikitaimpulse, @WhoIsTvoiDiller, whoistvoidiller",
    )
    got = _username_allowlist_env("ARTIFACTS_ALLOWLIST")
    assert got == ("@nikitaimpulse", "@whoistvoidiller")


def test_normalize_username() -> None:
    assert _normalize_username("UserName") == "@username"
    assert _normalize_username("@UserName") == "@username"
    assert _normalize_username("") == ""


def test_is_username_allowed_case_insensitive() -> None:
    allow = ("@nikitaimpulse", "@whoistvoidiller")
    assert _is_username_allowed(username="NikitaImpulse", allowlist=allow) is True
    assert _is_username_allowed(username="@WhoIsTvoidiller", allowlist=allow) is True
    assert _is_username_allowed(username="random_user", allowlist=allow) is False


def test_control_button_text_detection() -> None:
    assert _is_control_button_text("Отправить текст") is True
    assert _is_control_button_text("Отправить интересующий фрагмент") is True
    assert _is_control_button_text("Impulse 2nd") is True
    assert _is_control_button_text(" 3 ") is True
    assert _is_control_button_text("Это реальный текст песни") is False


def test_subtitles_mode_choice_parser() -> None:
    assert _parse_subtitles_mode_choice("Обычные blocks") == "legacy_blocks"
    assert _parse_subtitles_mode_choice("Impulse 2nd") == "impulse_2nd"
    assert _parse_subtitles_mode_choice("Scenes 3rd") == "scenes_3rd"
    assert _parse_subtitles_mode_choice("unknown") is None
