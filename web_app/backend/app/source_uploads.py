"""Shared upload path for desktop, phone and warm-up; ownership stays server-side."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4
from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from . import security, upload_store
from .media_uploads import normalize, MAX_SOURCE_BYTES
from .runtime import SETTINGS

logger = logging.getLogger(__name__)


async def upload(file: UploadFile, *, user_id: str, project_id: str, kind: str, format: str | None, local_dir: Path, backend: Any = None) -> dict[str, Any]:
    try:
        upload_store.reserve(user_id)
    except ValueError as exc:
        raise HTTPException(413, detail=str(exc)) from exc
    committed = False
    stored_url: str | None = None
    local_path: Path | None = None
    try:
        content = await file.read(MAX_SOURCE_BYTES + 1)
        is_video = kind != 'warmup-audio'
        content, metadata = await run_in_threadpool(normalize, content, video=is_video, expected_format=format)
        suffix = '.mp4' if is_video else '.wav'
        name = Path(security.sanitize_filename(file.filename, 'source' + suffix)).stem + suffix
        if SETTINGS.backend == 'production':
            put = backend.upload_source if is_video else backend.upload_hook_sound
            result = await run_in_threadpool(put, content=content, user_id=user_id, filename=name,
                                             content_type='video/mp4' if is_video else 'audio/wav')
            url, playback = result['s3_url'], result['playback_url']
            stored_url = url
        else:
            local_dir.mkdir(parents=True, exist_ok=True)
            path = local_dir / (uuid4().hex + suffix)
            path.write_bytes(content)
            local_path = path
            url = playback = f'/static/uploads/sources/{path.name}'
        asset = upload_store.save(user_id, project_id, kind, {**metadata, 'name': name, 's3Key': url, 'localUrl': playback})
        committed = True
        return asset
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    finally:
        await file.close()
        if not committed:
            try:
                if stored_url and backend is not None:
                    await run_in_threadpool(backend.delete_uploaded_asset, stored_url)
                elif local_path is not None:
                    local_path.unlink(missing_ok=True)
            except Exception:
                logger.exception("failed to clean up an uncommitted upload")
            upload_store.release(user_id)
