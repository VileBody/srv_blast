from __future__ import annotations

from pathlib import Path


_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "app.py"
_TEAM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_botapi" / "app.py"
_PUBLIC_SS_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "state_store.py"
_TEAM_SS_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_botapi" / "state_store.py"


def _app_source() -> str:
    return _APP_PATH.read_text(encoding="utf-8")


def _team_app_source() -> str:
    return _TEAM_APP_PATH.read_text(encoding="utf-8")


# ── existing reuse-input tests ────────────────────────────────────────────────

def test_reuse_input_button_wired_in_wait_audio_source() -> None:
    src = _app_source()
    assert 'BTN_REUSE_INPUT = "Сделать под тот же трек"' in src
    assert "if text == BTN_REUSE_INPUT:" in src
    assert "await self._ask_footage_genre(message, st)" in src


def test_reset_processing_state_does_not_drop_fragment_or_timing_source() -> None:
    src = _app_source()
    start = src.index("def _reset_processing_state(")
    tail = src[start:]
    end = tail.index("async def _send_long_html_message(")
    reset_body = tail[:end]

    assert "st.target_fragment = \"\"" not in reset_body
    assert "st.user_clip_start_sec = 0.0" not in reset_body
    assert "st.user_clip_end_sec = 0.0" not in reset_body


def test_can_reuse_input_checks_file_id_and_prepared_path_source() -> None:
    src = _app_source()
    assert "def _can_reuse_input(st: ChatState) -> bool:" in src
    assert "if str(st.pending_audio_file_id or \"\").strip():" in src
    assert "Path(prepared_raw).expanduser().resolve().exists()" in src


# ── bigtest parity: public bot must carry the flag + stub ────────────────────

def test_bigtest_disabled_in_public_bot_source() -> None:
    src = _app_source()
    assert "BIGTEST_ENABLED: bool = False" in src, (
        "Public bot must declare BIGTEST_ENABLED = False for parity"
    )


def test_bigtest_stub_handler_registered_in_public_bot_source() -> None:
    src = _app_source()
    assert 'Command("bigtest")' in src, (
        "/bigtest handler must be registered in public bot (parity)"
    )
    assert "Эта команда недоступна" in src, (
        "Public bot /bigtest stub must reply with rejection message"
    )


def test_bigtest_state_fields_in_public_state_store() -> None:
    src = _PUBLIC_SS_PATH.read_text(encoding="utf-8")
    for field in ("bigtest_mode", "bigtest_index", "bigtest_total",
                  "bigtest_current_label", "bigtest_master_job_id"):
        assert field in src, f"Public ChatState missing bigtest field: {field}"


# ── team bot: reuse-input wired in _handle_wait_next ─────────────────────────

def test_team_bot_reuse_input_in_wait_next_source() -> None:
    src = _team_app_source()
    assert "BTN_REUSE_INPUT" in src
    assert "await self._ask_bg_mode(message, st)" in src, (
        "Team bot reuse flow must go to _ask_bg_mode (not footage_genre)"
    )


def test_team_bot_batch_completion_restores_audio_s3_url_source() -> None:
    src = _team_app_source()
    assert "_saved_audio_s3 = str(st.batch_audio_s3_url or \"\")" in src, (
        "batch completion must save batch_audio_s3_url before _reset_processing_state"
    )
    assert "st.batch_audio_s3_url = _saved_audio_s3" in src, (
        "batch completion must restore batch_audio_s3_url after reset for /bigtest reuse"
    )


def test_team_bot_bigtest_cases_count_source() -> None:
    src = _team_app_source()
    assert "_BIGTEST_CASES" in src
    # 28 cases: count dict entries by label keys
    label_count = src.count('"label":')
    assert label_count >= 28, f"Expected at least 28 bigtest cases, found {label_count}"


def test_team_bot_bigtest_state_fields_in_state_store() -> None:
    src = _TEAM_SS_PATH.read_text(encoding="utf-8")
    for field in ("bigtest_mode", "bigtest_index", "bigtest_total",
                  "bigtest_current_label", "bigtest_master_job_id"):
        assert field in src, f"Team ChatState missing bigtest field: {field}"
