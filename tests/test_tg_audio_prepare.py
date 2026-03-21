from __future__ import annotations

from pathlib import Path

from services.tg_bot_botapi import audio_prepare as ap


def test_audio_prepare_stops_on_first_under_limit(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src.wav"
    src.write_bytes(b"x")

    sizes = {
        "192k": 6 * 1024 * 1024,
        "160k": 4 * 1024 * 1024,
        "128k": 3 * 1024 * 1024,
    }

    calls: list[str] = []

    def _fake_run_ffmpeg(*, ffmpeg_bin: str, src: Path, dst: Path, bitrate: str) -> None:
        del ffmpeg_bin, src
        calls.append(bitrate)
        dst.write_bytes(b"0" * sizes[bitrate])

    monkeypatch.setattr(ap, "_run_ffmpeg", _fake_run_ffmpeg)

    res = ap.prepare_audio_best_effort(
        src=src,
        work_dir=tmp_path / "out",
        ffmpeg_bin="ffmpeg",
        max_audio_mb=5,
        bitrate_ladder=("192k", "160k", "128k"),
    )

    assert res.bitrate == "160k"
    assert res.under_limit is True
    assert calls == ["192k", "160k"]


def test_audio_prepare_returns_lowest_if_still_over_limit(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src.wav"
    src.write_bytes(b"x")

    sizes = {
        "64k": 2 * 1024 * 1024,
        "32k": int(1.2 * 1024 * 1024),
    }

    def _fake_run_ffmpeg(*, ffmpeg_bin: str, src: Path, dst: Path, bitrate: str) -> None:
        del ffmpeg_bin, src
        dst.write_bytes(b"0" * sizes[bitrate])

    monkeypatch.setattr(ap, "_run_ffmpeg", _fake_run_ffmpeg)

    res = ap.prepare_audio_best_effort(
        src=src,
        work_dir=tmp_path / "out",
        ffmpeg_bin="ffmpeg",
        max_audio_mb=1,
        bitrate_ladder=("64k", "32k"),
    )

    assert res.bitrate == "32k"
    assert res.under_limit is False
    assert res.size_bytes == sizes["32k"]
