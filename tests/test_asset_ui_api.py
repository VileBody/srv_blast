from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from services.asset_ui import app as asset_ui_app
from services.asset_ui.config import AssetUISettings
from src.storage.s3 import S3ObjectNotFoundError


def _make_app(*, upload_max_mb: int = 512):
    settings = AssetUISettings(
        s3_bucket_assets="asset-bucket",
        port=8100,
        upload_max_mb=upload_max_mb,
        trash_prefix="_trash",
        presign_ttl_s=900,
    )
    return asset_ui_app.create_app(settings)


def test_api_objects_filters_trash_by_default(monkeypatch) -> None:
    def _fake_list(_bucket, *, prefix, continuation_token, max_keys, delimiter):
        assert prefix == ""
        assert continuation_token is None
        assert max_keys == 100
        assert delimiter == "/"
        return {
            "prefixes": ["folder/", "_trash/"],
            "objects": [
                {"key": "folder/clip.mp4", "size": 10, "last_modified": None, "etag": "a"},
                {"key": "_trash/2026-03-25/deleted.mp4", "size": 10, "last_modified": None, "etag": "b"},
            ],
            "next_continuation_token": None,
            "is_truncated": False,
        }

    monkeypatch.setattr(asset_ui_app.s3_storage, "list_s3_objects", _fake_list)

    client = TestClient(_make_app())
    res = client.get("/api/objects")
    assert res.status_code == 200
    data = res.json()
    assert data["prefixes"] == ["folder/"]
    assert [o["key"] for o in data["objects"]] == ["folder/clip.mp4"]

    res2 = client.get("/api/objects", params={"include_trash": 1})
    assert res2.status_code == 200
    data2 = res2.json()
    assert "_trash/" in data2["prefixes"]
    assert len(data2["objects"]) == 2


def test_root_renders(monkeypatch) -> None:
    monkeypatch.setattr(
        asset_ui_app.s3_storage,
        "list_s3_objects",
        lambda *_args, **_kwargs: {
            "prefixes": [],
            "objects": [],
            "next_continuation_token": None,
            "is_truncated": False,
        },
    )
    client = TestClient(_make_app())
    res = client.get("/")
    assert res.status_code == 200
    assert "S3 Asset UI" in res.text


def test_api_upload_and_size_limit(monkeypatch) -> None:
    captured = {}

    def _fake_upload(_bucket, key, path, content_type=None):
        captured["key"] = key
        captured["size"] = Path(path).stat().st_size
        captured["content_type"] = content_type

    monkeypatch.setattr(asset_ui_app.s3_storage, "upload_file_to_s3", _fake_upload)

    client = TestClient(_make_app(upload_max_mb=1))
    small = client.post(
        "/api/upload",
        data={"prefix": "clips"},
        files={"file": ("one.mp4", b"abc", "video/mp4")},
    )
    assert small.status_code == 200
    payload = small.json()
    assert payload["key"] == "clips/one.mp4"
    assert captured["key"] == "clips/one.mp4"
    assert captured["size"] == 3
    assert captured["content_type"] == "video/mp4"

    too_big = client.post(
        "/api/upload",
        data={"prefix": "clips"},
        files={"file": ("big.mp4", b"x" * (1024 * 1024 + 1), "video/mp4")},
    )
    assert too_big.status_code == 413
    assert "file_too_large_limit_mb=1" in too_big.json()["detail"]


def test_api_delete_soft_moves_to_trash(monkeypatch) -> None:
    monkeypatch.setattr(
        asset_ui_app.s3_storage,
        "soft_delete_s3_object",
        lambda _bucket, key, *, trash_prefix: f"{trash_prefix}/2026-03-25/{key}",
    )

    client = TestClient(_make_app())
    res = client.post("/api/delete", json={"key": "folder/clip.mp4"})
    assert res.status_code == 200
    data = res.json()
    assert data["key"] == "folder/clip.mp4"
    assert data["trash_key"] == "_trash/2026-03-25/folder/clip.mp4"


def test_api_preview_media_and_unsupported(monkeypatch) -> None:
    def _fake_head(_bucket, key):
        if key.endswith(".jpg"):
            return {"content_type": "image/jpeg"}
        if key.endswith(".mp4"):
            return {"content_type": "video/mp4"}
        if key.endswith(".mp3"):
            return {"content_type": "audio/mpeg"}
        if key.endswith(".missing"):
            raise S3ObjectNotFoundError("missing")
        return {"content_type": "text/plain"}

    monkeypatch.setattr(asset_ui_app.s3_storage, "head_s3_object", _fake_head)
    monkeypatch.setattr(
        asset_ui_app.s3_storage,
        "generate_presigned_url",
        lambda _bucket, key, expires_in: f"https://example.local/{key}?exp={expires_in}",
    )

    client = TestClient(_make_app())

    img = client.get("/api/preview-url", params={"key": "a.jpg"})
    assert img.status_code == 200
    assert img.json()["kind"] == "image"

    vid = client.get("/api/preview-url", params={"key": "b.mp4"})
    assert vid.status_code == 200
    assert vid.json()["kind"] == "video"

    aud = client.get("/api/preview-url", params={"key": "c.mp3"})
    assert aud.status_code == 200
    assert aud.json()["kind"] == "audio"

    bad = client.get("/api/preview-url", params={"key": "d.txt"})
    assert bad.status_code == 400
    assert "preview_not_supported_for_this_file_type" in bad.json()["detail"]

    missing = client.get("/api/preview-url", params={"key": "x.missing"})
    assert missing.status_code == 404

