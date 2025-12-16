# services/ml_core/planner.py
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from config import Config
from render_v1.assembler_core import build_project_payload_from_composition_v2
from src.genai.ae_composition_schema import AeComposition
from src.genai.client_base import GenaiClientBase
from src.genai.planners import AePlanner
from src.storage.library_store import AssetLibrary
from src.storage.s3 import download_from_s3

log = logging.getLogger(__name__)


TEXT_STYLES_PATH = Path("config/styles/text_styles.json")
FOOTAGE_PRESETS_PATH = Path("config/styles/footage_presets.json")
TEXT_MOTION_LIBRARY_PATH = Path("config/styles/text_motion_library.json")
PROJECT_SETTINGS_TEMPLATE_PATH = Path("config/styles/project_settings_template.json")

_STYLE_PACK = (os.getenv("AE_STYLE_PACK") or "pop-music").strip()
_PACK_DIR = Path("config/styles") / _STYLE_PACK


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
    # Validate composition structure (ensures required overrides for motion presets)
    composition = AeComposition.model_validate(composition).model_dump()

    # Prefer style-pack folder if present; otherwise fallback to legacy config/styles/*.json
    if _PACK_DIR.is_dir():
        styles_path = _PACK_DIR / "text_styles.json"
        presets_path = _PACK_DIR / "footage_presets.json"
        motion_path = _PACK_DIR / "text_motion_library.json"
        proj_path = _PACK_DIR / "project_settings_template.json"
    else:
        styles_path = TEXT_STYLES_PATH
        presets_path = FOOTAGE_PRESETS_PATH
        motion_path = TEXT_MOTION_LIBRARY_PATH
        proj_path = PROJECT_SETTINGS_TEMPLATE_PATH

    raw_payload, json_str = build_project_payload_from_composition_v2(
        composition=composition,
        styles_path=styles_path,
        presets_path=presets_path,
        motion_library_path=motion_path,
        project_settings_template_path=proj_path,
        entry_point="comp_main",
        style_pack=_STYLE_PACK,
    )

    # Persist LLM + normalized payload for debugging
    try:
        work_dir = Path(os.getenv("WORK_DIR", "/app/work"))
        # Prefer DEBUG_ARTIFACTS_DIR if set; otherwise fallback to WORK_DIR/llm_logs
        debug_root = os.getenv("DEBUG_ARTIFACTS_DIR", "").strip()
        if debug_root:
            log_dir = Path(debug_root) / job_id
        else:
            log_dir = work_dir / "llm_logs" / job_id
        log_dir.mkdir(parents=True, exist_ok=True)
        # 1) raw LLM output (composition after pydantic validation)
        (log_dir / "composition.json").write_text(
            json.dumps(composition, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 2) assembled PROJECT_DATA (pretty JSON string)
        (log_dir / "project_data.json").write_text(json_str, encoding="utf-8")
        # 3) also store dict form for quick grepping
        (log_dir / "project_data_raw.json").write_text(
            json.dumps(raw_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

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
