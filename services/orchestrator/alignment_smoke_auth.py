from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Mapping


_SIGNED_FIELDS = (
    "audio_s3_url",
    "target_fragment",
    "clip_start_abs",
    "clip_end_abs",
    "request_id",
    "idempotency_key",
    "auth_timestamp",
)


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    signed = {key: payload.get(key) for key in _SIGNED_FIELDS}
    return json.dumps(
        signed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_alignment_smoke_request(
    payload: Mapping[str, Any],
    *,
    secret: str,
) -> str:
    key = str(secret or "").strip()
    if not key:
        raise RuntimeError("alignment smoke signing secret is empty")
    return hmac.new(
        key.encode("utf-8"),
        _canonical_payload(payload),
        hashlib.sha256,
    ).hexdigest()


def alignment_smoke_signature_is_valid(
    payload: Mapping[str, Any],
    *,
    secret: str,
    max_age_s: int = 120,
    now: float | None = None,
) -> bool:
    key = str(secret or "").strip()
    signature = str(payload.get("auth_signature") or "").strip().lower()
    if not key or len(signature) != 64:
        return False
    try:
        timestamp = int(payload.get("auth_timestamp"))
    except (TypeError, ValueError):
        return False
    current = int(time.time() if now is None else now)
    if timestamp > current + 15 or current - timestamp > int(max_age_s):
        return False
    expected = sign_alignment_smoke_request(payload, secret=key)
    return hmac.compare_digest(signature, expected)
