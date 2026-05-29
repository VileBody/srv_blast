# mlcore/hooks/f5_cognition/__main__.py
"""
CLI smoke-test для ручного запуска F5.

Usage:
    python -m mlcore.hooks.f5_cognition generate \\
        --track path/to/track.mp3 \\
        --focal-ms 18000 \\
        --device question_to_track \\
        --lyrics-file lyrics.txt \\
        [--out out.wav]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from mlcore.hooks.f5_cognition.models import F5Device, F5Request, TrackMeta
from mlcore.hooks.f5_cognition.pipeline import generate


def _read_lyrics(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cmd_generate(args: argparse.Namespace) -> int:
    lyrics = _read_lyrics(args.lyrics_file)

    meta = TrackMeta(
        bpm=args.bpm,
        key=args.key,
        genre=args.genre,
        artist=args.artist,
    )
    req = F5Request(
        track_path=args.track,
        lyrics=lyrics,
        track_meta=meta,
        focal_start_ms=args.focal_ms,
        device=F5Device(args.device),
        drop_at_sec=args.drop_at_sec,
        seed=args.seed,
    )
    resp = generate(req, output_path=args.out)

    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mlcore.hooks.f5_cognition")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Сгенерировать F5-вставку")
    g.add_argument("--track", required=True, help="Путь к mp3/wav трека")
    g.add_argument("--focal-ms", type=int, required=True, help="Старт фокусного отрывка, мс")
    g.add_argument(
        "--device", required=True,
        choices=[d.value for d in F5Device],
        help="Какое устройство применить",
    )
    g.add_argument("--lyrics-file", required=True, help="Файл с лирикой (utf-8)")
    g.add_argument("--out", default=None, help="Куда сохранить .wav (опционально)")
    g.add_argument("--bpm", type=float, default=None)
    g.add_argument("--key", default=None)
    g.add_argument("--genre", default=None)
    g.add_argument("--artist", default=None)
    g.add_argument("--drop-at-sec", type=float, default=None)
    g.add_argument("--seed", type=int, default=None)
    g.add_argument("--verbose", "-v", action="store_true")
    g.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
