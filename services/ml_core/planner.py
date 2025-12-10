# services/ml_core/planner.py
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

from config import Config
from render_v1.assembler_core import build_project_payload_from_composition
from src.genai.client_base import GenaiClientBase
from src.genai.planners import AePlanner
from src.storage.library_store import AssetLibrary
from src.storage.s3 import download_from_s3

log = logging.getLogger(__name__)


TEXT_STYLES_PATH = Path("config/styles/text_styles.json")
FOOTAGE_PRESETS_PATH = Path("config/styles/footage_presets.json")


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
      - дергает AePlanner.build_ae_project (composition.json от модели),
      - прогоняет composition через ассемблер render_v1 для нормализации,
      - возвращает план с полями job_id, name, audio_source,
        composition (сырое от модели) и project_data (нормализованное).
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

    composition = planner.build_ae_project(audio_path, library_payload)

    raw_payload, json_str = build_project_payload_from_composition(
        styles_path=TEXT_STYLES_PATH,
        presets_path=FOOTAGE_PRESETS_PATH,
        composition=composition,
        entry_point="comp_main",
    )

    plan: Dict[str, Any] = {
        "job_id": job_id,
        "name": name,
        "audio_source": audio_src,
        "composition": composition,
        "project_data": raw_payload,
        "project_data_json": json_str,
    }

    log.info(
        "[ml-core] Built AE composition for job %s: %d items",
        job_id,
        len(composition.get("items", [])),
    )
    return plan
