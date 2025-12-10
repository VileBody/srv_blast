
from __future__ import annotations

import json
import logging
import math
import subprocess
from pathlib import Path
from typing import Iterable, Tuple, List

log = logging.getLogger(__name__)


class FFmpegExecutor:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe"):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        log.info(
            "FFmpegExecutor using ffmpeg_bin=%r, ffprobe_bin=%r",
            self.ffmpeg_bin,
            self.ffprobe_bin,
        )

    # ---------- внутренний helper для запуска ffmpeg ----------

    def _run(self, cmd: list[str]) -> None:
        """Запускает ffmpeg/ffprobe, логирует stderr при ошибке."""
        log.debug("Running command: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            log.error(
                "FFmpeg command failed (code=%s): %s\nstderr:\n%s",
                proc.returncode,
                " ".join(cmd),
                proc.stderr,
            )
            raise RuntimeError(f"ffmpeg command failed with code {proc.returncode}")
        if proc.stderr:
            log.debug("ffmpeg stderr: %s", proc.stderr.strip())

    # ---------- probe ----------

    def probe_video(self, path: Path) -> Tuple[int, int, float]:
        """Вернёт (width, height, duration_sec)."""
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
        log.debug("Running ffprobe: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            log.error(
                "ffprobe failed (code=%s) on %s\nstderr:\n%s",
                proc.returncode,
                path,
                proc.stderr,
            )
            raise RuntimeError(f"ffprobe failed with code {proc.returncode}")
        data = json.loads(proc.stdout)
        stream = data["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        duration = float(data["format"]["duration"])
        return width, height, duration

    # ---------- низкоуровневые операции ----------

    def cut_audio_segment(
        self, input_audio: Path, start: float, end: float, output_audio: Path
    ) -> None:
        """
        Режем аудио-кусок.

        Важно: выкидываем видео (-vn), потому что исходник может быть mp4/m4a
        с видеопотоком (h264), а m4a/ipod контейнер не дружит с таким видео.
        """
        duration = max(0.01, end - start)
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(input_audio),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-acodec",
            "aac",
            "-b:a",
            "192k",
            str(output_audio),
        ]
        log.info("Cut audio: %s", output_audio)
        self._run(cmd)

    def extract_or_loop_video(
        self,
        input_video: Path,
        target_duration: float,
        output_video: Path,
        source_duration: float | None = None,
    ) -> None:
        """
        Делаем шот нужной длины:

        - если исходник длиннее/равен таргету — просто обрезаем;
        - если короче — замедляем (time-stretch) через setpts так,
          чтобы исходные кадры растянулись на target_duration.
        """
        if source_duration is None:
            _, _, source_duration = self.probe_video(input_video)

        target_duration = max(0.1, target_duration)

        if source_duration >= target_duration:
            # просто обрезаем
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_video),
                "-t",
                f"{target_duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-an",
                str(output_video),
            ]
            log.info("Make shot (trim): %s", output_video)
        else:
            # замедляем видео, чтобы оно заняло ровно target_duration
            factor = target_duration / max(source_duration, 0.01)
            vf = f"setpts={factor}*PTS"
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_video),
                "-vf",
                vf,
                "-t",
                f"{target_duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-an",
                str(output_video),
            ]
            log.info(
                "Make shot (time-stretch, factor=%.3f): %s", factor, output_video
            )

        self._run(cmd)

    def concat_videos(
        self, inputs: Iterable[Path], output_video: Path, reencode: bool = False
    ) -> None:
        inputs = list(inputs)
        output_video.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_video.parent / (output_video.stem + "_concat.txt")
        with list_file.open("w", encoding="utf-8") as f:
            for p in inputs:
                abs_path = p.resolve().as_posix()
                f.write(f"file '{abs_path}'\n")

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
        ]
        if reencode:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        else:
            cmd += ["-c", "copy"]
        cmd.append(str(output_video))
        log.info("Concat %d videos -> %s", len(inputs), output_video)
        self._run(cmd)

    def concat_audios(self, inputs: Iterable[Path], output_audio: Path) -> None:
        inputs = list(inputs)
        output_audio.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_audio.parent / (output_audio.stem + "_concat.txt")
        with list_file.open("w", encoding="utf-8") as f:
            for p in inputs:
                abs_path = p.resolve().as_posix()
                f.write(f"file '{abs_path}'\n")
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_audio),
        ]
        log.info("Concat audio -> %s", output_audio)
        self._run(cmd)

    def combine_audio_video(
        self, video: Path, audio: Path, output_video: Path
    ) -> None:
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_video),
        ]
        log.info("Mux A+V -> %s", output_video)
        self._run(cmd)

    def compress_video(
        self, input_video: Path, output_video: Path, width: int, height: int
    ) -> None:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_video),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_video),
        ]
        log.info("Compress -> %s", output_video)
        self._run(cmd)

    def burn_subtitles(self, input_video: Path, srt_file: Path, output_video: Path):
        """
        Прожиг обычных SRT-сабов (если когда-нибудь захочешь без анимации).

        Используем libass через filter subtitles.
        force_style='Alignment=5,MarginV=0' даёт центр экрана.
        """
        vf = (
            f"subtitles='{srt_file.as_posix()}':"
            "force_style='Alignment=5,MarginV=0'"
        )
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_video),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-c:a",
            "copy",
            str(output_video),
        ]
        log.info("Burn subtitles (centered) -> %s", output_video)
        self._run(cmd)

    # ---------- word-by-word анимация через drawtext ----------

    def burn_word_animation(
        self,
        input_video: Path,
        cues: List[tuple[float, float, str]],
        output_video: Path,
    ) -> None:
        """
        cues: список (start_sec, end_sec, text),
        где text уже накопительный (слово1; слово1 слово2; ...).

        Строим один большой filtergraph из drawtext:

          drawtext(... enable=between(t,s1,e1)),
          drawtext(... enable=between(t,s2,e2)),...

        Текст центрируем:
          x=(w-text_w)/2
          y=(h-text_h)/2
        """
        if not cues:
            raise RuntimeError("No cues provided for word animation")

        filter_parts = []
        for start, end, text in cues:
            esc = (
                text.replace("\\", "\\\\")
                .replace(":", r"\:")
                .replace("'", r"\'")
            )
            part = (
                f"drawtext=text='{esc}':"
                "fontcolor=white:fontsize=42:"
                "x=(w-text_w)/2:y=(h-text_h)/2:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
            filter_parts.append(part)

        vf = ",".join(filter_parts)

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_video),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-c:a",
            "copy",
            str(output_video),
        ]
        log.info("Burn word animation (drawtext) -> %s", output_video)
        self._run(cmd)
