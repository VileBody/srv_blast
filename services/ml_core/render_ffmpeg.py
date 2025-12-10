from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from config import Config
from src.core.logging import setup_logging
from src.core.models import AudioSegmentPlan, SegmentEditPlan, VisualShotSpec
from src.genai.client_base import GenaiClientBase
from src.genai.subtitles import GeminiSubtitlesClient
from src.render.ffmpeg.ffmpeg_executor import FFmpegExecutor
from src.render.subtitles.elevenlabs_client import ElevenLabsClient
from src.render.subtitles.service import SubtitleService
from src.storage.library_store import AssetLibrary
from src.storage.s3 import generate_presigned_url, upload_file_to_s3
from .planner import _ensure_local_audio  # <- ВАЖНО: используем ту же функцию, что и в planner

log = logging.getLogger(__name__)


def _segment_plans_from_plan_dict(plan: Dict[str, Any]) -> List[SegmentEditPlan]:
    seg_plans: List[SegmentEditPlan] = []
    for seg in plan.get("segments", []):
        audio_seg = AudioSegmentPlan(
            index=seg["index"],
            start=seg["start_sec"],
            end=seg["end_sec"],
            mood=seg.get("mood", "") or "",
            description=seg.get("description", "") or "",
        )
        shots = [
            VisualShotSpec(
                asset_prefix=s["asset_prefix"],
                target_duration=float(s["target_duration_sec"]),
            )
            for s in seg.get("shots", [])
        ]
        seg_plans.append(SegmentEditPlan(audio_segment=audio_seg, shots=shots))
    return seg_plans


def render_from_plan(job_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    FFmpeg-рендер по готовому плану:

      - из plan["segments"] строим SegmentEditPlan;
      - по plan["audio_source"] (S3-key или URL) получаем локальный файл через _ensure_local_audio;
      - для каждого сегмента:
        * режем аудио под нужный отрезок,
        * собираем видеошоты из ассетов,
        * склеиваем и компрессим (вертикаль),
        * добавляем сабы (Gemini или ElevenLabs),
        * загружаем финальный сегмент в S3_BUCKET_OUTPUT_VIDEO;
      - возвращаем список сегментов с ссылками: {"job_id", "segments":[{index,s3_key,s3_url},...]}.
    """
    cfg = Config.from_env()
    setup_logging()

    bucket_out = os.getenv("S3_BUCKET_OUTPUT_VIDEO")
    if not bucket_out:
        raise RuntimeError("S3_BUCKET_OUTPUT_VIDEO is not set; cannot upload final videos")

    work_root = cfg.work_dir / f"job_{job_id}"
    work_root.mkdir(parents=True, exist_ok=True)
    output_dir = cfg.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_src = plan.get("audio_source")
    if not audio_src:
        raise RuntimeError("Plan has no 'audio_source' field")

    # Критично: приводим audio_src (S3-key или URL) к ЛОКАЛЬНОМУ файлу в work_dir
    audio_path = _ensure_local_audio(job_id, audio_src, cfg.work_dir / "ml_core_audio")
    log.info("[render] Using local audio file for job %s: %s", job_id, audio_path)

    ffmpeg = FFmpegExecutor()
    log.info("FFmpegExecutor using ffmpeg_bin=%r, ffprobe_bin=%r", ffmpeg.ffmpeg_bin, ffmpeg.ffprobe_bin)

    library = AssetLibrary(cfg.descriptions_dir, cfg.pins_dir)
    library.load_from_files()

    # Субтитры
    provider = cfg.subtitles_provider
    gemini_client = GenaiClientBase(cfg) if provider == "gemini" else None
    gemini = (
        GeminiSubtitlesClient(gemini_client) if gemini_client is not None else None
    )
    eleven = ElevenLabsClient(cfg) if provider == "elevenlabs" else None
    subs_work_dir = cfg.work_dir / f"subs_{job_id}"
    subtitle_service = SubtitleService(
        provider=provider,
        ffmpeg=ffmpeg,
        work_dir=subs_work_dir,
        gemini=gemini,
        eleven=eleven,
    )

    segment_plans = _segment_plans_from_plan_dict(plan)
    if not segment_plans:
        raise RuntimeError("Plan has no segments")

    uploaded_segments: List[Dict[str, Any]] = []

    for seg_plan in segment_plans:
        seg_idx = seg_plan.audio_segment.index
        seg_work = work_root / f"seg_{seg_idx:02d}"
        seg_work.mkdir(parents=True, exist_ok=True)

        audio_seg = seg_plan.audio_segment
        log.info(
            "[render] Rendering segment %d (%.2f–%.2f)",
            seg_idx,
            audio_seg.start,
            audio_seg.end,
        )

        # 1) режем аудио
        seg_audio = seg_work / f"seg_{seg_idx:02d}.m4a"
        ffmpeg.cut_audio_segment(
            audio_path,
            audio_seg.start,
            audio_seg.end,
            seg_audio,
        )

        # 2) шоты
        shot_files: List[Path] = []
        for i, shot in enumerate(seg_plan.shots):
            asset = library.get_asset(shot.asset_prefix)
            src = asset.canonical.path
            shot_out = seg_work / f"seg_{seg_idx:02d}_shot_{i:02d}.mp4"

            ffmpeg.extract_or_loop_video(
                src,
                shot.target_duration,
                shot_out,
                source_duration=None,
            )
            shot_files.append(shot_out)

        if not shot_files:
            log.warning("[render] Segment %d has no shots, skipping", seg_idx)
            continue

        # 3) склеиваем шоты
        seg_video_no_audio = seg_work / f"seg_{seg_idx:02d}_video.mp4"
        ffmpeg.concat_videos(shot_files, seg_video_no_audio, reencode=True)

        # 4) совмещаем аудио + видео
        seg_video = seg_work / f"seg_{seg_idx:02d}_final_raw.mp4"
        ffmpeg.combine_audio_video(seg_video_no_audio, seg_audio, seg_video)

        # 5) компрессим под вертикаль
        seg_final = output_dir / f"{job_id}_seg_{seg_idx:02d}_final.mp4"
        ffmpeg.compress_video(
            seg_video,
            seg_final,
            cfg.target_width,
            cfg.target_height,
        )

        # 6) сабы на каждый сегмент
        seg_final_subs = output_dir / f"{job_id}_seg_{seg_idx:02d}_final_subs.mp4"
        try:
            seg_with_subs = subtitle_service.add_subtitles(seg_final, seg_final_subs)
        except Exception as e:
            log.warning(
                "[render] Failed to add subtitles for segment %d: %s. Using video without subs.",
                seg_idx,
                e,
            )
            seg_with_subs = seg_final

        # 7) upload в S3 и presigned URL
        key = seg_with_subs.name
        upload_file_to_s3(bucket_out, key, seg_with_subs, content_type="video/mp4")
        url = generate_presigned_url(bucket_out, key, expires_in=3600 * 24)

        uploaded_segments.append(
            {
                "index": seg_idx,
                "s3_key": key,
                "s3_url": url,
            }
        )
        log.info(
            "[render] Segment %d uploaded to s3://%s/%s",
            seg_idx,
            bucket_out,
            key,
        )

    if not uploaded_segments:
        raise RuntimeError("No segments were rendered/uploaded")

    result = {
        "job_id": job_id,
        "segments": uploaded_segments,
    }
    log.info(
        "[render] Finished job %s with %d segments",
        job_id,
        len(uploaded_segments),
    )
    return result
