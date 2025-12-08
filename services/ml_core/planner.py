from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

import requests

from config import Config
from src.logging_setup import setup_logging
from src.genai_client import GeminiClient
from src.library_store import AssetLibrary
from src.s3_utils import download_from_s3

log = logging.getLogger(__name__)


def _ensure_local_audio(job_id: str, src: str, work_dir: Path) -> Path:
    """
    Приводим audio_src к ЛОКАЛЬНОМУ файлу в work_dir.

    Варианты src:

      1) http(s)://...      -> скачиваем по HTTP в <work_dir>/<job_id>.<ext>
      2) локальный путь     -> копируем в work_dir
      3) S3-key / просто имя файла:
         - если локального файла нет -> считаем, что это ключ в S3_BUCKET_RAW_AUDIO,
           качаем оттуда в work_dir.

    В итоге всегда возвращаем Path к локальному файлу, который можно отдавать Gemini.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) HTTP/HTTPS-источник
    if src.startswith("http://") or src.startswith("https://"):
        url = src
        ext = Path(url).suffix or ".m4a"
        audio_path = work_dir / f"{job_id}{ext}"
        log.info("[ml-core] Downloading audio for job %s from URL %s", job_id, url)
        resp = requests.get(url, stream=True, timeout=600)
        resp.raise_for_status()
        with audio_path.open("wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return audio_path

    # 2) всё остальное — сначала пытаемся интерпретировать как локальный путь
    src_path = Path(src)
    ext = src_path.suffix or ".m4a"
    audio_path = work_dir / f"{job_id}{ext}"

    if src_path.exists():
        log.info("[ml-core] Copying local audio %s -> %s", src_path, audio_path)
        shutil.copy2(src_path, audio_path)
        return audio_path

    # 3) если локального файла нет — трактуем как S3-key в S3_BUCKET_RAW_AUDIO
    bucket = os.getenv("S3_BUCKET_RAW_AUDIO")
    if not bucket:
        raise FileNotFoundError(
            f"Audio source '{src}' is not a local file and S3_BUCKET_RAW_AUDIO is not set"
        )

    key = src_path.name if src_path.name else src
    log.info(
        "[ml-core] Local audio %s not found, trying s3://%s/%s -> %s",
        src_path,
        bucket,
        key,
        audio_path,
    )
    download_from_s3(bucket, key, audio_path)
    return audio_path


def build_edit_plan(job_id: str, audio_src: str, name: str) -> Dict[str, Any]:
    """
    Новый планировщик:

      - скачиваем/находим аудио (S3/HTTP/локально) -> локальный файл;
      - грузим библиотеку ассетов;
      - ОДНИМ вызовом GeminiClient.build_full_plan()
        получаем сегменты + шоты (SegmentEditPlan);
      - упаковываем всё в JSON-план, совместимый с рендером.
    """
    cfg = Config.from_env()
    setup_logging()

    # приводим audio_src к локальному пути
    audio_path = _ensure_local_audio(job_id, audio_src, cfg.work_dir / "ml_core_audio")

    gemini = GeminiClient(cfg)
    library = AssetLibrary(
        descriptions_dir=cfg.descriptions_dir,
        pins_dir=cfg.pins_dir,
    )
    library.load_from_files()

    if not library.assets:
        raise RuntimeError(
            f"Asset library is empty; check DESCRIPTIONS_DIR={cfg.descriptions_dir} "
            f"and PINS_DIR={cfg.pins_dir}"
        )

    library_payload = library.to_prompt_payload()

    # один комбинированный запрос: три сегмента + шоты
    segment_plans = gemini.build_full_plan(audio_path, library_payload)

    plan_segments: List[Dict[str, Any]] = []
    for seg_plan in segment_plans:
        seg = seg_plan.audio_segment
        plan_segments.append(
            {
                "index": seg.index,
                "start_sec": seg.start,
                "end_sec": seg.end,
                "duration_sec": seg.duration,
                "mood": seg.mood,
                "description": seg.description,
                "shots": [
                    {
                        "asset_prefix": shot.asset_prefix,
                        "target_duration_sec": shot.target_duration,
                    }
                    for shot in seg_plan.shots
                ],
            }
        )

    plan: Dict[str, Any] = {
        "job_id": job_id,
        "name": name,
        # здесь мы сохраняем исходный src (S3-key),
        # а не локальный путь — для рендера/отладки.
        "audio_source": audio_src,
        "segments": plan_segments,
    }

    log.info(
        "[ml-core] Built full edit plan for job %s: %d segments",
        job_id,
        len(plan_segments),
    )
    return plan
