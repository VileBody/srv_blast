from __future__ import annotations

from pathlib import Path


_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "app.py"


def _app_source() -> str:
    return _APP_PATH.read_text(encoding="utf-8")


def test_generation_failflow_user_message_and_wait_audio_reset_present() -> None:
    src = _app_source()
    assert "Увидели ошибку, сейчас с тобой свяжется менеджер и запустит генерацию ролика вручную," in src
    assert "а пока тех. отдел все проверит" in src
    assert 'self._reset_processing_state(st, next_stage=STAGE_WAIT_AUDIO)' in src


def test_generation_failflow_logs_and_refund_present() -> None:
    src = _app_source()
    assert '"generation_failed_refund"' in src
    assert '"generation_failed"' in src
    assert "def _notify_manager_generation_failure(" in src
