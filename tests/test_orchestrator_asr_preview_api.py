"""`POST /asr/preview` — примерка субтитров для веб-визарда.

Скоуп аудио тот же, что у alignment-smoke: только raw-audio бакет. Воркер
скачивает файл сам, поэтому произвольный http(s) сюда пускать нельзя.
Джоба уходит в очередь `<build>.alignment-smoke` (тот же воркер, что smoke).
"""
from __future__ import annotations

import pytest

# тот же харнесс (стабы redis/celery/asyncpg + фейковый стор), что у admin-jobs
from tests.test_orchestrator_admin_jobs import _FakeStore, _build_client, orchestrator_app


def _payload(url: str) -> dict:
    return {
        "audio_s3_url": url,
        "target_fragment": "раз два три",
        "clip_start_abs": 10.0,
        "clip_end_abs": 22.0,
        "idempotency_key": "web-asr:u1:k1",
    }


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_RAW_AUDIO", "raw-audio")
    monkeypatch.setenv("S3_RAW_AUDIO_PREFIX", "raw_audio")
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        orchestrator_app.asr_preview_job,
        "apply_async",
        lambda *, args, queue: queued.append((args[0], queue)),
        raising=False,
    )
    store = _FakeStore([])
    return _build_client(monkeypatch, store), store, queued


def test_asr_preview_enqueues_on_smoke_queue(api) -> None:
    client, store, queued = api
    r = client.post("/asr/preview", json=_payload("s3://raw-audio/raw_audio/web/u1/track.mp3"))
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert r.json()["created"] is True
    assert queued and queued[0][0] == job_id and queued[0][1].endswith(".alignment-smoke")
    req = store.get(job_id).request
    assert req["job_kind"] == "asr_preview"
    assert req["stage1_alignment_backend"] == "local_ctc"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/track.mp3",  # http(s) — SSRF из воркера
        "s3://other-bucket/raw_audio/web/u1/track.mp3",  # чужой бакет
        "s3://raw-audio/elsewhere/track.mp3",  # чужой префикс
    ],
)
def test_asr_preview_rejects_audio_outside_raw_scope(api, url: str) -> None:
    client, _store, queued = api
    r = client.post("/asr/preview", json=_payload(url))
    assert r.status_code == 422, r.text
    assert queued == []


def test_asr_preview_rejects_inverted_window(api) -> None:
    client, _store, _queued = api
    bad = _payload("s3://raw-audio/raw_audio/web/u1/track.mp3")
    bad["clip_end_abs"] = 5.0
    assert client.post("/asr/preview", json=bad).status_code == 422
