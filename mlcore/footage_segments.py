"""Virtual segmentation: expose ONE long source file as N pickable clips.

The picker enforces a strict no-repeat policy keyed on ``file_name``, so a single
five-minute upload could only ever contribute ONE cut to a video. Physically
cutting the file at ingest would fix that, but it costs a re-encode, doubles the
stored bytes and throws away the fact that the render stack can already play an
arbitrary window of a source (``source_offset_sec`` → a negative AE layer
``startTime``).

So we cut the INVENTORY, not the file. One long asset becomes N rows with
distinct ``file_name``s that all point at the same ``file_path``; each row
carries the offset of its window (``segment_base_sec``) and the real name to
fetch (``media_file_name``). Everything downstream that reasons about identity —
no-repeat matching, dedup, cooldown, quality band — sees N independent clips,
while the media layer sees one file and downloads it once.

Boundaries snap to scene cuts when the ingest detected them. A blind grid will
sooner or later put a boundary across an edit, and the resulting clip contains
half of one shot and half of the next — the one artefact a viewer reads instantly
as "broken", so it is worth the one-off detection pass.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

# Marker inserted into the virtual file_name. Identity only — nothing in the
# render path parses it back (the real name travels explicitly as
# `media_file_name`), so a real file that happens to contain it is harmless.
SEGMENT_MARKER = "~seg"
_SEGMENT_RE = re.compile(rf"^(?P<base>.+){re.escape(SEGMENT_MARKER)}(?P<idx>\d+)$")

# A segment must comfortably exceed the longest interval the picker can ask for
# (footage_picker._MAX_SWITCH_SEC = 4.0). If it only just covered it, the random
# source offset would have nowhere to move and every job would open on the same
# frame — the uniqueness the offset exists to provide would silently vanish.
MIN_SEGMENT_SEC = 6.0

DEFAULT_SEGMENT_SEC = 20.0
DEFAULT_MIN_SOURCE_SEC = 60.0
# How far a boundary may travel to land on a scene cut.
DEFAULT_SNAP_WINDOW_SEC = 4.0


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    raw = str(os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"{key} must be a number, got {raw!r}") from e


def segment_sec() -> float:
    return _env_float("FOOTAGE_SEGMENT_SEC", DEFAULT_SEGMENT_SEC)


def min_source_sec() -> float:
    return _env_float("FOOTAGE_SEGMENT_MIN_SOURCE_SEC", DEFAULT_MIN_SOURCE_SEC)


def make_segment_name(file_name: str, index: int) -> str:
    """`clip.mp4`, 3 -> `clip~seg03.mp4` (extension preserved: AE and the media
    fetcher both key behaviour off it)."""
    name = str(file_name or "")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return f"{name}{SEGMENT_MARKER}{index:02d}"
    return f"{stem}{SEGMENT_MARKER}{index:02d}.{ext}"


def parse_segment_name(file_name: str) -> Tuple[str, int] | None:
    """Inverse of `make_segment_name`, for diagnostics/logs only."""
    name = str(file_name or "")
    stem, dot, ext = name.rpartition(".")
    core = stem if dot else name
    m = _SEGMENT_RE.match(core)
    if not m:
        return None
    base = m.group("base")
    return (f"{base}.{ext}" if dot else base), int(m.group("idx"))


def segment_bounds(
    duration_sec: float,
    *,
    target_sec: float,
    scene_cuts: Sequence[float] = (),
    snap_window_sec: float = DEFAULT_SNAP_WINDOW_SEC,
    min_segment_sec: float = MIN_SEGMENT_SEC,
) -> List[Tuple[float, float]]:
    """Split [0, duration) into consecutive windows of about `target_sec`.

    Interior boundaries snap to the nearest scene cut within `snap_window_sec`.
    A trailing remainder shorter than `min_segment_sec` is absorbed into the
    previous window rather than emitted — a 2-second tail cannot cover an
    interval and would only ever be dead weight in the pool.
    """
    dur = _f(duration_sec)
    target = max(_f(target_sec), min_segment_sec)
    if dur < min_segment_sec:
        return []
    if dur < 2 * min_segment_sec:
        return [(0.0, dur)]

    cuts = sorted({_f(c) for c in scene_cuts if 0.0 < _f(c) < dur})

    def _snap(t: float) -> float:
        if not cuts:
            return t
        nearest = min(cuts, key=lambda c: abs(c - t))
        return nearest if abs(nearest - t) <= snap_window_sec else t

    bounds: List[float] = [0.0]
    while True:
        nxt = _snap(bounds[-1] + target)
        # Snapping must never move a boundary backwards past its predecessor.
        if nxt - bounds[-1] < min_segment_sec:
            nxt = bounds[-1] + target
        if dur - nxt < min_segment_sec:
            break
        bounds.append(nxt)
    bounds.append(dur)
    return [(round(a, 3), round(b, 3)) for a, b in zip(bounds, bounds[1:])]


def expand_asset_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_sec: float | None = None,
    min_source: float | None = None,
    scene_cuts_by_file: Mapping[str, Sequence[float]] | None = None,
) -> List[Dict[str, Any]]:
    """Expand long assets into virtual segment rows; pass short ones through.

    Idempotent on rows that are already segments, so an inventory rebuild over an
    expanded index cannot compound.
    """
    tgt = segment_sec() if target_sec is None else float(target_sec)
    floor = min_source_sec() if min_source is None else float(min_source)
    cuts_by_file = dict(scene_cuts_by_file or {})

    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        name = str(item.get("file_name") or "").strip()
        duration = _f(item.get("duration_sec"))
        if not name or item.get("segment_base_sec") is not None or duration < floor:
            out.append(item)
            continue

        windows = segment_bounds(
            duration, target_sec=tgt, scene_cuts=cuts_by_file.get(name, ())
        )
        if len(windows) <= 1:
            out.append(item)
            continue

        for idx, (start, end) in enumerate(windows):
            seg = dict(item)
            seg["file_name"] = make_segment_name(name, idx)
            # The real object to fetch. Explicit rather than re-derived from the
            # virtual name, so nothing downstream has to trust a string pattern.
            seg["media_file_name"] = str(item.get("media_file_name") or name)
            seg["duration_sec"] = round(end - start, 3)
            seg["segment_base_sec"] = round(start, 3)
            out.append(seg)
    return out


def media_file_name(asset: Mapping[str, Any]) -> str:
    """The name the media layer should fetch/import this asset under."""
    return str(asset.get("media_file_name") or asset.get("file_name") or "").strip()
