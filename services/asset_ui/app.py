from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.storage import s3 as s3_storage

from .config import AssetUISettings

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Jinja2Templates(directory=str(REPO_ROOT / "templates"))
UPLOAD_CHUNK_BYTES = 1024 * 1024

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}


class ObjectEntry(BaseModel):
    key: str
    size: int
    last_modified: str | None = None
    etag: str | None = None
    content_type_guess: str | None = None


class ObjectsResponse(BaseModel):
    bucket: str
    prefix: str
    parent_prefix: str
    include_trash: bool
    prefixes: list[str] = Field(default_factory=list)
    objects: list[ObjectEntry] = Field(default_factory=list)
    next_cursor: str | None = None


class PreviewResponse(BaseModel):
    key: str
    url: str
    kind: str
    content_type: str | None = None
    expires_in: int


class UploadResponse(BaseModel):
    key: str
    size: int
    content_type: str


class DeleteRequest(BaseModel):
    key: str


class DeleteResponse(BaseModel):
    key: str
    trash_key: str


def _normalize_prefix(prefix: str) -> str:
    clean = str(prefix or "").strip().lstrip("/")
    if clean and not clean.endswith("/"):
        clean += "/"
    return clean


def _parent_prefix(prefix: str) -> str:
    clean = _normalize_prefix(prefix).rstrip("/")
    if not clean:
        return ""
    parts = [p for p in clean.split("/") if p]
    if len(parts) <= 1:
        return ""
    return "/".join(parts[:-1]) + "/"


def _join_prefix(prefix: str, file_name: str) -> str:
    name = Path(str(file_name or "")).name.strip()
    if not name:
        raise RuntimeError("file_name is empty")

    clean_prefix = _normalize_prefix(prefix)
    if not clean_prefix:
        return name
    return f"{clean_prefix}{name}"


def _is_under_prefix(key: str, prefix: str) -> bool:
    clean_key = str(key or "").strip().lstrip("/")
    clean_prefix = str(prefix or "").strip().strip("/")
    if not clean_prefix:
        return False
    return clean_key == clean_prefix or clean_key.startswith(f"{clean_prefix}/")


def _media_kind(*, key: str, content_type: str | None) -> str | None:
    ctype = str(content_type or "").lower().strip()
    if ctype.startswith("image/"):
        return "image"
    if ctype.startswith("video/"):
        return "video"
    if ctype.startswith("audio/"):
        return "audio"

    suffix = Path(key).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return None


def _build_objects_payload(
    *,
    settings: AssetUISettings,
    prefix: str,
    cursor: str | None,
    limit: int,
    include_trash: bool,
) -> ObjectsResponse:
    normalized_prefix = _normalize_prefix(prefix)
    if _is_under_prefix(normalized_prefix, settings.trash_prefix) and not include_trash:
        raise HTTPException(status_code=400, detail="trash_hidden_set_include_trash=1")

    raw = s3_storage.list_s3_objects(
        settings.s3_bucket_assets,
        prefix=normalized_prefix,
        continuation_token=(cursor or "").strip() or None,
        max_keys=limit,
        delimiter="/",
    )

    prefixes = [p for p in (raw.get("prefixes") or []) if isinstance(p, str)]
    objects = []
    for item in raw.get("objects") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if not key:
            continue
        if not include_trash and _is_under_prefix(key, settings.trash_prefix):
            continue
        ctype_guess, _ = mimetypes.guess_type(key)
        objects.append(
            ObjectEntry(
                key=key,
                size=int(item.get("size") or 0),
                last_modified=item.get("last_modified"),
                etag=item.get("etag"),
                content_type_guess=ctype_guess or None,
            )
        )

    if not include_trash:
        prefixes = [p for p in prefixes if not _is_under_prefix(p, settings.trash_prefix)]

    return ObjectsResponse(
        bucket=settings.s3_bucket_assets,
        prefix=normalized_prefix,
        parent_prefix=_parent_prefix(normalized_prefix),
        include_trash=include_trash,
        prefixes=prefixes,
        objects=objects,
        next_cursor=raw.get("next_continuation_token"),
    )


def create_app(settings: AssetUISettings) -> FastAPI:
    app = FastAPI(title="S3 Asset UI", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        prefix: str = Query(default=""),
        cursor: str = Query(default=""),
        limit: int = Query(default=100, ge=1, le=1000),
        include_trash: bool = Query(default=False),
    ) -> HTMLResponse:
        payload = _build_objects_payload(
            settings=settings,
            prefix=prefix,
            cursor=cursor or None,
            limit=limit,
            include_trash=include_trash,
        )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="asset_ui/index.html",
            context={
                "title": "S3 Asset UI",
                "initial_payload": payload.model_dump(mode="json"),
                "api_base_path": request.url.path.rstrip("/"),
                "default_limit": limit,
                "trash_prefix": settings.trash_prefix,
                "presign_ttl_s": settings.presign_ttl_s,
                "upload_max_mb": settings.upload_max_mb,
            },
        )

    @app.get("/api/objects", response_model=ObjectsResponse)
    def api_objects(
        prefix: str = Query(default=""),
        cursor: str = Query(default=""),
        limit: int = Query(default=100, ge=1, le=1000),
        include_trash: bool = Query(default=False),
    ) -> ObjectsResponse:
        return _build_objects_payload(
            settings=settings,
            prefix=prefix,
            cursor=cursor or None,
            limit=limit,
            include_trash=include_trash,
        )

    @app.get("/api/preview-url", response_model=PreviewResponse)
    def api_preview_url(
        key: str = Query(..., min_length=1),
        include_trash: bool = Query(default=False),
    ) -> PreviewResponse:
        clean_key = str(key).strip().lstrip("/")
        if not clean_key:
            raise HTTPException(status_code=400, detail="key_is_required")
        if _is_under_prefix(clean_key, settings.trash_prefix) and not include_trash:
            raise HTTPException(status_code=400, detail="trash_hidden_set_include_trash=1")

        try:
            head = s3_storage.head_s3_object(settings.s3_bucket_assets, clean_key)
        except s3_storage.S3ObjectNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        content_type = head.get("content_type")
        kind = _media_kind(key=clean_key, content_type=content_type)
        if not kind:
            raise HTTPException(status_code=400, detail="preview_not_supported_for_this_file_type")

        url = s3_storage.generate_presigned_url(
            settings.s3_bucket_assets,
            clean_key,
            expires_in=settings.presign_ttl_s,
        )
        return PreviewResponse(
            key=clean_key,
            url=url,
            kind=kind,
            content_type=content_type,
            expires_in=settings.presign_ttl_s,
        )

    @app.post("/api/upload", response_model=UploadResponse)
    async def api_upload(
        prefix: str = Form(default=""),
        file: UploadFile = File(...),
    ) -> UploadResponse:
        if not file.filename:
            raise HTTPException(status_code=400, detail="file_name_is_required")

        target_key = _join_prefix(prefix, file.filename)
        if _is_under_prefix(target_key, settings.trash_prefix):
            raise HTTPException(status_code=400, detail="uploads_to_trash_prefix_are_not_allowed")

        max_bytes = settings.upload_max_mb * 1024 * 1024
        total = 0
        temp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp:
                temp_path = Path(temp.name)
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"file_too_large_limit_mb={settings.upload_max_mb}",
                        )
                    temp.write(chunk)
                temp.flush()

            guessed_type, _ = mimetypes.guess_type(target_key)
            content_type = str(file.content_type or guessed_type or "application/octet-stream")
            s3_storage.upload_file_to_s3(
                settings.s3_bucket_assets,
                target_key,
                temp_path,
                content_type=content_type,
            )
            return UploadResponse(key=target_key, size=total, content_type=content_type)
        finally:
            await file.close()
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @app.post("/api/delete", response_model=DeleteResponse)
    def api_delete(payload: DeleteRequest) -> DeleteResponse:
        clean_key = str(payload.key or "").strip().lstrip("/")
        if not clean_key:
            raise HTTPException(status_code=400, detail="key_is_required")
        if _is_under_prefix(clean_key, settings.trash_prefix):
            raise HTTPException(status_code=400, detail="object_already_in_trash")

        try:
            trash_key = s3_storage.soft_delete_s3_object(
                settings.s3_bucket_assets,
                clean_key,
                trash_prefix=settings.trash_prefix,
            )
        except s3_storage.S3ObjectNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        return DeleteResponse(key=clean_key, trash_key=trash_key)

    return app
