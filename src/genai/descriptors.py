from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path

from google.genai import types
from pydantic import BaseModel, ValidationError

from .client_base import GenaiClientBase
from .prompts import DESCRIBE_VIDEO_SYSTEM

log = logging.getLogger(__name__)


class VideoOptionModel(BaseModel):
    file: str
    width: int
    height: int


class VideoResponseModel(BaseModel):
    response: dict
    options: list[VideoOptionModel]


def describe_video(
    client: GenaiClientBase, video_path: Path, variants_payload: list[dict]
) -> dict:
    mime_type, _ = mimetypes.guess_type(str(video_path))
    log.info(
        "[describe_video] Local guess mime_type for %s -> %r",
        video_path,
        mime_type,
    )

    file_obj = client.client.files.upload(file=str(video_path))
    log.info(
        "[describe_video] Uploaded video %s (id=%s) for model %s",
        video_path,
        getattr(file_obj, "name", None),
        client.cfg.gemini_model_planning,
    )

    file_obj = client.wait_file_active(file_obj, "describe_video")

    payload = json.dumps(variants_payload, ensure_ascii=False)

    log.info(
        "[describe_video] Request config: model=%s, mime=application/json",
        client.cfg.gemini_model_planning,
    )

    resp = client.client.models.generate_content(
        model=client.cfg.gemini_model_planning,
        contents=[
            DESCRIBE_VIDEO_SYSTEM,
            "Список доступных вариантов файла (верни их в поле 'options'): " + payload,
            file_obj,
        ],
        config=types.GenerateContentConfig(
            temperature=0.5,
            response_mime_type="application/json",
        ),
    )

    raw = client.extract_text_or_raise(resp, "describe_video")

    try:
        parsed = VideoResponseModel.model_validate_json(raw)
    except ValidationError as exc:
        log.error("[describe_video] Pydantic validation error: %s", exc)
        log.debug("[describe_video] Raw JSON that failed: %s", raw)
        raise

    return json.loads(parsed.model_dump_json(by_alias=True, exclude_none=True))
