"""Shared upload path for desktop, phone and warm-up; ownership stays server-side."""
from __future__ import annotations
import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4
from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from . import security, upload_store
from .media_uploads import normalize, MAX_SOURCE_BYTES
from .runtime import SETTINGS

logger = logging.getLogger(__name__)

_COPY_CHUNK = 4 * 1024 * 1024


def _spool_to_disk(file: UploadFile, dst: Path) -> None:
    """Скопировать тело загрузки на диск кусками, не поднимая 200 МБ в память."""
    file.file.seek(0)
    written = 0
    with dst.open('wb') as out:
        while chunk := file.file.read(_COPY_CHUNK):
            written += len(chunk)
            if written > MAX_SOURCE_BYTES:
                raise ValueError("Файл должен быть непустым и не больше 200 МБ")
            out.write(chunk)


def _put(put: Any, path: Path, **kwargs: Any) -> dict[str, str]:
    with path.open('rb') as body:
        return put(content=body, **kwargs)


async def upload(file: UploadFile, *, user_id: str, project_id: str, kind: str, format: str | None, local_dir: Path, backend: Any = None) -> dict[str, Any]:
    try:
        upload_store.reserve(user_id)
    except ValueError as exc:
        raise HTTPException(413, detail=str(exc)) from exc
    committed = False
    stored_url: str | None = None
    local_path: Path | None = None
    workdir = TemporaryDirectory(prefix='blast-upload-')
    try:
        folder = Path(workdir.name)
        src = folder/'input'
        await run_in_threadpool(_spool_to_disk, file, src)
        is_video = kind != 'warmup-audio'
        normalized, metadata = await run_in_threadpool(normalize, src, folder, video=is_video, expected_format=format)
        suffix = '.mp4' if is_video else '.wav'
        name = Path(security.sanitize_filename(file.filename, 'source' + suffix)).stem + suffix
        if SETTINGS.backend == 'production':
            put = backend.upload_source if is_video else backend.upload_hook_sound
            result = await run_in_threadpool(_put, put, normalized, user_id=user_id, filename=name,
                                             content_type='video/mp4' if is_video else 'audio/wav')
            url, playback = result['s3_url'], result['playback_url']
            stored_url = url
        else:
            local_dir.mkdir(parents=True, exist_ok=True)
            path = local_dir / (uuid4().hex + suffix)
            await run_in_threadpool(shutil.copyfile, normalized, path)
            local_path = path
            url = playback = f'/static/uploads/sources/{path.name}'
        asset = upload_store.save(user_id, project_id, kind, {**metadata, 'name': name, 's3Key': url, 'localUrl': playback})
        committed = True
        return asset
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    finally:
        await file.close()
        await run_in_threadpool(workdir.cleanup)
        if not committed:
            try:
                if stored_url and backend is not None:
                    await run_in_threadpool(backend.delete_uploaded_asset, stored_url)
                elif local_path is not None:
                    local_path.unlink(missing_ok=True)
            except Exception:
                logger.exception("failed to clean up an uncommitted upload")
            upload_store.release(user_id)
