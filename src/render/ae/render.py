# services/ml_core/render_ae.py
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from src.render.ae.compiler import build_project_payload_from_composition
from src.render.ae.template_paths import JOB_TEMPLATE_PATH
from src.storage.s3 import generate_presigned_url
from src.core.config.styles import TEXT_STYLES_PATH, FOOTAGE_PRESETS_PATH

from .client import AeMediaPayload, AeRenderClient

log = logging.getLogger(__name__)


DEFAULT_ENTRY_COMP = "comp_main"
OUTPUT_RELPATH = "work/output.mp4"


def _ensure_project_data(plan: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    project_data = plan.get("project_data")
    if project_data:
        entry_comp = project_data.get("entryPoint", DEFAULT_ENTRY_COMP)
        json_str = json.dumps(project_data, ensure_ascii=False, indent=2)
        return project_data, json_str, entry_comp

    composition = plan.get("composition")
    if not composition:
        raise RuntimeError("Plan has neither project_data nor composition")

    project_data, json_str = build_project_payload_from_composition(
        styles_path=TEXT_STYLES_PATH,
        presets_path=FOOTAGE_PRESETS_PATH,
        composition=composition,
        entry_point=DEFAULT_ENTRY_COMP,
    )
    entry_comp = project_data.get("entryPoint", DEFAULT_ENTRY_COMP)
    return project_data, json_str, entry_comp


def _build_media_payloads(
    project_data: Dict[str, Any], audio_source: str
) -> list[AeMediaPayload]:
    bucket_audio = os.getenv("S3_BUCKET_RAW_AUDIO")
    bucket_assets = os.getenv("S3_BUCKET_ASSET_STORAGE")

    if not audio_source:
        raise RuntimeError("Plan is missing audio_source")

    items = (project_data.get("project") or {}).get("items") or []
    if not items:
        raise RuntimeError("Project data has no items to render")

    seen_paths: set[str] = set()
    media: list[AeMediaPayload] = []

    for item in items:
        if (item.get("type") or "").lower() != "footage":
            continue

        path = item.get("path")
        if not path or path in seen_paths:
            continue

        if path.startswith("media/audio/"):
            if audio_source.startswith("http://") or audio_source.startswith("https://"):
                url = audio_source
            else:
                if not bucket_audio:
                    raise RuntimeError("S3_BUCKET_RAW_AUDIO is not set")
                url = generate_presigned_url(bucket_audio, audio_source, expires_in=3600 * 24)

            media.append(AeMediaPayload(url=url, relpath=path))
            seen_paths.add(path)
            continue

        if path.startswith("media/video/"):
            key = path[len("media/video/") :]
            if not bucket_assets:
                raise RuntimeError("S3_BUCKET_ASSET_STORAGE is not set")
            url = generate_presigned_url(bucket_assets, key, expires_in=3600 * 24)
            media.append(AeMediaPayload(url=url, relpath=path))
            seen_paths.add(path)

    if not media:
        raise RuntimeError("No media collected for AE render")

    return media


def render_from_plan(job_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    AE-рендер по готовому плану.

    План может содержать как project_data (Payload), так и исходную composition.
    В обоих случаях здесь собирается JSX для AE-ноды и список медиа.
    """

    bucket_output = os.getenv("S3_BUCKET_OUTPUT_VIDEO")
    if not bucket_output:
        raise RuntimeError("S3_BUCKET_OUTPUT_VIDEO is not set")

    log.info("[render_ae] Starting AE render for job_id=%s", job_id)

    project_data, json_str, entry_comp = _ensure_project_data(plan)
    media = _build_media_payloads(project_data, plan.get("audio_source", ""))

    template_code = JOB_TEMPLATE_PATH.read_text(encoding="utf-8")
    js_variable = f"var PROJECT_DATA = {json_str};\n"
    render_jsx = template_code.replace("/*__PYTHON_DATA_INJECT__*/", js_variable)

    client = AeRenderClient()
    output_s3_key = f"{job_id}.mp4"

    response = client.render(
        job_id=job_id,
        render_jsx=render_jsx,
        media=media,
        entry_comp=entry_comp or DEFAULT_ENTRY_COMP,
        output_relpath=OUTPUT_RELPATH,
        output_bucket=bucket_output,
        output_key=output_s3_key,
    )

    log.info(
        "[render_ae] AE node finished job_id=%s: success=%s, output_url=%s",
        job_id,
        response.success,
        response.output_url,
    )

    if not response.success:
        raise RuntimeError(f"AE render failed: {response.message}")

    output_url = response.output_url
    if not output_url:
        log.warning(
            "[render_ae] AE node returned empty output_url; generating presigned manually",
        )
        output_url = generate_presigned_url(bucket_output, output_s3_key, expires_in=3600 * 24)

    result_segment = {
        "index": 0,
        "s3_key": output_s3_key,
        "s3_url": output_url or "",
    }

    return {
        "job_id": job_id,
        "segments": [result_segment],
    }
