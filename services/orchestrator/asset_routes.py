"""Asset browsing & tagging API for the footage library UI."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .taxonomy_parser import get_taxonomy

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_INDEX = _REPO_ROOT / "data" / "static_assets_index.json"
_OVERRIDES_PATH = _REPO_ROOT / "data" / "asset_tag_overrides.json"
_TAG_OVERRIDES_PATH = _REPO_ROOT / "data" / "tag_overrides.json"

_VIDEO_DB_PATHS = [
    _REPO_ROOT / "2nd_footage_selection_prompt" / "video_database (2).json",
    _REPO_ROOT / "2nd_footage_selection_prompt" / "video_database2.json",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_assets_cache: Optional[List[Dict[str, Any]]] = None
_theme_tags_index: Optional[Dict[str, List[str]]] = None

_CLIP_ID_RE = re.compile(r"(\d{10,})")


def _normalize_tag(tag: str) -> str:
    return " ".join(tag.lower().strip().split())


def _load_assets() -> List[Dict[str, Any]]:
    global _assets_cache
    if _assets_cache is not None:
        return _assets_cache
    idx_path = Path(os.getenv("STATIC_ASSETS_INDEX_JSON", str(_STATIC_INDEX)))
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    _assets_cache = data.get("assets", [])
    return _assets_cache


def _load_theme_tags_index() -> Dict[str, List[str]]:
    """Build clip_id -> theme_tags lookup from video database files."""
    global _theme_tags_index
    if _theme_tags_index is not None:
        return _theme_tags_index

    index: Dict[str, List[str]] = {}
    for db_path in _VIDEO_DB_PATHS:
        if not db_path.exists():
            log.warning("Video DB not found: %s", db_path)
            continue
        try:
            entries = json.loads(db_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("Failed to load video DB %s: %s", db_path, e)
            continue
        for entry in entries:
            vk = entry.get("video_key", "")
            m = _CLIP_ID_RE.search(vk)
            if not m:
                continue
            clip_id = m.group(1)
            tags = entry.get("theme_tags", [])
            if tags:
                index[clip_id] = tags
    _theme_tags_index = index
    log.info("Loaded theme_tags for %d clips", len(index))
    return index


def _get_theme_tags_for_asset(file_name: str) -> List[str]:
    idx = _load_theme_tags_index()
    m = _CLIP_ID_RE.search(file_name)
    if not m:
        return []
    return idx.get(m.group(1), [])


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


def _load_tag_overrides() -> Dict[str, Any]:
    if not _TAG_OVERRIDES_PATH.exists():
        return {"blacklisted_tags": [], "tag_assignments": []}
    try:
        data = json.loads(_TAG_OVERRIDES_PATH.read_text(encoding="utf-8"))
        data.setdefault("blacklisted_tags", [])
        data.setdefault("tag_assignments", [])
        return data
    except Exception:
        return {"blacklisted_tags": [], "tag_assignments": []}


def _save_tag_overrides(data: Dict[str, Any]) -> None:
    _TAG_OVERRIDES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_tag_statuses(
    theme_tags: List[str],
    tag_ov: Dict[str, Any],
) -> Dict[str, Any]:
    """For each theme_tag, compute its override status."""
    blacklisted = {_normalize_tag(t) for t in tag_ov.get("blacklisted_tags", [])}
    assignments = tag_ov.get("tag_assignments", [])

    # Build tag -> list of assignments lookup
    assign_map: Dict[str, List[Dict[str, str]]] = {}
    for a in assignments:
        key = _normalize_tag(a.get("tag", ""))
        if key:
            assign_map.setdefault(key, []).append(
                {"theme": a["theme"], "group": a["group"]}
            )

    statuses: Dict[str, Any] = {}
    for tag in theme_tags:
        norm = _normalize_tag(tag)
        status: Dict[str, Any] = {}
        if norm in blacklisted:
            status["blacklisted"] = True
        if norm in assign_map:
            status["assigned_to"] = assign_map[norm]
        if status:
            statuses[tag] = status
    return statuses


def _enrich_asset(asset: Dict[str, Any], overrides: Dict[str, Any], tag_ov: Dict[str, Any]) -> Dict[str, Any]:
    """Add theme_tags, tag_statuses, and overrides to an asset dict."""
    item = {**asset}
    ov = overrides.get(asset["file_name"], {})
    if ov:
        item["overrides"] = ov
    theme_tags = _get_theme_tags_for_asset(asset["file_name"])
    item["theme_tags"] = theme_tags
    if theme_tags:
        item["tag_statuses"] = _build_tag_statuses(theme_tags, tag_ov)
    return item


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


class TagBlacklistRequest(BaseModel):
    tag: str


class TagAssignRequest(BaseModel):
    tag: str
    theme: str
    group: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_asset_router() -> APIRouter:
    router = APIRouter(prefix="/asset-ui/api")

    # --- taxonomy (must be before /{file_name} routes) ---
    @router.get("/assets/taxonomy")
    def get_taxonomy_endpoint() -> Dict[str, Any]:
        return {"themes": get_taxonomy()}

    # --- tag overrides ---
    @router.get("/tag-overrides")
    def get_tag_overrides() -> Dict[str, Any]:
        return _load_tag_overrides()

    @router.post("/tag-overrides/blacklist")
    def blacklist_tag(body: TagBlacklistRequest) -> Dict[str, Any]:
        tag = _normalize_tag(body.tag)
        if not tag:
            raise HTTPException(status_code=422, detail="Empty tag")
        data = _load_tag_overrides()
        bl = [_normalize_tag(t) for t in data["blacklisted_tags"]]
        if tag not in bl:
            data["blacklisted_tags"].append(tag)
            _save_tag_overrides(data)
        return {"ok": True, "tag": tag}

    @router.delete("/tag-overrides/blacklist/{tag}")
    def unblacklist_tag(tag: str) -> Dict[str, Any]:
        norm = _normalize_tag(tag)
        data = _load_tag_overrides()
        data["blacklisted_tags"] = [
            t for t in data["blacklisted_tags"]
            if _normalize_tag(t) != norm
        ]
        _save_tag_overrides(data)
        return {"ok": True, "tag": norm}

    @router.post("/tag-overrides/assign")
    def assign_tag(body: TagAssignRequest) -> Dict[str, Any]:
        tag = _normalize_tag(body.tag)
        if not tag:
            raise HTTPException(status_code=422, detail="Empty tag")
        # Validate theme/group
        taxonomy = get_taxonomy()
        theme_data = taxonomy.get(body.theme)
        if not theme_data:
            raise HTTPException(status_code=422, detail=f"Unknown theme: {body.theme}")
        if body.group not in theme_data.get("tags_groups", {}):
            raise HTTPException(status_code=422, detail=f"Unknown group: {body.group}")

        data = _load_tag_overrides()
        # Check if already assigned to this exact theme/group
        exists = any(
            _normalize_tag(a.get("tag", "")) == tag
            and a.get("theme") == body.theme
            and a.get("group") == body.group
            for a in data["tag_assignments"]
        )
        if not exists:
            data["tag_assignments"].append({
                "tag": tag,
                "theme": body.theme,
                "group": body.group,
            })
            _save_tag_overrides(data)
        return {"ok": True, "tag": tag, "theme": body.theme, "group": body.group}

    @router.delete("/tag-overrides/assign")
    def unassign_tag(body: TagAssignRequest) -> Dict[str, Any]:
        tag = _normalize_tag(body.tag)
        data = _load_tag_overrides()
        data["tag_assignments"] = [
            a for a in data["tag_assignments"]
            if not (
                _normalize_tag(a.get("tag", "")) == tag
                and a.get("theme") == body.theme
                and a.get("group") == body.group
            )
        ]
        _save_tag_overrides(data)
        return {"ok": True, "tag": tag}

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
        tag_ov = _load_tag_overrides()

        filtered = []
        for a in assets:
            ov = overrides.get(a["file_name"], {})
            if ov.get("excluded"):
                continue
            if genre and a.get("genre", "").lower() != genre.lower():
                continue
            if tag and a.get("tag", "").lower() != tag.lower():
                continue
            filtered.append(_enrich_asset(a, overrides, tag_ov))

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
        tag_ov = _load_tag_overrides()
        for a in assets:
            if a["file_name"] == file_name:
                return _enrich_asset(a, overrides, tag_ov)
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
