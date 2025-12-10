# services/ml_core/ae_jsx_builder.py
from __future__ import annotations

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
from src.config.styles import FootagePresetId, SubtitleStyle
from src.config import style_loader

log = logging.getLogger(__name__)

ENGINE_TEMPLATE_PATH = Path("render_v1/engine_template.jsx")


@dataclass
class AeBuildResult:
    render_jsx: str
    media: List[AeMediaPayload]
    output_relpath: str
    output_s3_key: str


def _apply_preset_to_layer(layer: Dict[str, Any], preset_id: FootagePresetId | str) -> None:
    pid = preset_id.value if isinstance(preset_id, FootagePresetId) else str(preset_id)
    layer["presetId"] = pid

    preset_cfg = style_loader.get_footage_preset(pid)
    transform_cfg = preset_cfg.get("transform")
    if transform_cfg and "transform" not in layer:
        layer["transform"] = dict(transform_cfg)


def build_render_jsx_and_media(job_id: str, plan: Dict[str, Any]) -> AeBuildResult:
    """
    Из плана (segments + subtitles) собираем PROJECT_DATA и список media
    для AE-ноды.
    """
    cfg = Config.from_env()
    bucket_assets = os.getenv("S3_BUCKET_ASSET_STORAGE")
    bucket_audio = os.getenv("S3_BUCKET_RAW_AUDIO")

    # --- 1. Аудио ---

    audio_src = plan.get("audio_source")
    if not audio_src:
        raise RuntimeError("Plan has no 'audio_source'")

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

    # --- 2. Сегменты и ассеты ---

    segments = plan.get("segments") or []
    if not segments:
        raise RuntimeError("Plan has no segments")

    active_seg = segments[0]
    seg_start = float(active_seg["start_sec"])
    comp_duration = float(
        active_seg.get("duration_sec") or (active_seg["end_sec"] - active_seg["start_sec"])
    )

    lib = AssetLibrary(cfg.descriptions_dir, cfg.pins_dir)
    lib.load_from_files()

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

        key = asset.canonical.path.name

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
            url = asset.canonical.http_url or ""
            if not url:
                log.warning("[ae_jsx_builder] No URL for asset prefix %s", prefix)
                continue

        relpath = f"media/video/{key}"
        media.append(
            AeMediaPayload(
                url=url,
                relpath=relpath,
            )
        )
        prefix_to_relpath[prefix] = relpath

    # --- 3. ProjectStructure ---

    items: List[Dict[str, Any]] = []

    # аудио
    items.append(
        {
            "id": "audio_main",
            "type": "footage",
            "name": "Audio Track",
            "path": "media/audio/track.m4a",
            "isRef": True,
        }
    )

    # видео-футажи
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
                "path": relpath,
                "isRef": False,
            }
        )

    # --- 3.1. comp_text (субтитры), если есть ---

    subtitles = plan.get("subtitles") or []
    comp_text_item: Dict[str, Any] | None = None

    if subtitles:
        text_layers: List[Dict[str, Any]] = []
        for sub in subtitles:
            try:
                start_global = float(sub["start_sec"])
                end_global = float(sub["end_sec"])
                text = str(sub["text"])
            except Exception:
                continue

            in_p = start_global - seg_start
            out_p = end_global - seg_start

            if out_p <= 0 or in_p >= comp_duration:
                continue

            in_p = max(0.0, in_p)
            out_p = min(comp_duration, out_p)

            style_tag = str(sub.get("style") or SubtitleStyle.DEFAULT.value).lower()
            style_props = style_loader.get_text_style(style_tag)

            text_doc: Dict[str, Any] = dict(style_props)
            text_doc["text"] = text

            layer = {
                "type": "text",
                "startTime": float(in_p),
                "inPoint": float(in_p),
                "outPoint": float(out_p),
                "enabled": True,
                "audioEnabled": False,
                "textDocument": text_doc,
            }
            text_layers.append(layer)

        if text_layers:
            comp_text_item = {
                "id": "comp_text",
                "type": "comp",
                "name": "Text",
                "width": cfg.target_width,
                "height": cfg.target_height,
                "duration": comp_duration,
                "fps": float(23.976),
                "pixelAspect": 1.0,
                "layers": text_layers,
            }
            items.append(comp_text_item)

    # --- 3.2. comp_main ---

    comp_layers: List[Dict[str, Any]] = []

    # аудио
    comp_layers.append(
        {
            "type": "ref",
            "refId": "audio_main",
            "name": "Audio",
            "startTime": float(-seg_start),
            "inPoint": 0.0,
            "outPoint": comp_duration,
            "enabled": False,
            "audioEnabled": True,
        }
    )

    # шоты
    t = 0.0
    for idx, shot in enumerate(shots):
        asset_prefix = shot.get("asset_prefix")
        if not asset_prefix:
            continue
        target = float(shot.get("target_duration_sec") or 0.0)
        if target <= 0:
            continue
        if t >= comp_duration:
            break

        dur = target
        out = t + dur
        if out > comp_duration:
            out = comp_duration

        layer: Dict[str, Any] = {
            "type": "ref",
            "refId": asset_prefix,
            "name": f"Shot {idx + 1}: {asset_prefix}",
            "startTime": t,
            "inPoint": t,
            "outPoint": out,
            "enabled": True,
            "audioEnabled": False,
        }

        _apply_preset_to_layer(layer, FootagePresetId.VERTICAL_FIT)

        comp_layers.append(layer)
        t = out
        if t >= comp_duration:
            break

    # overlay текста
    if comp_text_item is not None:
        comp_layers.append(
            {
                "type": "ref",
                "refId": "comp_text",
                "name": "Text Overlay",
                "startTime": 0.0,
                "inPoint": 0.0,
                "outPoint": comp_duration,
                "enabled": True,
                "audioEnabled": False,
            }
        )

    comp_item = {
        "id": "comp_main",
        "type": "comp",
        "name": plan.get("name") or f"Job {job_id}",
        "width": cfg.target_width,
        "height": cfg.target_height,
        "duration": comp_duration,
        "fps": float(23.976),
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

    payload_model = Payload(**raw_payload)
    json_str = payload_model.model_dump_json(indent=2, exclude_none=True)

    template_code = ENGINE_TEMPLATE_PATH.read_text(encoding="utf-8")
    js_variable = f"var PROJECT_DATA = {json_str};\n"
    final_jsx = template_code.replace("/*__PYTHON_DATA_INJECT__*/", js_variable)

    output_relpath = "work/output.mp4"
    output_s3_key = f"{job_id}.mp4"

    return AeBuildResult(
        render_jsx=final_jsx,
        media=media,
        output_relpath=output_relpath,
        output_s3_key=output_s3_key,
    )
