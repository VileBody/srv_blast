"""Encrypted, process-independent storage for TikTok OAuth tokens.

Production should provide TIKTOK_TOKEN_KEY (a Fernet key) through its secret manager.
For local development only, a random key is generated in backend/.runtime, which is
gitignored. Tokens are never returned through the public profile API.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken


RUNTIME_DIR = Path(__file__).resolve().parent.parent / ".runtime"
KEY_PATH = RUNTIME_DIR / "tiktok_token.key"
STORE_PATH = RUNTIME_DIR / "tiktok_tokens.enc"
_lock = threading.RLock()
_scheduler_started = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _key() -> bytes:
    configured = os.getenv("TIKTOK_TOKEN_KEY", "").strip().encode("ascii")
    if configured:
        return configured
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    value = Fernet.generate_key()
    temp = KEY_PATH.with_suffix(".tmp")
    temp.write_bytes(value)
    temp.replace(KEY_PATH)
    return value


def _read_all() -> dict[str, dict[str, Any]]:
    if not STORE_PATH.exists():
        return {}
    try:
        raw = Fernet(_key()).decrypt(STORE_PATH.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (InvalidToken, ValueError, json.JSONDecodeError):
        # A key mismatch must not leak ciphertext or silently expose tokens.
        return {}


def _write_all(data: dict[str, dict[str, Any]]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = Fernet(_key()).encrypt(encoded)
    temp = STORE_PATH.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    temp.replace(STORE_PATH)


def save(user_id: str, record: dict[str, Any]) -> None:
    with _lock:
        data = _read_all()
        data[user_id] = record
        _write_all(data)


def load(user_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _read_all().get(user_id)
        return dict(record) if record else None


def delete(user_id: str) -> None:
    with _lock:
        data = _read_all()
        if data.pop(user_id, None) is not None:
            _write_all(data)


def needs_refresh(record: dict[str, Any], within: timedelta = timedelta(minutes=10)) -> bool:
    value = record.get("expiresAt")
    if not value:
        return True
    try:
        expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires_at <= _utcnow() + within


def ensure_scheduler(refresh_all: Callable[[], None], interval_seconds: int = 900) -> None:
    """Run token refresh checks every 15 minutes in this API process."""
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def loop() -> None:
        import time
        while True:
            try:
                refresh_all()
            except Exception:
                # A transient TikTok outage must not stop the next scheduled refresh.
                pass
            time.sleep(interval_seconds)

    threading.Thread(target=loop, name="tiktok-token-refresh", daemon=True).start()
