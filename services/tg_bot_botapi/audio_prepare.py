from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BITRATE_LADDER: tuple[str, ...] = ("192k", "160k", "128k", "96k", "64k", "48k", "32k")


@dataclass(frozen=True)
class AudioPrepareResult:
    source_path: Path
    output_path: Path
    bitrate: str
    size_bytes: int
    under_limit: bool


def _run_ffmpeg(*, ffmpeg_bin: str, src: Path, dst: Path, bitrate: str) -> None:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        str(bitrate),
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"ffmpeg failed rc={proc.returncode} bitrate={bitrate} stderr_tail={err_tail}")


def _safe_name(name: str) -> str:
    out = []
    for ch in str(name or ""):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "audio"


def _max_bytes(max_mb: int) -> int:
    return int(max(1, int(max_mb)) * 1024 * 1024)


def prepare_audio_best_effort(
    *,
    src: Path,
    work_dir: Path,
    ffmpeg_bin: str,
    max_audio_mb: int,
    bitrate_ladder: Iterable[str] = BITRATE_LADDER,
) -> AudioPrepareResult:
    src = src.expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"audio source missing: {src}")

    target_max = _max_bytes(max_audio_mb)
    work_dir = work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    base = _safe_name(src.stem)
    best_path: Path | None = None
    best_bitrate = ""

    for bitrate in bitrate_ladder:
        out_path = work_dir / f"{base}_{bitrate}.mp3"
        _run_ffmpeg(ffmpeg_bin=ffmpeg_bin, src=src, dst=out_path, bitrate=str(bitrate))
        size = out_path.stat().st_size

        best_path = out_path
        best_bitrate = str(bitrate)

        if size <= target_max:
            return AudioPrepareResult(
                source_path=src,
                output_path=out_path,
                bitrate=str(bitrate),
                size_bytes=size,
                under_limit=True,
            )

    if best_path is None:
        raise RuntimeError("audio prepare failed: no ffmpeg outputs produced")

    final_size = best_path.stat().st_size
    return AudioPrepareResult(
        source_path=src,
        output_path=best_path,
        bitrate=best_bitrate,
        size_bytes=final_size,
        under_limit=final_size <= target_max,
    )
