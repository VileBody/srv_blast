from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.asset_ui import app as asset_ui_app
from services.asset_ui.config import AssetUISettings
from services.orchestrator import asset_routes


def _write_dist(tmp_path: Path, marker: str = "React Asset UI") -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        f"<!doctype html><html><body><div id='root'>{marker}</div></body></html>",
        encoding="utf-8",
    )
    return dist


def _make_app(tmp_path: Path, monkeypatch) -> TestClient:
    dist = _write_dist(tmp_path)
    monkeypatch.setenv("ASSET_UI_DIST_DIR", str(dist))
    settings = AssetUISettings(
        s3_bucket_assets="asset-bucket",
        port=8100,
        upload_max_mb=512,
        trash_prefix="_trash",
        presign_ttl_s=900,
    )
    app = asset_ui_app.create_app(settings)
    return TestClient(app)


def test_root_serves_react_dist(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, monkeypatch)
    res = client.get("/")
    assert res.status_code == 200
    assert "React Asset UI" in res.text


def test_tag_untagged_uses_slim_celery_producer(tmp_path: Path, monkeypatch) -> None:
    sent: list[dict[str, object]] = []

    class _Inspect:
        def active_queues(self):
            return {"worker@node": [{"name": "build.node-0"}]}

    class _Control:
        def inspect(self, timeout: int):
            assert timeout == 2
            return _Inspect()

    class _Producer:
        def __init__(self, name: str, *, broker: str | None, backend: None):
            assert name == "asset_ui_producer"
            assert broker == "redis://queue.example/0"
            assert backend is None
            self.conf = SimpleNamespace(broker_url=broker)
            self.control = _Control()

        def send_task(self, task_name: str, **kwargs):
            sent.append({"task_name": task_name, **kwargs})
            return SimpleNamespace(id="tag-task-1")

    import celery

    monkeypatch.setattr(celery, "Celery", _Producer)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://queue.example/0")
    monkeypatch.setitem(sys.modules, "services.orchestrator.celery_app", None)

    client = _make_app(tmp_path, monkeypatch)
    res = client.post("/api/assets/tag-untagged", params={"limit": 25, "media_type": "photo"})

    assert res.status_code == 200
    assert res.json()["task_id"] == "tag-task-1"
    assert res.json()["queue"] == "build.node-0"
    assert sent == [
        {
            "task_name": "orchestrator.tag_untagged_footage",
            "args": [25, "photo"],
            "queue": "build.node-0",
            "ignore_result": True,
            "retry": False,
        }
    ]


def test_assets_list_filters_excluded_and_supports_filtering(tmp_path: Path, monkeypatch) -> None:
    assets = [
        {"file_name": "a.mp4", "genre": "pop", "tag": "night", "duration_sec": 3.0},
        {"file_name": "b.mp4", "genre": "rock", "tag": "day", "duration_sec": 4.0},
    ]
    overrides = {
        "a.mp4": {"theme_assignments": [{"theme": "mood", "group": "vibe", "tags": ["neon"], "excluded_tags": []}]},
        "b.mp4": {"excluded": True},
    }
    monkeypatch.setattr(asset_routes, "_assets_cache", None)
    monkeypatch.setattr(asset_routes, "_load_assets", lambda: assets)
    monkeypatch.setattr(asset_routes, "_load_overrides", lambda: overrides)

    client = _make_app(tmp_path, monkeypatch)

    res = client.get("/api/assets", params={"page": 1, "per_page": 50})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["file_name"] == "a.mp4"
    assert "overrides" in data["items"][0]

    res2 = client.get("/api/assets", params={"genre": "pop"})
    assert res2.status_code == 200
    assert res2.json()["total"] == 1

    res3 = client.get("/api/assets", params={"genre": "rock"})
    assert res3.status_code == 200
    assert res3.json()["total"] == 0


def test_taxonomy_and_video_url_endpoints(tmp_path: Path, monkeypatch) -> None:
    assets = [{"file_name": "clip.mp4", "genre": "pop", "tag": "night"}]
    monkeypatch.setattr(asset_routes, "_assets_cache", None)
    monkeypatch.setattr(asset_routes, "_load_assets", lambda: assets)
    monkeypatch.setattr(asset_routes, "_load_overrides", lambda: {})
    monkeypatch.setattr(
        asset_routes,
        "get_taxonomy",
        lambda: {"mood": {"color": ["#fff"], "exclude": [], "tags_groups": {"vibe": {"_tags": ["neon"]}}}},
    )

    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "asset-bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection")

    from src.storage import s3 as s3_storage

    monkeypatch.setattr(
        s3_storage,
        "generate_presigned_url",
        lambda bucket, key, expires_in: f"https://example.local/{bucket}/{key}?exp={expires_in}",
    )

    client = _make_app(tmp_path, monkeypatch)

    tax = client.get("/api/assets/taxonomy")
    assert tax.status_code == 200
    assert "mood" in tax.json()["themes"]

    url = client.get("/api/assets/clip.mp4/video-url")
    assert url.status_code == 200
    assert "clip.mp4" in url.json()["url"]


def test_update_tags_and_delete_persist_overrides(tmp_path: Path, monkeypatch) -> None:
    assets = [{"file_name": "clip.mp4", "genre": "pop", "tag": "night"}]
    stored_overrides: dict[str, object] = {}

    monkeypatch.setattr(asset_routes, "_assets_cache", None)
    monkeypatch.setattr(asset_routes, "_load_assets", lambda: assets)
    monkeypatch.setattr(asset_routes, "_load_overrides", lambda: dict(stored_overrides))

    def _save(updated: dict[str, object]) -> None:
        stored_overrides.clear()
        stored_overrides.update(updated)

    monkeypatch.setattr(asset_routes, "_save_overrides", _save)

    client = _make_app(tmp_path, monkeypatch)

    put = client.put(
        "/api/assets/clip.mp4/tags",
        json={
            "theme_assignments": [
                {
                    "theme": "mood",
                    "group": "vibe",
                    "tags": ["neon"],
                    "excluded_tags": ["dark"],
                }
            ]
        },
    )
    assert put.status_code == 200
    assert stored_overrides["clip.mp4"]["theme_assignments"][0]["theme"] == "mood"  # type: ignore[index]

    delete = client.delete("/api/assets/clip.mp4")
    assert delete.status_code == 200
    assert stored_overrides["clip.mp4"]["excluded"] is True  # type: ignore[index]


def test_assets_list_reads_only_first_level_pinterest_collection_prefix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from src.storage import s3 as s3_storage

    monkeypatch.setattr(asset_routes, "_assets_cache", None)
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "asset-bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/pins2_1to1_20260323")

    calls: list[dict[str, object]] = []

    def _fake_list(
        bucket: str,
        *,
        prefix: str = "",
        continuation_token: str | None = None,
        max_keys: int = 200,
        delimiter: str = "/",
    ) -> dict[str, object]:
        calls.append(
            {
                "bucket": bucket,
                "prefix": prefix,
                "continuation_token": continuation_token,
                "max_keys": max_keys,
                "delimiter": delimiter,
            }
        )
        return {
            "objects": [
                {"key": "pinterest_collection/Rock/dark/clip-a.mp4"},
                {"key": "pinterest_collection/Hip-Hop/night/clip-b.mp4"},
                {"key": "pinterest_collection/Hip-Hop/night/readme.txt"},
            ],
            "prefixes": [],
            "next_continuation_token": None,
            "is_truncated": False,
        }

    monkeypatch.setattr(s3_storage, "list_s3_objects", _fake_list)

    client = _make_app(tmp_path, monkeypatch)
    res = client.get("/api/assets", params={"page": 1, "per_page": 50})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["items"][0]["s3_key"].startswith("pinterest_collection/")
    assert data["items"][1]["s3_key"].startswith("pinterest_collection/")
    assert calls[0]["prefix"] == "pinterest_collection/"
    assert calls[0]["delimiter"] == ""


def test_delete_asset_soft_deletes_s3_object_when_s3_key_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assets = [
        {
            "file_name": "clip.mp4",
            "genre": "rock",
            "tag": "night",
            "s3_key": "pinterest_collection/rock/night/clip.mp4",
        }
    ]
    stored_overrides: dict[str, object] = {}

    monkeypatch.setattr(asset_routes, "_assets_cache", None)
    monkeypatch.setattr(asset_routes, "_load_assets", lambda: assets)
    monkeypatch.setattr(asset_routes, "_load_overrides", lambda: dict(stored_overrides))
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "asset-bucket")
    monkeypatch.setenv("ASSET_UI_TRASH_PREFIX", "_trash")

    def _save(updated: dict[str, object]) -> None:
        stored_overrides.clear()
        stored_overrides.update(updated)

    monkeypatch.setattr(asset_routes, "_save_overrides", _save)

    from src.storage import s3 as s3_storage

    soft_delete_calls: list[dict[str, str]] = []

    def _fake_soft_delete(bucket: str, key: str, *, trash_prefix: str) -> str:
        soft_delete_calls.append({"bucket": bucket, "key": key, "trash_prefix": trash_prefix})
        return "_trash/2026-03-30/pinterest_collection/rock/night/clip.mp4"

    monkeypatch.setattr(s3_storage, "soft_delete_s3_object", _fake_soft_delete)

    client = _make_app(tmp_path, monkeypatch)
    delete = client.delete(
        "/api/assets/clip.mp4",
        params={"s3_key": "pinterest_collection/rock/night/clip.mp4"},
    )
    assert delete.status_code == 200
    assert soft_delete_calls == [
        {
            "bucket": "asset-bucket",
            "key": "pinterest_collection/rock/night/clip.mp4",
            "trash_prefix": "_trash",
        }
    ]
    assert stored_overrides["s3:pinterest_collection/rock/night/clip.mp4"]["excluded"] is True  # type: ignore[index]
    assert (
        stored_overrides["s3:pinterest_collection/rock/night/clip.mp4"]["trash_key"]  # type: ignore[index]
        == "_trash/2026-03-30/pinterest_collection/rock/night/clip.mp4"
    )


def test_create_app_fails_without_dist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ASSET_UI_DIST_DIR", str(tmp_path / "missing_dist"))
    settings = AssetUISettings(
        s3_bucket_assets="asset-bucket",
        port=8100,
        upload_max_mb=512,
        trash_prefix="_trash",
        presign_ttl_s=900,
    )
    try:
        asset_ui_app.create_app(settings)
    except RuntimeError as exc:
        assert "frontend build not found" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when dist is missing")
