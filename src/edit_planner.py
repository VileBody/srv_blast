from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from .ffmpeg_utils import FFmpegExecutor
from .genai_client import GeminiClient
from .library_store import AssetLibrary
from .models import EditProject, SegmentEditPlan, AudioSegmentPlan, VisualShotSpec

log = logging.getLogger(__name__)


class EditPlanner:
    def __init__(
        self,
        gemini: GeminiClient,
        library: AssetLibrary,
        ffmpeg: FFmpegExecutor,
        work_dir: Path,
        output_dir: Path,
        target_width: int,
        target_height: int,
    ):
        self.gemini = gemini
        self.library = library
        self.ffmpeg = ffmpeg
        self.work_dir = work_dir
        self.output_dir = output_dir
        self.target_width = target_width
        self.target_height = target_height

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ---- полный цикл под один аудио-файл ----

    def build_edit(self, audio_path: Path, name: str) -> EditProject:
        # 1) аудио-сегменты
        segments = self.gemini.select_audio_highlights(audio_path)
        segment_plans: List[SegmentEditPlan] = []

        library_payload = self.library.to_prompt_payload()

        for seg in segments:
            shots = self.gemini.plan_visuals_for_segment(seg, library_payload)
            segment_plans.append(SegmentEditPlan(audio_segment=seg, shots=shots))

        project = EditProject(audio_path=audio_path, segments=segment_plans)

        # 2) рендерим каждый сегмент: режем аудио + подбираем видео
        final_videos: List[Path] = []

        for seg_plan in project.segments:
            seg_idx = seg_plan.audio_segment.index
            log.info(
                "Rendering segment %d (%.2f–%.2f)",
                seg_idx,
                seg_plan.audio_segment.start,
                seg_plan.audio_segment.end,
            )

            seg_audio = self.work_dir / f"seg_{seg_idx:02d}.m4a"
            self.ffmpeg.cut_audio_segment(
                audio_path,
                seg_plan.audio_segment.start,
                seg_plan.audio_segment.end,
                seg_audio,
            )

            shot_files: List[Path] = []
            for i, shot in enumerate(seg_plan.shots):
                asset = self.library.get_asset(shot.asset_prefix)
                src = asset.canonical.path
                shot_out = self.work_dir / f"seg_{seg_idx:02d}_shot_{i:02d}.mp4"

                # duration для source можно заранее хранить в asset.canonical.duration,
                # но если его нет — ffprobe сам вычислит.
                self.ffmpeg.extract_or_loop_video(
                    src, shot.target_duration, shot_out, source_duration=None
                )
                shot_files.append(shot_out)

            seg_video_no_audio = self.work_dir / f"seg_{seg_idx:02d}_video.mp4"
            self.ffmpeg.concat_videos(shot_files, seg_video_no_audio, reencode=True)

            seg_video = self.work_dir / f"seg_{seg_idx:02d}_final_raw.mp4"
            self.ffmpeg.combine_audio_video(seg_video_no_audio, seg_audio, seg_video)

            # 3) финальное пережатие каждого сегмента под нужное телефонное разрешение
            seg_final = self.output_dir / f"{name}_seg_{seg_idx:02d}_final.mp4"
            self.ffmpeg.compress_video(
                seg_video, seg_final, self.target_width, self.target_height
            )

            final_videos.append(seg_final)
            log.info("Segment %d final ready: %s", seg_idx, seg_final)

        project.final_videos = final_videos
        log.info("Edit project ready with %d segments", len(final_videos))
        return project
