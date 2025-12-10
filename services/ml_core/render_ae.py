# services/ml_core/render_ae.py
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from config import Config
from .ae_client import AeRenderClient
from .ae_jsx_builder import build_render_jsx_and_media

log = logging.getLogger(__name__)


def render_from_plan(job_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    AE-рендер по готовому плану.

    v1:
      - Берём первый сегмент из планировщика.
      - Собираем под него PROJECT_DATA в формате render_v1.Payload (comp_main + ref-слои).
      - Генерируем единый финальный ролик и кладём его в S3_BUCKET_OUTPUT_VIDEO.

    Контракт по результату совпадает с render_ffmpeg.render_from_plan:
      {
        "job_id": str,
        "segments": [
          {"index": 0, "s3_key": "...", "s3_url": "..."}
        ]
      }
    """

    cfg = Config.from_env()  # пока не обязателен, но может пригодиться для логики дальше
    bucket_output = os.getenv("S3_BUCKET_OUTPUT_VIDEO")
    if not bucket_output:
        raise RuntimeError("S3_BUCKET_OUTPUT_VIDEO is not set")

    log.info("[render_ae] Starting AE render for job_id=%s", job_id)

    # AE_NODE_URL берется внутри клиента из env, если base_url не передан
    client = AeRenderClient()

    build = build_render_jsx_and_media(job_id, plan)

    log.debug(
        "[render_ae] Built AE job for job_id=%s: jsx_len=%d, media_count=%d",
        job_id,
        len(build.render_jsx),
        len(build.media),
    )

    response = client.render(
        job_id=job_id,
        render_jsx=build.render_jsx,
        media=build.media,
        entry_comp="comp_main",
        output_relpath=build.output_relpath,
        output_bucket=bucket_output,
        output_key=build.output_s3_key,
    )

    log.info(
        "[render_ae] AE node finished job_id=%s: success=%s, output_url=%s",
        job_id,
        response.success,
        response.output_url,
    )

    if not response.success:
        raise RuntimeError(f"AE render failed: {response.message}")

    result_segment = {
        "index": 0,
        "s3_key": build.output_s3_key,
        "s3_url": response.output_url or "",
    }

    return {
        "job_id": job_id,
        "segments": [result_segment],
    }
