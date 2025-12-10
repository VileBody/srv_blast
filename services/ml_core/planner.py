# services/ml_core/planner.py
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from config import Config
from src.genai.client_base import GenaiClientBase
from src.genai.planners import AePlanner
from src.storage.library_store import AssetLibrary
from src.storage.s3 import download_from_s3
from src.render.ae.models import AeEditPlan, AeSegment, SubtitleLine

log = logging.getLogger(__name__)


def _ensure_local_audio(job_id: str, audio_src: str, dst_dir: Path) -> Path:
    """
    audio_src — S3 key (как кладёт оркестратор).
    Качаем в dst_dir/<job_id>.m4a, если его ещё нет.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{job_id}.m4a"
    dest = dst_dir / filename

    if dest.is_file():
        log.info("[ml-core] Local audio %s already exists, reuse", dest)
        return dest

    bucket = os.getenv("S3_BUCKET_RAW_AUDIO")
    if not bucket:
        raise RuntimeError("S3_BUCKET_RAW_AUDIO is not set for _ensure_local_audio")

    log.info(
        "[ml-core] Local audio %s not found, trying s3://%s/%s -> %s",
        filename,
        bucket,
        audio_src,
        dest,
    )
    download_from_s3(bucket=bucket, key=audio_src, dest=dest)
    return dest


def build_edit_plan(job_id: str, audio_src: str, name: str) -> Dict[str, Any]:
    """
    Главный планировщик под AE:

      - приводит audio_src к локальному пути,
      - дергает AePlanner.build_ae_edit_plan (AeEditPlan JSON),
      - валидирует через AeEditPlan из src.render.ae.models,
      - возвращает plan с полями:
          job_id, name, audio_source, segments[], subtitles[], total_duration_sec.
    """
    cfg = Config.from_env()

    audio_path = _ensure_local_audio(job_id, audio_src, cfg.work_dir / "ml_core_audio")

    genai_client = GenaiClientBase(cfg)
    planner = AePlanner(genai_client)
    library = AssetLibrary(cfg.descriptions_dir, cfg.pins_dir)
    library.load_from_files()

    if not library.assets:
        raise RuntimeError(
            f"Asset library is empty; check DESCRIPTIONS_DIR={cfg.descriptions_dir} "
            f"and PINS_DIR={cfg.pins_dir}"
        )

    library_payload = library.to_prompt_payload()

    raw_plan = planner.build_ae_edit_plan(audio_path, library_payload)

    try:
        ae_plan = AeEditPlan.model_validate(raw_plan)
    except Exception as e:
        log.error("[ml-core] Failed to validate AeEditPlan: %s", e)
        # грубый фолбэк
        ae_plan = AeEditPlan(
            total_duration_sec=raw_plan.get("total_duration_sec", 0.0),
            segments=[AeSegment(**s) for s in raw_plan.get("segments", [])],
            subtitles=[SubtitleLine(**s) for s in raw_plan.get("subtitles", [])],
        )

    plan_segments: List[Dict[str, Any]] = []
    for seg in ae_plan.segments:
        plan_segments.append(
            {
                "index": seg.index,
                "start_sec": seg.start_sec,
                "end_sec": seg.end_sec,
                "duration_sec": seg.end_sec - seg.start_sec,
                "mood": seg.mood,
                "description": seg.description,
                "shots": [
                    {
                        "asset_prefix": shot.asset_prefix,
                        "target_duration_sec": shot.end_sec - shot.start_sec,
                    }
                    for shot in seg.shots
                ],
            }
        )

    plan_subtitles: List[Dict[str, Any]] = []
    for sub in ae_plan.subtitles:
        plan_subtitles.append(
            {
                "index": sub.index,
                "start_sec": sub.start_sec,
                "end_sec": sub.end_sec,
                "text": sub.text,
                "style": sub.style.value,  # "default" / "highlight"
            }
        )

    plan: Dict[str, Any] = {
        "job_id": job_id,
        "name": name,
        "audio_source": audio_src,
        "segments": plan_segments,
        "subtitles": plan_subtitles,
        "total_duration_sec": ae_plan.total_duration_sec,
    }

    log.info(
        "[ml-core] Built AE edit plan for job %s: %d segments, %d subtitles",
        job_id,
        len(plan_segments),
        len(plan_subtitles),
    )
    return plan
