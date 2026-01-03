# services/ml_core/ae_jsx_builder.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from config import Config
from src.render.ae.compiler import build_project_payload_from_composition
from .client import AeMediaPayload

# --- ИМПОРТИРУЕМ ПУТИ ИЗ КОНФИГА (ВМЕСТО ХАРДКОДА) ---
from src.config.styles.paths import TEXT_STYLES_PATH, FOOTAGE_PRESETS_PATH
from src.core.config.styles import FootagePresetId, SubtitleStyle
from src.render.ae.template_paths import JOB_TEMPLATE_PATH
from src.storage.library_store import AssetLibrary
from src.storage.s3 import generate_presigned_url

log = logging.getLogger(__name__)


@dataclass
class AeBuildResult:
    render_jsx: str
    media: List[AeMediaPayload]
    output_relpath: str
    output_s3_key: str


_SUBTITLE_STYLE_KEYS = {
    SubtitleStyle.DEFAULT: "main_subtitle",
    SubtitleStyle.HIGHLIGHT: "highlight_subtitle",
}


def _get_subtitle_style_id(style: SubtitleStyle | str) -> str:
    if isinstance(style, str):
        style = (
            SubtitleStyle(style)
            if style in SubtitleStyle._value2member_map_  # type: ignore[attr-defined]
            else SubtitleStyle.DEFAULT
        )

    return _SUBTITLE_STYLE_KEYS.get(style, "main_subtitle")


def build_render_jsx_and_media(job_id: str, plan: Dict[str, Any]) -> AeBuildResult:
    """
    Из плана (segments + subtitles) собираем composition.json-подобный объект,
    прогоняем через общий ассемблер и получаем PROJECT_DATA и список media
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

            # Legacy style fallback (used only when tagId is not provided)
            tag_id = sub.get("tagId") or sub.get("tag") or sub.get("textTag") or sub.get("fxTag")
            style_tag = str(sub.get("style") or SubtitleStyle.DEFAULT.value).lower()
            style_id = _get_subtitle_style_id(style_tag) if not tag_id else _get_subtitle_style_id(SubtitleStyle.DEFAULT.value)

            layer = {
                "type": "text",
                "styleId": style_id,
                "content": text,
                "startTime": float(in_p),
                "inPoint": float(in_p),
                "outPoint": float(out_p),
                "enabled": True,
                "audioEnabled": False,
            }

            # Tag-based styling (optional)
            if tag_id:
                layer["tagId"] = str(tag_id)
                tag_plan = sub.get("tagPlan") or sub.get("timing") or sub.get("timingPlan") or {}
                if isinstance(tag_plan, dict) and tag_plan:
                    layer["tagPlan"] = tag_plan
                # Allow "words" directly on subtitle item
                if "words" in sub:
                    layer.setdefault("tagPlan", {})
                    if isinstance(layer["tagPlan"], dict) and "words" not in layer["tagPlan"]:
                        layer["tagPlan"]["words"] = sub["words"]

            text_layers.append(layer)

        if text_layers:
            comp_text_item = {
                "id": "comp_text",
                "type": "comp",
                "name": "Text",
                "height": cfg.target_height,
                "layers": text_layers,
            }
            items.append(comp_text_item)

    # --- 3.2. comp_main ---

    comp_layers: List[Dict[str, Any]] = []

    # аудио-реф
    comp_layers.append(
        {
            "type": "ref",
            "refId": "audio_main",
            "name": "Audio",
            "inPoint": 0.0,
            "outPoint": comp_duration,
            "enabled": False,
            "audioEnabled": True,
        }
    )

    # шоты
    t = 0.0
    for shot in shots:
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
            "inPoint": t,
            "outPoint": out,
            "presetId": FootagePresetId.VERTICAL_FIT.value,
            "audioEnabled": False,
        }

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
                "inPoint": 0.0,
                "outPoint": comp_duration,
                "audioEnabled": False,
            }
        )

    comp_item = {
        "id": "comp_main",
        "type": "comp",
        "name": plan.get("name") or f"Job {job_id}",
        "layers": comp_layers,
    }
    items.append(comp_item)

    composition: Dict[str, Any] = {
        "projectSettings": {
            "name": plan.get("name") or f"Job {job_id}",
            "defaults": {
                "width": cfg.target_width,
                "height": cfg.target_height,
                "pixelAspect": 1.0,
                "fps": float(23.976),
                "duration": comp_duration,
            },
        },
        "items": items,
    }

    _, json_str = build_project_payload_from_composition(
        styles_path=TEXT_STYLES_PATH,
        presets_path=FOOTAGE_PRESETS_PATH,
        composition=composition,
        entry_point="comp_main",
    )

    template_code = JOB_TEMPLATE_PATH.read_text(encoding="utf-8")
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