from __future__ import annotations

from services.orchestrator.tasks import _extract_artifacts_source


def test_extract_artifacts_source_prefers_direct_key() -> None:
    payload = {
        "artifacts_s3_uri": "s3://bucket/path/job.tar.gz",
        "message": "ok; artifacts=s3://other/path.tar.gz",
    }
    assert _extract_artifacts_source(payload) == "s3://bucket/path/job.tar.gz"


def test_extract_artifacts_source_from_message_marker() -> None:
    payload = {
        "message": "ok; uploaded=1; artifacts=s3://bucket/ae_jobs/job_1.tar.gz; local_job_dir_deleted=1"
    }
    assert _extract_artifacts_source(payload) == "s3://bucket/ae_jobs/job_1.tar.gz"


def test_extract_artifacts_source_from_http_message_marker() -> None:
    payload = {
        "message": "ok; artifacts=https://example.com/archive.zip; note=done"
    }
    assert _extract_artifacts_source(payload) == "https://example.com/archive.zip"


def test_extract_artifacts_source_missing_returns_empty() -> None:
    payload = {"message": "ok; no artifacts here"}
    assert _extract_artifacts_source(payload) == ""
