from services.orchestrator.alignment_smoke_auth import (
    alignment_smoke_authorization_digest,
    alignment_smoke_authorization_key,
    consume_alignment_smoke_authorization,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def authorize(self, nonce: str, payload: dict) -> None:
        self.values[alignment_smoke_authorization_key(nonce)] = (
            alignment_smoke_authorization_digest(payload)
        )

    def eval(self, _script: str, _key_count: int, key: str):
        return self.values.pop(str(key), None)


def _payload() -> dict:
    return {
        "audio_s3_url": "s3://media/raw_audio/test.mp3",
        "target_fragment": "exact text",
        "clip_start_abs": 10.0,
        "clip_end_abs": 25.0,
        "request_id": "smoke:batch:run",
        "idempotency_key": "alignment-smoke:batch:run",
    }


def test_alignment_smoke_authorization_is_single_use() -> None:
    redis = _FakeRedis()
    payload = _payload()
    nonce = "n" * 32
    redis.authorize(nonce, payload)

    assert consume_alignment_smoke_authorization(
        redis,
        payload,
        nonce=nonce,
    )
    assert not consume_alignment_smoke_authorization(
        redis,
        payload,
        nonce=nonce,
    )


def test_alignment_smoke_authorization_rejects_tampering_and_is_consumed() -> None:
    redis = _FakeRedis()
    payload = _payload()
    nonce = "t" * 32
    redis.authorize(nonce, payload)
    tampered = {**payload, "clip_end_abs": 26.0}

    assert not consume_alignment_smoke_authorization(
        redis,
        tampered,
        nonce=nonce,
    )
    assert not consume_alignment_smoke_authorization(
        redis,
        payload,
        nonce=nonce,
    )


def test_alignment_smoke_authorization_rejects_missing_nonce() -> None:
    redis = _FakeRedis()
    assert not consume_alignment_smoke_authorization(
        redis,
        _payload(),
        nonce="m" * 32,
    )
