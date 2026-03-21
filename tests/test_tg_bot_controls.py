from __future__ import annotations

from services.tg_bot_botapi.app import (
    _build_subtitles_debug_text,
    _extract_footage_file_names,
    _is_control_button_text,
    _is_username_allowed,
    _load_used_footage_file_names_for_job,
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
    assert _is_control_button_text("Scenes 3rd Single-Step") is True
    assert _is_control_button_text("Template 4th") is True
    assert _is_control_button_text(" 3 ") is True
    assert _is_control_button_text("Это реальный текст песни") is False


def test_subtitles_mode_choice_parser() -> None:
    assert _parse_subtitles_mode_choice("Обычные blocks") == "legacy_blocks"
    assert _parse_subtitles_mode_choice("Impulse 2nd") == "impulse_2nd"
    assert _parse_subtitles_mode_choice("Scenes 3rd") == "scenes_3rd"
    assert _parse_subtitles_mode_choice("Scenes 3rd Single-Step") == "scenes_3rd_single_step"
    assert _parse_subtitles_mode_choice("Template 4th") == "template_4th"
    assert _parse_subtitles_mode_choice("unknown") is None


def test_build_impulse_debug_text_uses_reason_from_raw_payload() -> None:
    final_payload = {
        "mode": "impulse_2nd",
        "clip": {"start": 10.0, "end": 12.0},
        "segments": [
            {"segment_id": "impulse_001", "text": "мы станем", "in_point": 10.0, "out_point": 11.2, "style_tag": "long"},
            {"segment_id": "impulse_002", "text": "чужими", "in_point": 11.2, "out_point": 12.0, "style_tag": "short"},
        ],
    }
    raw_payload = {
        "anchor_in_abs": 10.0,
        "segments": [
            {"text": "мы станем", "in": 0.0, "out": 1.2, "type": "long", "reason": "base phrase"},
            {"text": "чужими", "in": 1.2, "out": 2.0, "type": "short", "reason": "accent word"},
        ],
    }

    text = _build_subtitles_debug_text(
        ver_label="Версия 1/1",
        final_payload=final_payload,
        raw_payload=raw_payload,
    )

    assert "Разметка Impulse 2nd" in text
    assert "SHORT" in text
    assert "reason:" in text
    assert "accent word" in text


def test_build_scenes_debug_text_contains_type_and_focus() -> None:
    final_payload = {
        "mode": "scenes_3rd",
        "clip": {"start": 100.0, "end": 104.0},
        "segments": [
            {
                "segment_id": "scene_001",
                "text": "она кричала",
                "in_point": 100.0,
                "out_point": 101.3,
                "style_tag": "TYPE_4",
                "lines": ["она кричала"],
                "focus_word": "кричала",
                "focus_style": "red",
            }
        ],
    }

    text = _build_subtitles_debug_text(
        ver_label="Версия 1/1",
        final_payload=final_payload,
        raw_payload=None,
    )

    assert "Разметка Scenes 3rd" in text
    assert "TYPE_4" in text
    assert "focus=" in text
    assert "кричала:red" in text


def test_extract_footage_file_names_dedupes_and_skips_empty() -> None:
    payload = {
        "clips": [
            {"file_name": "a.mp4"},
            {"file_name": "b.mp4"},
            {"file_name": "a.mp4"},
            {"file_name": ""},
            {},
        ]
    }
    assert _extract_footage_file_names(payload) == ["a.mp4", "b.mp4"]


def test_load_used_footage_file_names_for_job_reads_stage2_footage(monkeypatch, tmp_path) -> None:
    job_id = "job123"
    logs = tmp_path / job_id / "out" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "stage2_footage.json").write_text(
        '{"clips":[{"file_name":"x.mp4"},{"file_name":"y.mp4"},{"file_name":"x.mp4"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_JOBS_OUTPUT_DIR", str(tmp_path))
    assert _load_used_footage_file_names_for_job(job_id) == ["x.mp4", "y.mp4"]
