from __future__ import annotations

from datetime import datetime, timezone

import boto3
import pytest
from botocore.stub import Stubber

from src.storage import s3 as s3_storage


def _make_stubbed_s3():
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_list_s3_objects_returns_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_stubbed_s3()
    response = {
        "IsTruncated": True,
        "NextContinuationToken": "cursor_2",
        "Contents": [
            {
                "Key": "clips/one.mp4",
                "Size": 42,
                "ETag": '"etag1"',
                "LastModified": datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc),
            }
        ],
        "CommonPrefixes": [{"Prefix": "clips/sub/"}],
    }
    expected_params = {
        "Bucket": "bucket",
        "Prefix": "clips/",
        "MaxKeys": 200,
        "Delimiter": "/",
    }

    with Stubber(client) as stubber:
        stubber.add_response("list_objects_v2", response, expected_params)
        monkeypatch.setattr(s3_storage, "get_s3_client", lambda: client)

        out = s3_storage.list_s3_objects("bucket", prefix="clips/")

    assert out["next_continuation_token"] == "cursor_2"
    assert out["is_truncated"] is True
    assert out["prefixes"] == ["clips/sub/"]
    assert len(out["objects"]) == 1
    assert out["objects"][0]["key"] == "clips/one.mp4"


def test_generate_presigned_url_uses_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def generate_presigned_url(self, *, ClientMethod, Params, ExpiresIn):  # noqa: N803
            self.calls.append(
                {
                    "ClientMethod": ClientMethod,
                    "Params": Params,
                    "ExpiresIn": ExpiresIn,
                }
            )
            return "https://example.local/presigned"

    fake = _FakeClient()
    monkeypatch.setattr(s3_storage, "get_s3_client", lambda: fake)

    url = s3_storage.generate_presigned_url("bucket", "clips/one.mp4", expires_in=900)
    assert url == "https://example.local/presigned"
    assert fake.calls == [
        {
            "ClientMethod": "get_object",
            "Params": {"Bucket": "bucket", "Key": "clips/one.mp4"},
            "ExpiresIn": 900,
        }
    ]


def test_soft_delete_moves_to_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_stubbed_s3()
    date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected_trash_key = f"_trash/{date_part}/clips/one.mp4"

    with Stubber(client) as stubber:
        stubber.add_response(
            "copy_object",
            {},
            {
                "Bucket": "bucket",
                "Key": expected_trash_key,
                "CopySource": {"Bucket": "bucket", "Key": "clips/one.mp4"},
                "MetadataDirective": "COPY",
            },
        )
        stubber.add_response(
            "delete_object",
            {},
            {
                "Bucket": "bucket",
                "Key": "clips/one.mp4",
            },
        )
        monkeypatch.setattr(s3_storage, "get_s3_client", lambda: client)

        trash_key = s3_storage.soft_delete_s3_object(
            "bucket",
            "clips/one.mp4",
            trash_prefix="_trash",
        )

    assert trash_key == expected_trash_key


def test_soft_delete_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_stubbed_s3()
    date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected_trash_key = f"_trash/{date_part}/clips/missing.mp4"

    with Stubber(client) as stubber:
        stubber.add_client_error(
            "copy_object",
            service_error_code="NoSuchKey",
            service_message="missing",
            http_status_code=404,
            expected_params={
                "Bucket": "bucket",
                "Key": expected_trash_key,
                "CopySource": {"Bucket": "bucket", "Key": "clips/missing.mp4"},
                "MetadataDirective": "COPY",
            },
        )
        monkeypatch.setattr(s3_storage, "get_s3_client", lambda: client)

        with pytest.raises(s3_storage.S3ObjectNotFoundError):
            s3_storage.soft_delete_s3_object(
                "bucket",
                "clips/missing.mp4",
                trash_prefix="_trash",
            )

