"""Asset browsing & tagging API for the footage library UI."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .taxonomy_parser import get_taxonomy

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_INDEX = _REPO_ROOT / "data" / "static_assets_index.json"
_OVERRIDES_PATH = _REPO_ROOT / "data" / "asset_tag_overrides.json"
_TAG_OVERRIDES_PATH = _REPO_ROOT / "data" / "tag_overrides.json"
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

_VIDEO_DB_PATHS = [
    _REPO_ROOT / "2nd_footage_selection_prompt" / "video_database (2).json",
    _REPO_ROOT / "2nd_footage_selection_prompt" / "video_database2.json",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_assets_cache: Optional[List[Dict[str, Any]]] = None
_index_meta_by_triplet_cache: Optional[Dict[tuple[str, str, str], Dict[str, Any]]] = None
_index_meta_by_file_name_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None


def _normalize_prefix(raw: str) -> str:
    return str(raw or "").strip().strip("/")


def _asset_ui_source_prefix() -> str:
    """
    React Asset UI must browse only one concrete top-level S3 folder.
    By default we pin this to the first-level `pinterest_collection`.

    Override via ASSET_UI_SOURCE_PREFIX if needed.
    """
    explicit = _normalize_prefix(os.getenv("ASSET_UI_SOURCE_PREFIX", ""))
    if explicit:
        return explicit

    s3_prefix = _normalize_prefix(os.getenv("S3_ASSET_PREFIX", ""))
    if s3_prefix:
        # Keep only first-level folder from active S3 prefix.
        return s3_prefix.split("/", 1)[0]
    return "pinterest_collection"


def _load_assets_index_metadata() -> tuple[Dict[tuple[str, str, str], Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    global _index_meta_by_triplet_cache
    global _index_meta_by_file_name_cache

    if _index_meta_by_triplet_cache is not None and _index_meta_by_file_name_cache is not None:
        return _index_meta_by_triplet_cache, _index_meta_by_file_name_cache

    idx_path = Path(os.getenv("STATIC_ASSETS_INDEX_JSON", str(_STATIC_INDEX)))
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    assets = data.get("assets", [])

    by_triplet: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    by_file_name: Dict[str, List[Dict[str, Any]]] = {}
    for raw in assets:
        if not isinstance(raw, dict):
            continue
        file_name = str(raw.get("file_name") or "").strip()
        genre = str(raw.get("genre") or "").strip()
        tag = str(raw.get("tag") or "").strip()
        if not file_name:
            continue

        if genre and tag:
            by_triplet[(genre.lower(), tag.lower(), file_name)] = raw
        by_file_name.setdefault(file_name, []).append(raw)

    _index_meta_by_triplet_cache = by_triplet
    _index_meta_by_file_name_cache = by_file_name
    return by_triplet, by_file_name


def _list_s3_video_keys(*, bucket: str, prefix: str) -> List[str]:
    from src.storage.s3 import list_s3_objects

    keys: List[str] = []
    continuation_token: Optional[str] = None
    normalized_prefix = _normalize_prefix(prefix)
    prefix_for_list = f"{normalized_prefix}/" if normalized_prefix else ""

    while True:
        page = list_s3_objects(
            bucket,
            prefix=prefix_for_list,
            continuation_token=continuation_token,
            max_keys=1000,
            delimiter="",
        )
        for obj in page.get("objects") or []:
            key = str(obj.get("key") or "").strip().lstrip("/")
            if not key or key.endswith("/"):
                continue
            if Path(key).suffix.lower() not in _VIDEO_EXTENSIONS:
                continue
            keys.append(key)

        continuation_token = page.get("next_continuation_token")
        if not page.get("is_truncated") or not continuation_token:
            break

    return keys


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _asset_from_s3_key(
    key: str,
    *,
    source_prefix: str,
    meta_by_triplet: Dict[tuple[str, str, str], Dict[str, Any]],
    meta_by_file_name: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    normalized_key = str(key).strip().lstrip("/")
    source_prefix_norm = _normalize_prefix(source_prefix)
    source_prefix_slash = f"{source_prefix_norm}/" if source_prefix_norm else ""

    rel = normalized_key
    if source_prefix_slash and normalized_key.startswith(source_prefix_slash):
        rel = normalized_key[len(source_prefix_slash):]
    parts = [p for p in rel.split("/") if p]

    file_name = parts[-1] if parts else Path(normalized_key).name
    genre = parts[-3] if len(parts) >= 3 else ""
    tag = parts[-2] if len(parts) >= 2 else ""

    meta = meta_by_triplet.get((genre.lower(), tag.lower(), file_name))
    if meta is None:
        candidates = meta_by_file_name.get(file_name) or []
        if len(candidates) == 1:
            meta = candidates[0]

    item: Dict[str, Any] = {
        "file_name": file_name,
        "genre": genre,
        "tag": tag,
        "src_w": _safe_int((meta or {}).get("src_w"), 0),
        "src_h": _safe_int((meta or {}).get("src_h"), 0),
        "duration_sec": _safe_float((meta or {}).get("duration_sec"), 0.0),
        "s3_key": normalized_key,
    }
    if meta and meta.get("dominant_color"):
        item["dominant_color"] = meta.get("dominant_color")
    if meta and isinstance(meta.get("palette_bins"), list):
        item["palette_bins"] = meta.get("palette_bins")
    return item


def _load_assets() -> List[Dict[str, Any]]:
    global _assets_cache
    if _assets_cache is not None:
        return _assets_cache

    bucket = str(os.getenv("S3_BUCKET_ASSET_STORAGE") or "").strip()
    if not bucket:
        idx_path = Path(os.getenv("STATIC_ASSETS_INDEX_JSON", str(_STATIC_INDEX)))
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        _assets_cache = data.get("assets", [])
        return _assets_cache

    source_prefix = _asset_ui_source_prefix()
    try:
        meta_by_triplet, meta_by_file_name = _load_assets_index_metadata()
        keys = _list_s3_video_keys(bucket=bucket, prefix=source_prefix)
    except Exception as e:  # pragma: no cover - surfaced via endpoint error
        raise RuntimeError(
            "Failed to load asset list from "
            f"s3://{bucket}/{source_prefix}"
        ) from e

    items = [
        _asset_from_s3_key(
            key,
            source_prefix=source_prefix,
            meta_by_triplet=meta_by_triplet,
            meta_by_file_name=meta_by_file_name,
        )
        for key in keys
    ]
    items.sort(key=lambda x: str(x.get("s3_key") or x.get("file_name") or ""))
    _assets_cache = items
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


_theme_tags_index: Optional[Dict[str, List[str]]] = None
_CLIP_ID_RE = re.compile(r"(\d{10,})")


def _normalize_tag(tag: str) -> str:
    return " ".join(tag.lower().strip().split())


def _load_theme_tags_index() -> Dict[str, List[str]]:
    """Build clip_id -> theme_tags lookup from video database files."""
    global _theme_tags_index
    if _theme_tags_index is not None:
        return _theme_tags_index

    index: Dict[str, List[str]] = {}
    for db_path in _VIDEO_DB_PATHS:
        if not db_path.exists():
            continue
        try:
            entries = json.loads(db_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in entries:
            vk = entry.get("video_key", "")
            m = _CLIP_ID_RE.search(vk)
            if not m:
                continue
            tags = entry.get("theme_tags", [])
            if tags:
                index[m.group(1)] = tags
    _theme_tags_index = index
    return index


def _get_theme_tags_for_asset(file_name: str) -> List[str]:
    idx = _load_theme_tags_index()
    m = _CLIP_ID_RE.search(file_name)
    if not m:
        return []
    return idx.get(m.group(1), [])


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


def _build_tag_statuses(theme_tags: List[str], tag_ov: Dict[str, Any]) -> Dict[str, Any]:
    blacklisted = {_normalize_tag(t) for t in tag_ov.get("blacklisted_tags", [])}
    assign_map: Dict[str, List[Dict[str, str]]] = {}
    for a in tag_ov.get("tag_assignments", []):
        key = _normalize_tag(a.get("tag", ""))
        if key:
            assign_map.setdefault(key, []).append({"theme": a["theme"], "group": a["group"]})
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


def _enrich_asset_tags(item: Dict[str, Any], tag_ov: Dict[str, Any]) -> Dict[str, Any]:
    """Add theme_tags and tag_statuses to an asset dict."""
    theme_tags = _get_theme_tags_for_asset(item.get("file_name", ""))
    item["theme_tags"] = theme_tags
    if theme_tags:
        item["tag_statuses"] = _build_tag_statuses(theme_tags, tag_ov)
    return item


def _s3_key_for_asset(asset: Dict[str, Any]) -> str:
    explicit_key = str(asset.get("s3_key") or "").strip().lstrip("/")
    if explicit_key:
        return explicit_key
    prefix = (os.getenv("S3_ASSET_PREFIX") or "pinterest_collection").strip("/")
    genre = asset.get("genre", "")
    tag = asset.get("tag", "")
    name = asset.get("file_name", "")
    return f"{prefix}/{genre}/{tag}/{name}"


def _override_key(*, file_name: str, s3_key: Optional[str]) -> str:
    clean_key = str(s3_key or "").strip().lstrip("/")
    if clean_key:
        return f"s3:{clean_key}"
    return file_name


def _find_asset(assets: List[Dict[str, Any]], *, file_name: str, s3_key: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_key = str(s3_key or "").strip().lstrip("/")
    if clean_key:
        for a in assets:
            if str(a.get("s3_key") or "").strip().lstrip("/") == clean_key:
                return a
    for a in assets:
        if a.get("file_name") == file_name:
            return a
    return None


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
# Bulk export / import helpers
# ---------------------------------------------------------------------------

_VIDEO_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


def _mime_for_ext(ext: str) -> Optional[str]:
    return _VIDEO_MIME_BY_EXT.get(ext.lower())


def _iter_zip_from_s3(bucket: str, assets: List[Dict[str, Any]]) -> Iterator[bytes]:
    """Stream a ZIP archive built from S3 objects without buffering the whole archive.

    Uses zipfile.ZipFile.open(..., 'w') for per-entry streaming writes, and drains
    the outer BytesIO buffer after every chunk so memory usage stays bounded by
    the S3 read chunk size (1 MiB).
    """
    from src.storage.s3 import get_s3_client

    s3 = get_s3_client()

    buffer = io.BytesIO()
    zf = zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED, allowZip64=True)

    def drain() -> bytes:
        buffer.seek(0)
        data = buffer.read()
        buffer.seek(0)
        buffer.truncate()
        return data

    seen_arcnames: set[str] = set()
    for item in assets:
        key = _s3_key_for_asset(item)
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
        except Exception as e:  # pragma: no cover - surfaced via log
            log.warning("Skipping %s in ZIP export: %s", key, e)
            continue
        body = obj.get("Body")
        if body is None:
            continue

        genre = str(item.get("genre") or "_").strip() or "_"
        tag = str(item.get("tag") or "_").strip() or "_"
        file_name = str(item.get("file_name") or Path(key).name)
        arcname = f"{genre}/{tag}/{file_name}"
        base = arcname
        dedup_n = 1
        while arcname in seen_arcnames:
            stem, dot, ext = base.rpartition(".")
            arcname = f"{stem}_{dedup_n}.{ext}" if dot else f"{base}_{dedup_n}"
            dedup_n += 1
        seen_arcnames.add(arcname)

        with zf.open(arcname, mode="w", force_zip64=True) as entry:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                entry.write(chunk)
                flushed = drain()
                if flushed:
                    yield flushed

        tail = drain()
        if tail:
            yield tail

    zf.close()
    final = drain()
    if final:
        yield final


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_asset_router(*, prefix: str = "/asset-ui/api") -> APIRouter:
    router = APIRouter(prefix=prefix)

    # --- taxonomy (must be before /{file_name} routes) ---
    @router.get("/assets/taxonomy")
    def get_taxonomy_endpoint() -> Dict[str, Any]:
        return {"themes": get_taxonomy()}

    # --- auto-tagging (server-side Groq Vision) ---
    _TAGGING_PROGRESS_KEY = "footage_tagging:progress"
    # If a run's progress hasn't been updated within this window we treat it as
    # dead (worker crashed) so a new run can start instead of being blocked
    # forever by a stale "running"/"queued" key.
    _TAGGING_STALE_S = 180.0

    def _tagging_redis():
        from .job_store import _redis_client_from_env

        return _redis_client_from_env()

    def _tagging_active(raw: Any) -> bool:
        if not raw:
            return False
        try:
            obj = json.loads(raw)
        except Exception:
            return False
        if obj.get("state") not in ("running", "queued"):
            return False
        try:
            age = time.time() - float(obj.get("updated_at") or 0.0)
        except Exception:
            return True
        return age < _TAGGING_STALE_S

    @router.post("/assets/tag-untagged")
    def tag_untagged(limit: int = Query(0, ge=0)) -> Dict[str, Any]:
        """Enqueue a batch that tags every untagged S3 clip via Groq.

        Single-flight: refuses if a run is already in progress (Redis state).
        Pass ?limit=N to cap how many clips this run processes.
        """
        try:
            r = _tagging_redis()
        except Exception as e:  # redis client construction failed
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")

        try:
            raw = r.get(_TAGGING_PROGRESS_KEY)
        except Exception:
            raw = None
        if _tagging_active(raw):
            raise HTTPException(status_code=409, detail="Tagging already running")

        # Enqueue onto the Celery broker. asset-ui is a slim image — surface a
        # clear 503 if celery isn't installed or the broker is unreachable,
        # instead of an opaque 500.
        try:
            from .celery_app import celery_app
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Task queue unavailable (celery import failed): {e}")

        # Bound the enqueue: with a missing/wrong CELERY_BROKER_URL, Celery would
        # otherwise block for a long time retrying the connection, hanging the
        # request. Use a short connect timeout and no publish retry so a broker
        # problem fails fast as a clear 503 instead of an unresponsive button.
        try:
            with celery_app.connection_for_write(connect_timeout=5) as conn:
                async_result = celery_app.send_task(
                    "orchestrator.tag_untagged_footage",
                    args=[int(limit)],
                    connection=conn,
                    retry=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Failed to enqueue tagging task (broker unreachable? check CELERY_BROKER_URL): {e}",
            )

        try:
            r.set(_TAGGING_PROGRESS_KEY, json.dumps({"state": "queued", "updated_at": time.time()}), ex=86400)
        except Exception:
            pass  # progress is best-effort; the task republishes on start
        return {"ok": True, "task_id": str(async_result.id), "limit": int(limit)}

    @router.get("/assets/tag-untagged/status")
    def tag_untagged_status() -> Dict[str, Any]:
        r = _tagging_redis()
        try:
            raw = r.get(_TAGGING_PROGRESS_KEY)
        except Exception:
            raw = None
        if not raw:
            return {"state": "idle"}
        try:
            return json.loads(raw)
        except Exception:
            return {"state": "unknown"}

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
        bl = {_normalize_tag(t) for t in data["blacklisted_tags"]}
        if tag not in bl:
            data["blacklisted_tags"].append(tag)
            _save_tag_overrides(data)
        return {"ok": True, "tag": tag}

    @router.delete("/tag-overrides/blacklist/{tag}")
    def unblacklist_tag(tag: str) -> Dict[str, Any]:
        norm = _normalize_tag(tag)
        data = _load_tag_overrides()
        data["blacklisted_tags"] = [t for t in data["blacklisted_tags"] if _normalize_tag(t) != norm]
        _save_tag_overrides(data)
        return {"ok": True, "tag": norm}

    @router.post("/tag-overrides/assign")
    def assign_tag(body: TagAssignRequest) -> Dict[str, Any]:
        tag = _normalize_tag(body.tag)
        if not tag:
            raise HTTPException(status_code=422, detail="Empty tag")
        taxonomy = get_taxonomy()
        theme_data = taxonomy.get(body.theme)
        if not theme_data:
            raise HTTPException(status_code=422, detail=f"Unknown theme: {body.theme}")
        if body.group not in theme_data.get("tags_groups", {}):
            raise HTTPException(status_code=422, detail=f"Unknown group: {body.group}")
        data = _load_tag_overrides()
        exists = any(
            _normalize_tag(a.get("tag", "")) == tag and a.get("theme") == body.theme and a.get("group") == body.group
            for a in data["tag_assignments"]
        )
        if not exists:
            data["tag_assignments"].append({"tag": tag, "theme": body.theme, "group": body.group})
            _save_tag_overrides(data)
        return {"ok": True, "tag": tag, "theme": body.theme, "group": body.group}

    @router.delete("/tag-overrides/assign")
    def unassign_tag(body: TagAssignRequest) -> Dict[str, Any]:
        tag = _normalize_tag(body.tag)
        data = _load_tag_overrides()
        data["tag_assignments"] = [
            a for a in data["tag_assignments"]
            if not (_normalize_tag(a.get("tag", "")) == tag and a.get("theme") == body.theme and a.get("group") == body.group)
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

        # Filter out excluded
        filtered = []
        for a in assets:
            ov = overrides.get(
                _override_key(file_name=str(a.get("file_name") or ""), s3_key=str(a.get("s3_key") or "") or None),
                {},
            ) or overrides.get(str(a.get("file_name") or ""), {})
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
            _enrich_asset_tags(item, tag_ov)
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

    # --- bulk export ---
    @router.get("/assets/export")
    def export_assets(
        genre: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        format: str = Query("manifest", pattern="^(manifest|zip)$"),
    ):
        """Export the currently visible asset set.

        - format=manifest (default): JSON with metadata + presigned URLs (1h TTL).
        - format=zip: streaming ZIP archive built directly from S3 (no ZIP cached on disk).
        """
        assets = _load_assets()
        overrides = _load_overrides()

        filtered: List[Dict[str, Any]] = []
        for a in assets:
            ov = overrides.get(
                _override_key(file_name=str(a.get("file_name") or ""), s3_key=str(a.get("s3_key") or "") or None),
                {},
            ) or overrides.get(str(a.get("file_name") or ""), {})
            if ov.get("excluded"):
                continue
            if genre and a.get("genre", "").lower() != genre.lower():
                continue
            if tag and a.get("tag", "").lower() != tag.lower():
                continue
            filtered.append(a)

        bucket = str(os.getenv("S3_BUCKET_ASSET_STORAGE") or "").strip()

        if format == "zip":
            if not bucket:
                raise HTTPException(status_code=503, detail="S3 not configured")
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            parts = ["assets"]
            if genre:
                parts.append(genre.strip().replace("/", "_"))
            if tag:
                parts.append(tag.strip().replace("/", "_"))
            parts.append(ts)
            zip_name = "_".join(parts) + ".zip"
            headers = {
                "Content-Disposition": f'attachment; filename="{zip_name}"',
                "X-Export-Count": str(len(filtered)),
            }
            return StreamingResponse(
                _iter_zip_from_s3(bucket, filtered),
                media_type="application/zip",
                headers=headers,
            )

        # format == "manifest"
        from src.storage.s3 import generate_presigned_url

        items: List[Dict[str, Any]] = []
        for a in filtered:
            entry: Dict[str, Any] = {
                "file_name": a.get("file_name"),
                "s3_key": a.get("s3_key"),
                "genre": a.get("genre"),
                "tag": a.get("tag"),
                "duration_sec": a.get("duration_sec"),
                "src_w": a.get("src_w"),
                "src_h": a.get("src_h"),
            }
            if bucket:
                try:
                    key = _s3_key_for_asset(a)
                    entry["download_url"] = generate_presigned_url(bucket, key, expires_in=3600)
                except Exception as e:  # pragma: no cover
                    log.warning("Failed to presign %s: %s", a.get("file_name"), e)
            items.append(entry)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(items),
            "filters": {"genre": genre, "tag": tag},
            "expires_in_sec": 3600 if bucket else None,
            "items": items,
        }

    # --- bulk import ---
    @router.post("/assets/import")
    async def import_assets(
        files: List[UploadFile] = File(...),
        genre: str = Query(...),
        tag: str = Query(...),
    ) -> Dict[str, Any]:
        """Upload one or more videos (or a ZIP containing videos) to the configured S3
        bucket under ``<prefix>/<genre>/<tag>/<file>``. Invalidates the asset cache
        so newly uploaded assets appear in the list on the next request.
        """
        global _assets_cache

        genre_clean = genre.strip()
        tag_clean = tag.strip()
        if not genre_clean or not tag_clean:
            raise HTTPException(status_code=422, detail="genre and tag are required")
        # Guard against path segments in user input
        if "/" in genre_clean or "\\" in genre_clean or "/" in tag_clean or "\\" in tag_clean:
            raise HTTPException(status_code=422, detail="genre/tag must not contain slashes")

        bucket = str(os.getenv("S3_BUCKET_ASSET_STORAGE") or "").strip()
        if not bucket:
            raise HTTPException(status_code=503, detail="S3 not configured")

        prefix = (os.getenv("S3_ASSET_PREFIX") or "pinterest_collection").strip("/")

        from src.storage.s3 import upload_file_to_s3

        uploaded: List[Dict[str, str]] = []
        errors: List[Dict[str, str]] = []

        def _upload_from_path(orig_name: str, src: Path, ext: str) -> None:
            safe_name = Path(orig_name).name
            if not safe_name:
                raise RuntimeError("empty file name")
            key = f"{prefix}/{genre_clean}/{tag_clean}/{safe_name}"
            upload_file_to_s3(bucket, key, src, content_type=_mime_for_ext(ext))
            uploaded.append({"file_name": safe_name, "s3_key": key})

        for f in files:
            name = f.filename or "unknown"
            ext = Path(name).suffix.lower()

            try:
                if ext == ".zip":
                    # Spool upload to disk, then iterate entries
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_zip:
                        shutil.copyfileobj(f.file, tmp_zip)
                        tmp_zip_path = Path(tmp_zip.name)
                    try:
                        with zipfile.ZipFile(tmp_zip_path, "r") as zf:
                            for zinfo in zf.infolist():
                                if zinfo.is_dir():
                                    continue
                                inner = zinfo.filename
                                inner_base = Path(inner).name
                                inner_ext = Path(inner).suffix.lower()
                                if not inner_base or inner_base.startswith("."):
                                    continue
                                if inner_ext not in _VIDEO_EXTENSIONS:
                                    continue
                                with zf.open(zinfo, "r") as src_fh:
                                    with tempfile.NamedTemporaryFile(delete=False, suffix=inner_ext) as tmp_vid:
                                        shutil.copyfileobj(src_fh, tmp_vid)
                                        tmp_vid_path = Path(tmp_vid.name)
                                try:
                                    _upload_from_path(inner_base, tmp_vid_path, inner_ext)
                                except Exception as e:
                                    errors.append({"file": f"{name}::{inner_base}", "error": str(e)})
                                finally:
                                    tmp_vid_path.unlink(missing_ok=True)
                    finally:
                        tmp_zip_path.unlink(missing_ok=True)
                elif ext in _VIDEO_EXTENSIONS:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                        shutil.copyfileobj(f.file, tmp)
                        tmp_path = Path(tmp.name)
                    try:
                        _upload_from_path(name, tmp_path, ext)
                    except Exception as e:
                        errors.append({"file": name, "error": str(e)})
                    finally:
                        tmp_path.unlink(missing_ok=True)
                else:
                    errors.append({"file": name, "error": f"unsupported extension {ext}"})
            except Exception as e:
                errors.append({"file": name, "error": str(e)})
            finally:
                await f.close()

        # Invalidate cache so new files show up in the next /assets call
        _assets_cache = None

        return {
            "uploaded": len(uploaded),
            "uploaded_files": uploaded,
            "errors": errors,
            "target_prefix": f"{prefix}/{genre_clean}/{tag_clean}/",
        }

    # --- single asset ---
    @router.get("/assets/{file_name}")
    def get_asset(file_name: str, s3_key: Optional[str] = Query(None)) -> Dict[str, Any]:
        assets = _load_assets()
        overrides = _load_overrides()
        tag_ov = _load_tag_overrides()
        asset = _find_asset(assets, file_name=file_name, s3_key=s3_key)
        if asset:
            item = {**asset}
            ov = overrides.get(
                _override_key(file_name=file_name, s3_key=str(asset.get("s3_key") or "") or s3_key),
                {},
            ) or overrides.get(file_name, {})
            if ov:
                item["overrides"] = ov
            _enrich_asset_tags(item, tag_ov)
            return item
        raise HTTPException(status_code=404, detail="Asset not found")

    # --- video presigned URL ---
    @router.get("/assets/{file_name}/video-url")
    def get_video_url(file_name: str, s3_key: Optional[str] = Query(None)) -> Dict[str, str]:
        assets = _load_assets()
        asset = _find_asset(assets, file_name=file_name, s3_key=s3_key)
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
    def update_tags(file_name: str, body: TagUpdateRequest, s3_key: Optional[str] = Query(None)) -> Dict[str, Any]:
        assets = _load_assets()
        asset = _find_asset(assets, file_name=file_name, s3_key=s3_key)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        overrides = _load_overrides()
        override_key = _override_key(file_name=file_name, s3_key=str(asset.get("s3_key") or "") or s3_key)
        entry = overrides.get(override_key, {})
        entry["theme_assignments"] = [ta.model_dump() for ta in body.theme_assignments]
        overrides[override_key] = entry
        _save_overrides(overrides)
        return {"ok": True, "file_name": file_name, "s3_key": asset.get("s3_key"), "overrides": entry}

    # --- soft delete ---
    @router.delete("/assets/{file_name}")
    def delete_asset(file_name: str, s3_key: Optional[str] = Query(None)) -> Dict[str, Any]:
        global _assets_cache

        assets = _load_assets()
        asset = _find_asset(assets, file_name=file_name, s3_key=s3_key)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        resolved_s3_key = str(asset.get("s3_key") or s3_key or "").strip().lstrip("/")
        trash_key: Optional[str] = None
        if resolved_s3_key:
            bucket = str(os.getenv("S3_BUCKET_ASSET_STORAGE") or "").strip()
            if not bucket:
                raise HTTPException(status_code=503, detail="S3 not configured")
            trash_prefix = str(os.getenv("ASSET_UI_TRASH_PREFIX") or "").strip().strip("/")
            if not trash_prefix:
                raise HTTPException(status_code=503, detail="ASSET_UI_TRASH_PREFIX is not configured")
            try:
                from src.storage.s3 import S3ObjectNotFoundError, soft_delete_s3_object

                trash_key = soft_delete_s3_object(
                    bucket,
                    resolved_s3_key,
                    trash_prefix=trash_prefix,
                )
            except S3ObjectNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                log.error("Failed to soft-delete s3://%s/%s: %s", bucket, resolved_s3_key, e)
                raise HTTPException(status_code=500, detail=f"Failed to soft-delete S3 object: {e}")

        overrides = _load_overrides()
        override_key = _override_key(file_name=file_name, s3_key=resolved_s3_key or s3_key)
        entry = overrides.get(override_key, {})
        entry["excluded"] = True
        if trash_key:
            entry["trash_key"] = trash_key
        overrides[override_key] = entry
        _save_overrides(overrides)
        _assets_cache = None
        return {
            "ok": True,
            "file_name": file_name,
            "s3_key": resolved_s3_key or None,
            "excluded": True,
            "trash_key": trash_key,
        }

    return router
