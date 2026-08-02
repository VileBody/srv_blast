from services.orchestrator.alignment_smoke_auth import (
    alignment_smoke_signature_is_valid,
    sign_alignment_smoke_request,
)


def _payload(timestamp: int = 1_800_000_000) -> dict:
    return {
        "audio_s3_url": "s3://media/raw_audio/test.mp3",
        "target_fragment": "exact text",
        "clip_start_abs": 10.0,
        "clip_end_abs": 25.0,
        "request_id": "smoke:batch:run",
        "idempotency_key": "alignment-smoke:batch:run",
        "auth_timestamp": timestamp,
    }


def test_alignment_smoke_signature_accepts_fresh_exact_payload() -> None:
    payload = _payload()
    payload["auth_signature"] = sign_alignment_smoke_request(
        payload, secret="secret"
    )
    assert alignment_smoke_signature_is_valid(
        payload,
        secret="secret",
        now=1_800_000_030,
    )


def test_alignment_smoke_signature_rejects_tampering() -> None:
    payload = _payload()
    payload["auth_signature"] = sign_alignment_smoke_request(
        payload, secret="secret"
    )
    payload["clip_end_abs"] = 26.0
    assert not alignment_smoke_signature_is_valid(
        payload,
        secret="secret",
        now=1_800_000_030,
    )


def test_alignment_smoke_signature_rejects_expired_request() -> None:
    payload = _payload()
    payload["auth_signature"] = sign_alignment_smoke_request(
        payload, secret="secret"
    )
