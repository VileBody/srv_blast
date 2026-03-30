"""Asset browsing & tagging API for the footage library UI."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .taxonomy_parser import get_taxonomy

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_INDEX = _REPO_ROOT / "data" / "static_assets_index.json"
_OVERRIDES_PATH = _REPO_ROOT / "data" / "asset_tag_overrides.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_assets_cache: Optional[List[Dict[str, Any]]] = None


def _load_assets() -> List[Dict[str, Any]]:
    global _assets_cache
    if _assets_cache is not None:
        return _assets_cache
    idx_path = Path(os.getenv("STATIC_ASSETS_INDEX_JSON", str(_STATIC_INDEX)))
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    _assets_cache = data.get("assets", [])
    return _assets_cache


def _load_overrides() -> Dict[str, Any]:
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_overrides(overrides: Dict[str, Any]) -> None:
    _OVERRIDES_PATH.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _s3_key_for_asset(asset: Dict[str, Any]) -> str:
    prefix = (os.getenv("S3_ASSET_PREFIX") or "pinterest_collection").strip("/")
    genre = asset.get("genre", "")
    tag = asset.get("tag", "")
    name = asset.get("file_name", "")
    return f"{prefix}/{genre}/{tag}/{name}"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ThemeAssignment(BaseModel):
    theme: str
    group: str
    tags: List[str] = Field(default_factory=list)
    excluded_tags: List[str] = Field(default_factory=list)


class TagUpdateRequest(BaseModel):
    theme_assignments: List[ThemeAssignment] = Field(default_factory=list)


class PaginatedAssets(BaseModel):
    total: int
    page: int
    per_page: int
    items: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_asset_router() -> APIRouter:
    router = APIRouter(prefix="/asset-ui/api")

    # --- taxonomy (must be before /{file_name} routes) ---
    @router.get("/assets/taxonomy")
    def get_taxonomy_endpoint() -> Dict[str, Any]:
        return {"themes": get_taxonomy()}

    # --- paginated list ---
    @router.get("/assets", response_model=PaginatedAssets)
    def list_assets(
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=500),
        genre: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
    ) -> PaginatedAssets:
        assets = _load_assets()
        overrides = _load_overrides()

        # Filter out excluded
        filtered = []
        for a in assets:
            ov = overrides.get(a["file_name"], {})
            if ov.get("excluded"):
                continue
            if genre and a.get("genre", "").lower() != genre.lower():
                continue
            if tag and a.get("tag", "").lower() != tag.lower():
                continue
            # Merge override info
            item = {**a}
            if ov:
                item["overrides"] = ov
            filtered.append(item)

        total = len(filtered)
        start = (page - 1) * per_page
        end = start + per_page
        return PaginatedAssets(
            total=total,
            page=page,
            per_page=per_page,
            items=filtered[start:end],
        )

    # --- single asset ---
    @router.get("/assets/{file_name}")
    def get_asset(file_name: str) -> Dict[str, Any]:
        assets = _load_assets()
        overrides = _load_overrides()
        for a in assets:
            if a["file_name"] == file_name:
                item = {**a}
                ov = overrides.get(file_name, {})
                if ov:
                    item["overrides"] = ov
                return item
        raise HTTPException(status_code=404, detail="Asset not found")

    # --- video presigned URL ---
    @router.get("/assets/{file_name}/video-url")
    def get_video_url(file_name: str) -> Dict[str, str]:
        assets = _load_assets()
        asset = None
        for a in assets:
            if a["file_name"] == file_name:
                asset = a
                break
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        bucket = os.getenv("S3_BUCKET_ASSET_STORAGE", "")
        if not bucket:
            raise HTTPException(status_code=503, detail="S3 not configured")

        try:
            from src.storage.s3 import generate_presigned_url
            key = _s3_key_for_asset(asset)
            url = generate_presigned_url(bucket, key, expires_in=3600)
            return {"url": url}
        except Exception as e:
            log.error("Failed to generate presigned URL for %s: %s", file_name, e)
            raise HTTPException(status_code=500, detail=str(e))

    # --- update tags ---
    @router.put("/assets/{file_name}/tags")
    def update_tags(file_name: str, body: TagUpdateRequest) -> Dict[str, Any]:
        # Verify asset exists
        assets = _load_assets()
        found = any(a["file_name"] == file_name for a in assets)
        if not found:
            raise HTTPException(status_code=404, detail="Asset not found")

        overrides = _load_overrides()
        entry = overrides.get(file_name, {})
        entry["theme_assignments"] = [ta.model_dump() for ta in body.theme_assignments]
        overrides[file_name] = entry
        _save_overrides(overrides)
        return {"ok": True, "file_name": file_name, "overrides": entry}

    # --- soft delete ---
    @router.delete("/assets/{file_name}")
    def delete_asset(file_name: str) -> Dict[str, Any]:
        assets = _load_assets()
        found = any(a["file_name"] == file_name for a in assets)
        if not found:
            raise HTTPException(status_code=404, detail="Asset not found")

        overrides = _load_overrides()
        entry = overrides.get(file_name, {})
        entry["excluded"] = True
        overrides[file_name] = entry
        _save_overrides(overrides)
        return {"ok": True, "file_name": file_name, "excluded": True}

    return router
