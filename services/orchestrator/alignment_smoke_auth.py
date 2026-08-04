from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

_SIGNED_FIELDS = (
    "audio_s3_url",
    "target_fragment",
    "clip_start_abs",
    "clip_end_abs",
    "request_id",
    "idempotency_key",
)

AUTHORIZATION_TTL_S = 120
_AUTHORIZATION_KEY_PREFIX = "blast:alignment-smoke:authorization:"
_CONSUME_AUTHORIZATION_LUA = """
local value = redis.call('GET', KEYS[1])
if value then
  redis.call('DEL', KEYS[1])
end
return value
"""


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    signed = {key: payload.get(key) for key in _SIGNED_FIELDS}
    return json.dumps(
        signed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def alignment_smoke_authorization_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def alignment_smoke_authorization_key(nonce: str) -> str:
    value = str(nonce or "").strip()
    if not value or len(value) > 128:
        raise ValueError("invalid alignment smoke authorization nonce")
    return f"{_AUTHORIZATION_KEY_PREFIX}{value}"


def consume_alignment_smoke_authorization(
    redis_client: Any,
    payload: Mapping[str, Any],
    *,
    nonce: str,
) -> bool:
    key = alignment_smoke_authorization_key(nonce)
    stored = redis_client.eval(_CONSUME_AUTHORIZATION_LUA, 1, key)
    if isinstance(stored, bytes):
        stored = stored.decode("utf-8", errors="replace")
    actual = str(stored or "").strip().lower()
    expected = alignment_smoke_authorization_digest(payload)
    return len(actual) == 64 and hmac.compare_digest(actual, expected)
