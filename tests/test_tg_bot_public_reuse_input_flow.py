from __future__ import annotations

from pathlib import Path


_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "app.py"


def _app_source() -> str:
    return _APP_PATH.read_text(encoding="utf-8")


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
