# services/ml_core/ae_jsx_builder.py
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from config import Config
from render_v1.models import Payload
from .ae_client import AeMediaPayload

from src.library_store import AssetLibrary
from src.s3_utils import generate_presigned_url

log = logging.getLogger(__name__)

ENGINE_TEMPLATE_PATH = Path("render_v1/engine_template.jsx")


@dataclass
class AeBuildResult:
    render_jsx: str
    media: List[AeMediaPayload]
    output_relpath: str
    output_s3_key: str


def build_render_jsx_and_media(job_id: str, plan: Dict[str, Any]) -> AeBuildResult:
    """
    Собираем:
      - полный текст JSX-скрипта для AE (engine_template.jsx + PROJECT_DATA),
      - список медиафайлов, которые нужно заранее скачать нодой (Windows-сервер).

    План берем в том же формате, который возвращает planner.build_edit_plan():
      {
        "job_id": ...,
        "name": ...,
        "audio_source": "<s3-key или URL>",
        "segments": [
          {
            "index": ...,
            "start_sec": ...,
            "end_sec": ...,
            "duration_sec": ...,
            "mood": "...",
            "description": "...",
            "shots": [
              { "asset_prefix": "...", "target_duration_sec": ... },
              ...
            ],
          },
          ...
        ],
      }

    v1: рендерим только ПЕРВЫЙ сегмент.
    """
    cfg = Config.from_env()
    bucket_assets = os.getenv("S3_BUCKET_ASSET_STORAGE")
    bucket_audio = os.getenv("S3_BUCKET_RAW_AUDIO")

    # --- 1. Аудио ---

    audio_src = plan.get("audio_source")
    if not audio_src:
        raise RuntimeError("Plan has no 'audio_source'")

    # AE-нода должна уметь скачать аудио по HTTP(S); для S3-key генерим presigned URL
    if audio_src.startswith("http://") or audio_src.startswith("https://"):
        audio_url = audio_src
    else:
        if not bucket_audio:
            raise RuntimeError("S3_BUCKET_RAW_AUDIO is not set")
        audio_url = generate_presigned_url(bucket_audio, audio_src, expires_in=3600 * 24)

    media: List[AeMediaPayload] = [
        AeMediaPayload(
            url=audio_url,
            relpath="media/audio/track.m4a",
        )
    ]

    # --- 2. Разбираем план и библиотеку ассетов ---

    lib = AssetLibrary(cfg.descriptions_dir, cfg.pins_dir)
    lib.load_from_files()

    segments = plan.get("segments") or []
    if not segments:
        raise RuntimeError("Plan has no segments")

    # Пока берём только первый сегмент
    active_seg = segments[0]

    shots = active_seg.get("shots") or []
    used_prefixes = {
        s.get("asset_prefix")
        for s in shots
        if isinstance(s, dict) and s.get("asset_prefix")
    }

    prefix_to_relpath: Dict[str, str] = {}

    for prefix in sorted(used_prefixes):
        if not prefix:
            continue

        asset = lib.assets.get(prefix)
        if not asset:
            log.warning("[ae_jsx_builder] Asset prefix %s not found in library", prefix)
            continue

        key = asset.canonical.path.name  # basename файла (и одновременно S3-key)

        if bucket_assets:
            try:
                url = generate_presigned_url(bucket_assets, key, expires_in=3600 * 24)
            except Exception as e:
                log.warning(
                    "[ae_jsx_builder] Failed to generate presigned URL for %s/%s: %s",
                    bucket_assets,
                    key,
                    e,
                )
                continue
        else:
            # В библиотеке путь локальный, с S3 обратно уже не вернёмся — логируем и скипаем
            log.warning(
                "[ae_jsx_builder] S3_BUCKET_ASSET_STORAGE is not set; "
                "cannot generate URL for asset %s (key=%s)",
                prefix,
                key,
            )
            continue

        relpath = f"media/video/{key}"
        media.append(
            AeMediaPayload(
                url=url,
                relpath=relpath,
            )
        )
        prefix_to_relpath[prefix] = relpath

    # --- 3. Собираем ProjectStructure в формате render_v1.Payload ---

    items: List[Dict[str, Any]] = []

    # Аудио как footage-айтем (используется только как ref-слой)
    items.append(
        {
            "id": "audio_main",
            "type": "footage",
            "name": "Audio Track",
            "path": "media/audio/track.m4a",  # относительный путь внутри app/
            "isRef": True,
        }
    )

    # Видео-футажи
    for prefix in sorted(used_prefixes):
        relpath = prefix_to_relpath.get(prefix)
        if not relpath:
            continue
        asset = lib.assets.get(prefix)
        name = asset.canonical.path.name if asset else prefix
        items.append(
            {
                "id": prefix,
                "type": "footage",
                "name": name,
                "path": relpath,  # относительный путь внутри app/
                "isRef": False,
            }
        )

    # Главная композиция
    comp_duration = float(active_seg.get("duration_sec") or (active_seg["end_sec"] - active_seg["start_sec"]))
    comp_layers: List[Dict[str, Any]] = []

    seg_start = float(active_seg["start_sec"])

    # 1) Аудио-слой: двигаем startTime в минус, чтобы начать с нужного offset'а
    comp_layers.append(
        {
            "type": "ref",
            "refId": "audio_main",
            "name": "Audio",
            "startTime": float(-seg_start),
            "inPoint": 0.0,
            "outPoint": comp_duration,
            "enabled": False,  # видеоотображение выключено, оставляем только звук
            "audioEnabled": True,
        }
    )

    # 2) Видеошоты подряд
    t = 0.0
    for idx, shot in enumerate(shots):
        target = float(shot.get("target_duration_sec") or 0.0)
        asset_prefix = shot.get("asset_prefix")
        if not asset_prefix or target <= 0:
            continue
        if t >= comp_duration:
            break

        dur = target
        out = t + dur
        if out > comp_duration:
            out = comp_duration

        comp_layers.append(
            {
                "type": "ref",
                "refId": asset_prefix,
                "name": f"Shot {idx + 1}: {asset_prefix}",
                "startTime": t,
                "inPoint": t,
                "outPoint": out,
                "enabled": True,
                "audioEnabled": False,
            }
        )

        t = out
        if t >= comp_duration:
            break

    comp_item = {
        "id": "comp_main",
        "type": "comp",
        "name": plan.get("name") or f"Job {job_id}",
        "width": cfg.target_width,
        "height": cfg.target_height,
        "duration": comp_duration,
        "fps": float(23.976),  # можно заменить на cfg.target_fps, если хочешь синк по fps
        "pixelAspect": 1.0,
        "layers": comp_layers,
    }
    items.append(comp_item)

    raw_payload: Dict[str, Any] = {
        "project": {
            "projectName": plan.get("name") or f"Job {job_id}",
            "items": items,
        },
        "entryPoint": "comp_main",
    }

    # ВАЛИДАЦИЯ
    payload_model = Payload(**raw_payload)
    json_str = payload_model.model_dump_json(indent=2, exclude_none=True)

    # --- 4. Вклеиваем PROJECT_DATA в JSX-шаблон ---

    template_code = ENGINE_TEMPLATE_PATH.read_text(encoding="utf-8")
    js_variable = f"var PROJECT_DATA = {json_str};\n"

    final_jsx = template_code.replace("/*__PYTHON_DATA_INJECT__*/", js_variable)

    # имя выходного файла внутри app/
    output_relpath = "work/output.mp4"
    # ключ в S3 — просто job_id.mp4
    output_s3_key = f"{job_id}.mp4"

    return AeBuildResult(
        render_jsx=final_jsx,
        media=media,
        output_relpath=output_relpath,
        output_s3_key=output_s3_key,
    )
