#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fill `duration_sec` inside descriptions/*.json options[] by probing local files in footage/.

Why:
- workers can run without ffprobe available
- inventory generation becomes deterministic (duration comes from descriptors)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
_MEDIA_EXTS = _AUDIO_EXTS | _VIDEO_EXTS


def _read_json(p: Path) -> Dict[str, Any]:
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object: {p}")
    return obj


def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ffprobe_duration_sec(*, ffprobe_bin: str, media_path: Path) -> Optional[float]:
    if not media_path.exists():
        return None
    try:
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None
        s = (proc.stdout or "").strip()
        if not s:
            return None
        v = float(s)
        if v <= 0:
            return None
        return v
    except Exception:
        return None


def _is_media_filename(fn: str) -> bool:
    return Path(fn).suffix.lower() in _MEDIA_EXTS


def _iter_desc_files(desc_dir: Path) -> List[Path]:
    if not desc_dir.exists():
        return []
    return [p for p in sorted(desc_dir.rglob("*.json")) if p.is_file()]


def _patch_one(
    *,
    desc_path: Path,
    footage_dir: Path,
    ffprobe_bin: str,
    precision: int,
) -> Tuple[bool, List[str]]:
    d = _read_json(desc_path)
    opts = d.get("options")
    if not isinstance(opts, list) or not opts:
        return False, []

    changed = False
    problems: List[str] = []

    for i, opt in enumerate(opts):
        if not isinstance(opt, dict):
            continue
        fn = str(opt.get("file") or "").strip()
        if not fn:
            continue
        if not _is_media_filename(fn):
            continue

        media_path = (footage_dir / fn).resolve()
        dur = _ffprobe_duration_sec(ffprobe_bin=ffprobe_bin, media_path=media_path)
        if dur is None:
            problems.append(f"{desc_path}: option[{i}] file={fn!r} probe_failed path={str(media_path)!r}")
            continue

        dur2 = round(float(dur), int(precision))
        prev = opt.get("duration_sec")
        if prev is None or (isinstance(prev, (int, float)) and float(prev) != float(dur2)) or (not isinstance(prev, (int, float))):
            opt["duration_sec"] = dur2
            changed = True

    if changed:
        d["options"] = opts
        _write_json(desc_path, d)

    return changed, problems


def main() -> int:
    ap = argparse.ArgumentParser("fill_description_durations.py")
    ap.add_argument("--descriptions-dir", default="descriptions", help="Directory with description json files")
    ap.add_argument("--footage-dir", default="footage", help="Directory with local media files")
    ap.add_argument("--ffprobe-bin", default="ffprobe", help="ffprobe binary name/path")
    ap.add_argument("--precision", type=int, default=3, help="Round duration_sec to N decimals")
    ap.add_argument("--strict", action="store_true", help="Fail (exit 2) if any file can't be probed")
    ap.add_argument("--dry-run", action="store_true", help="Do not write, only report what would change")
    args = ap.parse_args()

    desc_dir = Path(args.descriptions_dir).expanduser().resolve()
    footage_dir = Path(args.footage_dir).expanduser().resolve()

    files = _iter_desc_files(desc_dir)
    if not files:
        print(f"[ERR] no description json files in: {desc_dir}")
        return 2
    if not footage_dir.exists():
        print(f"[ERR] footage dir not found: {footage_dir}")
        return 2

    changed_n = 0
    problems_all: List[str] = []

    for p in files:
        if args.dry_run:
            # simulate by patching in memory only
            d = _read_json(p)
            opts = d.get("options")
            if not isinstance(opts, list) or not opts:
                continue
            would_change = False
            for opt in opts:
                if not isinstance(opt, dict):
                    continue
                fn = str(opt.get("file") or "").strip()
                if not fn or not _is_media_filename(fn):
                    continue
                media_path = (footage_dir / fn).resolve()
                dur = _ffprobe_duration_sec(ffprobe_bin=args.ffprobe_bin, media_path=media_path)
                if dur is None:
                    continue
                dur2 = round(float(dur), int(args.precision))
                prev = opt.get("duration_sec")
                if prev is None or not isinstance(prev, (int, float)) or float(prev) != float(dur2):
                    would_change = True
                    break
            if would_change:
                changed_n += 1
            continue

        changed, probs = _patch_one(
            desc_path=p,
            footage_dir=footage_dir,
            ffprobe_bin=args.ffprobe_bin,
            precision=int(args.precision),
        )
        if changed:
            changed_n += 1
        problems_all.extend(probs)

    print(f"[ok] descriptions scanned: {len(files)}")
    if args.dry_run:
        print(f"[ok] would change files: {changed_n}")
    else:
        print(f"[ok] changed files: {changed_n}")

    if problems_all:
        print(f"[warn] probe problems: {len(problems_all)}")
        for s in problems_all[:50]:
            print(f"  - {s}")
        if len(problems_all) > 50:
            print("  - ... (truncated)")
        if args.strict:
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

