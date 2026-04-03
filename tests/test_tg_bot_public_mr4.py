from __future__ import annotations

import re
from pathlib import Path


_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "app.py"


def _app_source() -> str:
    return _APP_PATH.read_text(encoding="utf-8")


def test_enqueue_batch_idempotency_key_is_deterministic_in_source() -> None:
    src = _app_source()

    assert "def _build_batch_idempotency_key" in src
    assert "tg-{int(chat_id)}-batch-{str(batch_id or '').strip()}-v{int(version_index)}" in src

    match = re.search(r"def _enqueue_batch_version\([\s\S]+?\n\s*def _progress_interval_s", src)
    assert match is not None
    enqueue_body = match.group(0)
    assert "idempotency_key=idem" in enqueue_body
    assert "uuid.uuid4" not in enqueue_body


def test_referral_batch_id_is_stable_in_source() -> None:
    src = _app_source()
    assert "def _build_referral_batch_id" in src
    assert "referral-round-2" in src
