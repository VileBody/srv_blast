from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from mlcore.alignment import client as alignment_client


def test_internal_alignment_request_ignores_proxy_environment(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class _Response:
        status_code = 503

        @staticmethod
        def json():
            return {
                "error": {
                    "code": "ALIGNMENT_MODEL_UNAVAILABLE",
                    "message": "not ready",
                    "details": {"hard_valid_candidate_count": 0},
                }
            }

    class _Client:
        def __init__(self, **kwargs):
            observed["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, json):
            observed["url"] = url
            observed["payload"] = json
            return _Response()

    monkeypatch.setattr(alignment_client.httpx, "Client", _Client)

    with pytest.raises(
        alignment_client.AlignmentServiceError,
        match="ALIGNMENT_MODEL_UNAVAILABLE: not ready",
    ) as exc:
        alignment_client.request_local_alignment(
            service_url="http://alignment-api:8000",
            timeout_s=600.0,
            audio_path=Path("/app/work/jobs/job/audio.mp3"),
            target_fragment="точный фрагмент",
            clip_start_abs=1.0,
            clip_end_abs=4.0,
            request_id="job",
        )

    assert exc.value.details == {"hard_valid_candidate_count": 0}
    assert observed["client_kwargs"] == {"timeout": 600.0, "trust_env": False}
    assert observed["url"] == "http://alignment-api:8000/align"


def test_alignment_service_error_survives_pickle() -> None:
    error = alignment_client.AlignmentServiceError(
        "ALIGNMENT_TIMEOUT",
        "inference timed out",
    )

    restored = pickle.loads(pickle.dumps(error))

    assert isinstance(restored, alignment_client.AlignmentServiceError)
    assert restored.job_stage == "alignment"
    assert restored.code == "ALIGNMENT_TIMEOUT"
    assert restored.message == "inference timed out"
    assert str(restored) == "ALIGNMENT_TIMEOUT: inference timed out"
