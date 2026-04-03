from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence


def _safe_ttl_seconds(ttl_s: float, *, min_seconds: float = 1.0) -> float:
    try:
        parsed = float(ttl_s)
    except Exception:
        parsed = min_seconds
    if parsed < min_seconds:
        return min_seconds
    return parsed


def parse_glob_allowlist(raw: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(raw, str):
        parts = [chunk.strip() for chunk in raw.split(",")]
    else:
        parts = [str(chunk or "").strip() for chunk in raw]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part or part in seen:
            continue
        seen.add(part)
        out.append(part)
    return tuple(out)


def _iter_files_under(root: Path, *, max_scan_files: int) -> Iterable[Path]:
    scanned = 0
    for path in root.rglob("*"):
        if scanned >= max_scan_files:
            break
        if not path.is_file():
            continue
        scanned += 1
        yield path


def _remove_empty_dirs(root: Path, *, max_scan_dirs: int) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    removed = 0
    scanned = 0
    # Deepest paths first so parent directories can become empty and be removed.
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for directory in dirs:
        if scanned >= max_scan_dirs:
            break
        scanned += 1
        try:
            directory.rmdir()
            removed += 1
        except OSError:
            continue
    try:
        root.rmdir()
        removed += 1
    except OSError:
        pass
    return removed


def cleanup_tmp_chat_dirs(
    *,
    tmp_root: Path,
    retention_by_subdir_s: Mapping[str, float],
    now_ts: float | None = None,
    max_scan_files: int = 2000,
    max_scan_dirs: int = 500,
) -> dict[str, int]:
    out = {
        "scanned_files": 0,
        "removed_files": 0,
        "removed_dirs": 0,
        "errors": 0,
    }
    root = Path(tmp_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return out

    now = float(now_ts) if now_ts is not None else time.time()
    scanned = 0

    for chat_dir in sorted(root.iterdir(), key=lambda p: p.name):
        if scanned >= max_scan_files:
            break
        if not chat_dir.is_dir():
            continue
        for subdir, ttl_s in retention_by_subdir_s.items():
            if scanned >= max_scan_files:
                break
            target = chat_dir / str(subdir)
            if not target.exists() or not target.is_dir():
                continue
            cutoff = now - _safe_ttl_seconds(float(ttl_s))
            for file_path in _iter_files_under(target, max_scan_files=max(1, max_scan_files - scanned)):
                scanned += 1
                out["scanned_files"] += 1
                try:
                    st = file_path.stat()
                except FileNotFoundError:
                    continue
                except Exception:
                    out["errors"] += 1
                    continue
                if float(st.st_mtime) >= cutoff:
                    continue
                try:
                    file_path.unlink()
                    out["removed_files"] += 1
                except FileNotFoundError:
                    continue
                except Exception:
                    out["errors"] += 1
            out["removed_dirs"] += _remove_empty_dirs(target, max_scan_dirs=max_scan_dirs)
        out["removed_dirs"] += _remove_empty_dirs(chat_dir, max_scan_dirs=max_scan_dirs)

    return out


def cleanup_jobs_artifacts(
    *,
    jobs_roots: Sequence[Path],
    regular_retention_s: float,
    debug_retention_s: float,
    debug_allowlist_patterns: Sequence[str],
    now_ts: float | None = None,
    max_scan_files: int = 4000,
    max_scan_dirs: int = 1000,
) -> dict[str, int]:
    out = {
        "scanned_files": 0,
        "removed_files": 0,
        "removed_dirs": 0,
        "kept_debug_files": 0,
        "errors": 0,
    }
    now = float(now_ts) if now_ts is not None else time.time()
    regular_cutoff = now - _safe_ttl_seconds(float(regular_retention_s))
    debug_cutoff = now - _safe_ttl_seconds(float(debug_retention_s))
    patterns = parse_glob_allowlist(debug_allowlist_patterns)

    scanned = 0
    seen_roots: set[str] = set()
    for raw_root in jobs_roots:
        if scanned >= max_scan_files:
            break
        root = Path(raw_root).expanduser().resolve()
        root_key = str(root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        if not root.exists() or not root.is_dir():
            continue

        for job_dir in sorted(root.iterdir(), key=lambda p: p.name):
            if scanned >= max_scan_files:
                break
            if not job_dir.is_dir():
                continue
            target = job_dir / "out"
            if not target.exists() or not target.is_dir():
                continue

            for file_path in _iter_files_under(target, max_scan_files=max(1, max_scan_files - scanned)):
                scanned += 1
                out["scanned_files"] += 1
                rel_name = str(file_path.relative_to(target)).replace(os.sep, "/")
                is_debug = any(fnmatch.fnmatch(rel_name, p) or fnmatch.fnmatch(file_path.name, p) for p in patterns)
                cutoff = debug_cutoff if is_debug else regular_cutoff
                try:
                    st = file_path.stat()
                except FileNotFoundError:
                    continue
                except Exception:
                    out["errors"] += 1
                    continue
                if float(st.st_mtime) >= cutoff:
                    if is_debug:
                        out["kept_debug_files"] += 1
                    continue
                try:
                    file_path.unlink()
                    out["removed_files"] += 1
                except FileNotFoundError:
                    continue
                except Exception:
                    out["errors"] += 1

            out["removed_dirs"] += _remove_empty_dirs(target, max_scan_dirs=max_scan_dirs)
            out["removed_dirs"] += _remove_empty_dirs(job_dir / "out" / "logs", max_scan_dirs=max_scan_dirs)
            out["removed_dirs"] += _remove_empty_dirs(job_dir, max_scan_dirs=max_scan_dirs)

    return out
