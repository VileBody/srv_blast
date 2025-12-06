from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

import requests

from config import Config
from src.logging_setup import setup_logging
from src.genai_client import GeminiClient
from src.library_store import AssetLibrary

log = logging.getLogger(__name__)


def _ensure_local_audio(job_id: str, src: str, work_dir: Path) -> Path:
    """
    Приводим аудио к локальному файлу:
    - если src = http(s)://... -> качаем
    - если src = локальный путь -> копируем
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    audio_path = work_dir / f"{job_id}.m4a"

    if src.startswith("http://") or src.startswith("https://"):
        log.info("[ml-core] Downloading audio for job %s from %s", job_id, src)
        resp = requests.get(src, stream=True, timeout=600)
        resp.raise_for_status()
        with audio_path.open("wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)
    else:
        src_path = Path(src)
        if not src_path.exists():
            raise FileNotFoundError(f"Audio path does not exist: {src_path}")
        log.info("[ml-core] Copying local audio %s -> %s", src_path, audio_path)
        shutil.copy2(src_path, audio_path)

    return audio_path


def build_edit_plan(job_id: str, audio_src: str, name: str) -> Dict[str, Any]:
    """
    Строим логический план эдита.
    Никакого ffmpeg/рендера здесь нет, только мозги (Gemini + библиотека клипов).
    """
    cfg = Config.from_env()
    setup_logging()

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
    segments = gemini.select_audio_highlights(audio_path)

    plan_segments: List[Dict[str, Any]] = []
    for seg in segments:
        shots = gemini.plan_visuals_for_segment(seg, library_payload)
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
                    for shot in shots
                ],
            }
        )

    plan: Dict[str, Any] = {
        "job_id": job_id,
        "name": name,
        "audio_source": audio_src,
        "segments": plan_segments,
    }

    log.info(
        "[ml-core] Built edit plan for job %s: %d segments",
        job_id,
        len(plan_segments),
    )
    return plan
