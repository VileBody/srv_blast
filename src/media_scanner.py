from __future__ import annotations

import json
import logging
import math
import subprocess
from pathlib import Path
from typing import Iterable, Tuple

log = logging.getLogger(__name__)


class FFmpegExecutor:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe"):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin

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
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
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
            "-acodec",
            "aac",
            "-b:a",
            "192k",
            str(output_audio),
        ]
        log.info("Cut audio: %s", output_audio)
        subprocess.run(cmd, check=True)

    def extract_or_loop_video(
        self,
        input_video: Path,
        target_duration: float,
        output_video: Path,
        source_duration: float | None = None,
    ) -> None:
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
        else:
            # залупляем видео, пока не наберём длительность
            loops = math.ceil(target_duration / source_duration) - 1
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-stream_loop",
                str(loops),
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
        log.info("Make shot: %s", output_video)
        subprocess.run(cmd, check=True)

    def concat_videos(
        self, inputs: Iterable[Path], output_video: Path, reencode: bool = False
    ) -> None:
        inputs = list(inputs)
        output_video.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_video.parent / (output_video.stem + "_concat.txt")
        with list_file.open("w", encoding="utf-8") as f:
            for p in inputs:
                f.write(f"file '{p.as_posix()}'\n")

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
        subprocess.run(cmd, check=True)

    def concat_audios(self, inputs: Iterable[Path], output_audio: Path) -> None:
        inputs = list(inputs)
        list_file = output_audio.parent / (output_audio.stem + "_concat.txt")
        with list_file.open("w", encoding="utf-8") as f:
            for p in inputs:
                f.write(f"file '{p.as_posix()}'\n")
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
        subprocess.run(cmd, check=True)

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
        subprocess.run(cmd, check=True)

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
        subprocess.run(cmd, check=True)

    def burn_subtitles(self, input_video: Path, srt_file: Path, output_video: Path):
        # путь к сабам нужно экранировать для ffmpeg
        vf = f"subtitles='{srt_file.as_posix()}'"
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
        log.info("Burn subtitles -> %s", output_video)
        subprocess.run(cmd, check=True)
