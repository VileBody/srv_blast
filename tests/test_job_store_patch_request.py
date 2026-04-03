from __future__ import annotations

from services.orchestrator.job_store import JobStore
from services.orchestrator.schemas import JobState


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    def get(self, key: str):
        return self._kv.get(key)

    def set(self, key: str, value, ex=None, nx=False):  # noqa: ANN001
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True


def test_patch_request_updates_existing_job_request() -> None:
    store = JobStore(r=_FakeRedis(), key_prefix="test")
    st = JobState(
        job_id="job-1",
        status="NEW",
        created_at=1.0,
        updated_at=1.0,
        queued_at=None,
        started_at=None,
        finished_at=None,
        stage=None,
        idempotency_key=None,
        request={"audio_s3_url": "s3://bucket/raw.mp3"},
        result=None,
        error=None,
    )
    store._put(st)

    out = store.patch_request("job-1", {"llm_worker_type": "sdk"})
    assert out is not None
    assert out.request["audio_s3_url"] == "s3://bucket/raw.mp3"
    assert out.request["llm_worker_type"] == "sdk"


def test_patch_request_returns_none_for_missing_job() -> None:
    store = JobStore(r=_FakeRedis(), key_prefix="test")
    assert store.patch_request("missing-job", {"x": "y"}) is None
