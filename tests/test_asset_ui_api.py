from __future__ import annotations

from pathlib import Path

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
