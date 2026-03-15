from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload, FootageStyleRawPayload


_EPS = 1e-6
_MAX_SWITCH_SEC = 4.0
_STYLE_COLOR_ALLOWED = {"dark", "light", "warm", "cold", "neutral"}
_STYLE_MOOD_ALLOWED = {"major", "minor"}
_STYLE_PEOPLE_ALLOWED = {"none", "girls", "guys", "couple", "crowd", "driver"}
_CLIP_ID_RE = re.compile(r"(\d{8,})")


@dataclass(frozen=True)
class FootagePickerDiagnostics:
    genre: str
    tag: str
    target_duration_sec: float
    primary_pool_duration_sec: float
    selected_pool_duration_sec: float
    widened_to_genre: bool
    repeats_used: bool
    deterministic_seed: int
    seed_key: str
    selected_file_names: List[str]


@dataclass(frozen=True)
class FootageIntervalPickerDiagnostics:
    genre: str
    tag: str
    intervals_count: int
    max_interval_sec: float
    primary_pool_count: int
    selected_pool_count: int
    widened_to_genre: bool
    widened_to_global: bool
    repeats_used: bool
    excluded_input_count: int
    selected_excluded_count: int
    exclude_relaxed: bool
    deterministic_seed: int
    seed_key: str
    selected_file_names: List[str]


@dataclass(frozen=True)
class FootageStyleRawAdapterDiagnostics:
    total_assets: int
    metadata_rows_merged: int
    mapped_assets: int
    unmapped_assets: int
    mood_filtered_out: int
    exclude_filtered_out: int
    scored_assets: int
    selected_genre: str
    selected_tag: str
    selected_group_score: float
    selected_group_duration_sec: float
    selected_group_assets_count: int
    top_groups: List[Dict[str, Any]]


def _as_pos_float(v: Any) -> float:
    try:
        x = float(v)
    except Exception as e:
        raise RuntimeError(f"Invalid float value: {v!r}") from e
    if x <= 0:
        raise RuntimeError(f"Expected positive float, got {x!r}")
    return x


def _require_non_empty_str(v: Any, *, field: str) -> str:
    s = str(v or "").strip()
    if not s:
        raise RuntimeError(f"Missing required inventory field: {field}")
    return s


def load_picker_assets_from_inventory(inv: Dict[str, Any]) -> List[Dict[str, Any]]:
    assets_raw = inv.get("assets")
    if not isinstance(assets_raw, list):
        raise RuntimeError("Inventory must contain assets[]")

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for idx, it in enumerate(assets_raw):
        if not isinstance(it, dict):
            raise RuntimeError(f"Inventory assets[{idx}] must be object")

        file_name = _require_non_empty_str(it.get("file_name"), field="file_name")
        genre = _require_non_empty_str(it.get("genre"), field="genre")
        tag = _require_non_empty_str(it.get("tag"), field="tag")
        duration_sec = _as_pos_float(it.get("duration_sec"))
        src_w = int(it.get("src_w") or 0)
        src_h = int(it.get("src_h") or 0)
        if src_w <= 0 or src_h <= 0:
            raise RuntimeError(f"Inventory asset has invalid src size: file_name={file_name!r}")

        # Deterministic de-duplication by file_name.
        if file_name in seen:
            continue
        seen.add(file_name)

        out.append(
            {
                "file_name": file_name,
                "genre": genre,
                "tag": tag,
                "duration_sec": float(duration_sec),
                "src_w": src_w,
                "src_h": src_h,
            }
        )

    if not out:
        raise RuntimeError("No valid assets in inventory for footage picker")
    return out


def build_style_groups_from_assets(assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for it in assets:
        genre = _require_non_empty_str(it.get("genre"), field="genre")
        tag = _require_non_empty_str(it.get("tag"), field="tag")
        dur = _as_pos_float(it.get("duration_sec"))
        key = (genre, tag)
        row = agg.get(key)
        if row is None:
            row = {"genre": genre, "tag": tag, "assets_count": 0, "total_duration_sec": 0.0}
            agg[key] = row
        row["assets_count"] = int(row["assets_count"]) + 1
        row["total_duration_sec"] = float(row["total_duration_sec"]) + float(min(dur, _MAX_SWITCH_SEC))

    out = list(agg.values())
    out.sort(key=lambda x: (str(x["genre"]).lower(), str(x["tag"]).lower()))
    if not out:
        raise RuntimeError("No style groups built from inventory assets")
    return out


def validate_style_pick_in_groups(style_pick: FootageStylePickPayload, style_groups: List[Dict[str, Any]]) -> None:
    pool_keys: set[Tuple[str, str]] = set()
    for it in style_groups:
        if not isinstance(it, dict):
            continue
        g = str(it.get("genre") or "").strip()
        t = str(it.get("tag") or "").strip()
        if g and t:
            pool_keys.add((g, t))
    key = (str(style_pick.genre).strip(), str(style_pick.tag).strip())
    if key not in pool_keys:
        raise RuntimeError(
            "Gemini style pick is not present in style pool: "
            f"genre={style_pick.genre!r} tag={style_pick.tag!r}"
        )


def deterministic_seed_from_key(seed_key: str) -> int:
    s = str(seed_key or "").strip()
    if not s:
        raise RuntimeError("Empty deterministic seed key")
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _duration_sum(pool: List[Dict[str, Any]]) -> float:
    return float(sum(min(float(it["duration_sec"]), _MAX_SWITCH_SEC) for it in pool))


def _deterministic_sort_assets(pool: List[Dict[str, Any]], *, seed_value: int) -> List[Dict[str, Any]]:
    def _sort_key(it: Dict[str, Any]) -> Tuple[str, str]:
        file_name = str(it["file_name"])
        material = f"{seed_value}:{file_name}"
        h = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return h, file_name

    return sorted(pool, key=_sort_key)


def pick_footage_clips_deterministic(
    *,
    style_pick: FootageStylePickPayload,
    assets: List[Dict[str, Any]],
    clip_start_abs: float,
    clip_end_abs: float,
    seed_key: str,
    fit_mode: str = "cover",
) -> Tuple[FootageSelectionPayload, FootagePickerDiagnostics]:
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    target_dur = ce - cs
    if target_dur <= 0:
        raise RuntimeError(f"Invalid clip window: {cs}..{ce}")

    genre = str(style_pick.genre).strip()
    tag = str(style_pick.tag).strip()
    if not genre or not tag:
        raise RuntimeError("Style pick must contain non-empty genre and tag")

    primary_pool = [it for it in assets if str(it["genre"]) == genre and str(it["tag"]) == tag]
    if not primary_pool:
        raise RuntimeError(f"No assets for selected style genre={genre!r} tag={tag!r}")

    primary_total = _duration_sum(primary_pool)
    selected_pool = list(primary_pool)
    widened_to_genre = False

    if primary_total + _EPS < target_dur:
        widen_pool = [it for it in assets if str(it["genre"]) == genre and str(it["tag"]) != tag]
        if widen_pool:
            selected_pool.extend(widen_pool)
            widened_to_genre = True

    selected_pool_total = _duration_sum(selected_pool)
    repeats_used = selected_pool_total + _EPS < target_dur

    seed_value = deterministic_seed_from_key(seed_key)
    ordered_pool = _deterministic_sort_assets(selected_pool, seed_value=seed_value)
    if not ordered_pool:
        raise RuntimeError("Selected style pool is empty after deterministic ordering")

    cursor = cs
    idx = 0
    clips: List[Dict[str, Any]] = []
    selected_file_names: List[str] = []

    while cursor < ce - _EPS:
        if idx >= len(ordered_pool):
            if not repeats_used:
                raise RuntimeError("Insufficient selected pool duration and repeats are disabled")
            idx = 0

        asset = ordered_pool[idx]
        idx += 1
        file_dur = float(asset["duration_sec"])
        if file_dur <= _EPS:
            raise RuntimeError(f"Non-positive asset duration: {asset['file_name']!r}")
        slot_dur = min(file_dur, _MAX_SWITCH_SEC)
        if slot_dur <= _EPS:
            raise RuntimeError(f"Non-positive effective slot duration: {asset['file_name']!r}")

        remaining = ce - cursor
        if remaining <= slot_dur + _EPS:
            out_point = ce
        else:
            out_point = cursor + slot_dur

        if out_point <= cursor + _EPS:
            raise RuntimeError(f"Failed to allocate positive clip duration for {asset['file_name']!r}")

        clips.append(
            {
                "file_name": str(asset["file_name"]),
                "fit_mode": fit_mode,
                "in_point": float(cursor),
                "out_point": float(out_point),
                "start_time": float(cursor),
            }
        )
        selected_file_names.append(str(asset["file_name"]))
        cursor = float(out_point)

    payload = FootageSelectionPayload.model_validate({"clips": clips, "allow_gaps": False})
    diagnostics = FootagePickerDiagnostics(
        genre=genre,
        tag=tag,
        target_duration_sec=float(target_dur),
        primary_pool_duration_sec=float(primary_total),
        selected_pool_duration_sec=float(selected_pool_total),
        widened_to_genre=bool(widened_to_genre),
        repeats_used=bool(repeats_used),
        deterministic_seed=int(seed_value),
        seed_key=str(seed_key),
        selected_file_names=selected_file_names,
    )
    return payload, diagnostics


def build_intervals_from_switch_points(
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    switch_points_abs: List[float],
) -> List[Tuple[float, float]]:
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    if ce <= cs + _EPS:
        raise RuntimeError(f"Invalid clip window: {cs}..{ce}")

    pts = [float(x) for x in switch_points_abs]
    prev = cs
    for idx, p in enumerate(pts):
        if p <= cs + _EPS or p >= ce - _EPS:
            raise RuntimeError(f"switch_points_abs[{idx}] outside clip window: {p}")
        if p <= prev + _EPS:
            raise RuntimeError("switch_points_abs must be strictly increasing")
        prev = p

    bounds = [cs] + pts + [ce]
    intervals: List[Tuple[float, float]] = []
    for i in range(len(bounds) - 1):
        a = float(bounds[i])
        b = float(bounds[i + 1])
        if b <= a + _EPS:
            raise RuntimeError(f"Non-positive interval at index={i}: {a}..{b}")
        intervals.append((a, b))
    return intervals


def _fits_interval(asset: Dict[str, Any], *, interval_len: float) -> bool:
    try:
        dur = float(asset["duration_sec"])
    except Exception:
        return False
    return dur + _EPS >= float(interval_len)


def _deterministic_choose(
    *,
    candidates: List[Dict[str, Any]],
    seed_value: int,
    interval_idx: int,
    interval_start: float,
    avoid_file_name: str | None = None,
) -> Dict[str, Any]:
    if not candidates:
        raise RuntimeError("deterministic choose candidates is empty")

    def _sort_key(it: Dict[str, Any]) -> Tuple[str, str]:
        file_name = str(it["file_name"])
        material = f"{seed_value}:{interval_idx}:{interval_start:.6f}:{file_name}"
        h = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return h, file_name

    ranked = sorted(candidates, key=_sort_key)
    avoid = str(avoid_file_name or "").strip()
    if avoid and len(ranked) > 1:
        for it in ranked:
            if str(it.get("file_name") or "") != avoid:
                return it
    return ranked[0]


def _dedupe_assets_by_file_name(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for it in pool:
        name = str(it.get("file_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(it)
    return out


def _deterministic_file_name_order(
    *,
    file_names: List[str],
    seed_value: int,
    interval_idx: int,
    interval_start: float,
) -> List[str]:
    def _key(name: str) -> Tuple[str, str]:
        material = f"{seed_value}:{interval_idx}:{interval_start:.6f}:{name}"
        h = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return h, name

    return sorted(file_names, key=_key)


def _assign_unique_file_names_for_intervals(
    *,
    intervals: List[Tuple[float, float]],
    pool: List[Dict[str, Any]],
    seed_value: int,
) -> List[str]:
    if not intervals:
        raise RuntimeError("No intervals were built from switch points")
    by_name: Dict[str, Dict[str, Any]] = {}
    for it in pool:
        name = str(it.get("file_name") or "").strip()
        if name:
            by_name[name] = it
    if len(by_name) < len(intervals):
        raise RuntimeError(
            "insufficient unique assets for strict no-repeat policy: "
            f"need={len(intervals)} have={len(by_name)}"
        )

    candidates: List[List[str]] = []
    for idx, (a, b) in enumerate(intervals):
        need = float(b - a)
        names = [n for n, it in by_name.items() if _fits_interval(it, interval_len=need)]
        if not names:
            raise RuntimeError(
                "no asset can cover interval for strict no-repeat policy: "
                f"idx={idx} interval={a:.3f}..{b:.3f} dur={need:.3f}"
            )
        candidates.append(
            _deterministic_file_name_order(
                file_names=names,
                seed_value=seed_value,
                interval_idx=idx,
                interval_start=float(a),
            )
        )

    order = sorted(range(len(intervals)), key=lambda i: (len(candidates[i]), i))
    matched_name_to_interval: Dict[str, int] = {}

    def _try_match(interval_idx: int, seen_names: set[str]) -> bool:
        for nm in candidates[interval_idx]:
            if nm in seen_names:
                continue
            seen_names.add(nm)
            prev_interval = matched_name_to_interval.get(nm)
            if prev_interval is None or _try_match(prev_interval, seen_names):
                matched_name_to_interval[nm] = interval_idx
                return True
        return False

    for i in order:
        if not _try_match(i, set()):
            raise RuntimeError(
                "cannot assign unique assets to all intervals under strict no-repeat policy"
            )

    out = [""] * len(intervals)
    for nm, i in matched_name_to_interval.items():
        out[i] = nm
    if any(not x for x in out):
        raise RuntimeError("internal matching failure for strict no-repeat policy")
    return out


def pick_footage_clips_by_intervals_deterministic(
    *,
    style_pick: FootageStylePickPayload,
    assets: List[Dict[str, Any]],
    clip_start_abs: float,
    clip_end_abs: float,
    switch_points_abs: List[float],
    seed_key: str,
    fit_mode: str = "cover",
    exclude_file_names: List[str] | None = None,
) -> Tuple[FootageSelectionPayload, FootageIntervalPickerDiagnostics]:
    genre = str(style_pick.genre).strip()
    tag = str(style_pick.tag).strip()
    if not genre or not tag:
        raise RuntimeError("Style pick must contain non-empty genre and tag")

    intervals = build_intervals_from_switch_points(
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        switch_points_abs=switch_points_abs,
    )
    if not intervals:
        raise RuntimeError("No intervals were built from switch points")

    primary_pool = [it for it in assets if str(it["genre"]) == genre and str(it["tag"]) == tag]
    if not primary_pool:
        raise RuntimeError(f"No assets for selected style genre={genre!r} tag={tag!r}")

    widened_to_genre = False
    widened_to_global = False
    seed_value = deterministic_seed_from_key(seed_key)

    selected_pool_all = _dedupe_assets_by_file_name(list(primary_pool))
    excluded_names = {str(x).strip() for x in list(exclude_file_names or []) if str(x).strip()}
    selected_pool = [it for it in selected_pool_all if str(it.get("file_name") or "") not in excluded_names]
    assignment_err: str | None = None
    exclude_relaxed = False

    def _try_assign(pool: List[Dict[str, Any]]) -> List[str] | None:
        nonlocal assignment_err
        try:
            return _assign_unique_file_names_for_intervals(
                intervals=intervals,
                pool=pool,
                seed_value=seed_value,
            )
        except RuntimeError as e:
            assignment_err = str(e)
            return None

    assigned_file_names = _try_assign(selected_pool)

    if assigned_file_names is None:
        widen_pool = [it for it in assets if str(it["genre"]) == genre and str(it["tag"]) != tag]
        if widen_pool:
            selected_pool_all = _dedupe_assets_by_file_name(selected_pool_all + widen_pool)
            selected_pool = [it for it in selected_pool_all if str(it.get("file_name") or "") not in excluded_names]
            widened_to_genre = True
            assigned_file_names = _try_assign(selected_pool)

    if assigned_file_names is None:
        global_pool = [it for it in assets if str(it["genre"]) != genre]
        if global_pool:
            selected_pool_all = _dedupe_assets_by_file_name(selected_pool_all + global_pool)
            selected_pool = [it for it in selected_pool_all if str(it.get("file_name") or "") not in excluded_names]
            widened_to_global = True
            assigned_file_names = _try_assign(selected_pool)

    if assigned_file_names is None and excluded_names:
        exclude_relaxed = True
        assigned_file_names = _try_assign(selected_pool_all)

    by_name = {str(it["file_name"]): it for it in selected_pool_all}
    clips: List[Dict[str, Any]] = []
    repeats_used = False

    if assigned_file_names is None:
        repeats_used = True
        prev_file_name: str | None = None
        pool_for_repeats = selected_pool
        if not pool_for_repeats and excluded_names:
            exclude_relaxed = True
            pool_for_repeats = selected_pool_all
        for idx, (a, b) in enumerate(intervals):
            need = float(b - a)
            candidates = [it for it in pool_for_repeats if _fits_interval(it, interval_len=need)]
            if not candidates and excluded_names and not exclude_relaxed:
                exclude_relaxed = True
                pool_for_repeats = selected_pool_all
                candidates = [it for it in pool_for_repeats if _fits_interval(it, interval_len=need)]
            if not candidates:
                raise RuntimeError(
                    "No footage asset can cover interval after pool enrichment "
                    f"(idx={idx}, interval={a:.3f}..{b:.3f}, dur={need:.3f})"
                )
            chosen = _deterministic_choose(
                candidates=candidates,
                seed_value=seed_value,
                interval_idx=idx,
                interval_start=float(a),
                avoid_file_name=prev_file_name,
            )
            chosen_name = str(chosen["file_name"])
            clips.append(
                {
                    "file_name": chosen_name,
                    "fit_mode": fit_mode,
                    "in_point": float(a),
                    "out_point": float(b),
                    "start_time": float(a),
                }
            )
            prev_file_name = chosen_name
            assigned_file_names = [str(c["file_name"]) for c in clips]
    else:
        for idx, (a, b) in enumerate(intervals):
            chosen_name = str(assigned_file_names[idx])
            if chosen_name not in by_name:
                raise RuntimeError(f"assigned file_name not present in selected pool: {chosen_name!r}")
            clips.append(
                {
                    "file_name": chosen_name,
                    "fit_mode": fit_mode,
                    "in_point": float(a),
                    "out_point": float(b),
                    "start_time": float(a),
                }
            )

    selected_excluded_count = 0
    if excluded_names:
        selected_excluded_count = sum(1 for x in assigned_file_names if str(x) in excluded_names)

    payload = FootageSelectionPayload.model_validate({"clips": clips, "allow_gaps": False})
    diag = FootageIntervalPickerDiagnostics(
        genre=genre,
        tag=tag,
        intervals_count=len(intervals),
        max_interval_sec=max(float(b - a) for a, b in intervals),
        primary_pool_count=len(primary_pool),
        selected_pool_count=len(selected_pool_all),
        widened_to_genre=bool(widened_to_genre),
        widened_to_global=bool(widened_to_global),
        repeats_used=bool(repeats_used),
        excluded_input_count=len(excluded_names),
        selected_excluded_count=int(selected_excluded_count),
        exclude_relaxed=bool(exclude_relaxed),
        deterministic_seed=int(seed_value),
        seed_key=str(seed_key),
        selected_file_names=[str(x) for x in assigned_file_names],
    )
    return payload, diag


def _extract_clip_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    m = _CLIP_ID_RE.search(raw)
    if not m:
        return None
    out = str(m.group(1) or "").strip()
    return out or None


def _normalize_theme_tag(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def _normalize_people_type(v: Any) -> str:
    out = _normalize_theme_tag(v)
    if out == "guy":
        out = "guys"
    if out not in _STYLE_PEOPLE_ALLOWED:
        return ""
    return out


def _normalize_color_tone(v: Any) -> str:
    out = _normalize_theme_tag(v)
    if out not in _STYLE_COLOR_ALLOWED:
        return ""
    return out


def _normalize_mood(v: Any) -> str:
    out = _normalize_theme_tag(v)
    if out not in _STYLE_MOOD_ALLOWED:
        return ""
    return out


def load_footage_style_metadata_rows(
    *,
    db_paths: List[Path],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in list(db_paths or []):
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Style metadata db missing: {p}")
        obj = json.loads(p.read_text(encoding="utf-8"))
        items = obj if isinstance(obj, list) else (obj.get("items") or obj.get("videos") or obj.get("assets") or [])
        if not isinstance(items, list):
            raise RuntimeError(f"Style metadata db root must contain list rows: {p}")
        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            clip_id = _extract_clip_id(it.get("video_key")) or _extract_clip_id(it.get("video_path"))
            if not clip_id:
                continue
            mood = _normalize_mood(it.get("mood"))
            color_tone = _normalize_color_tone(it.get("color_tone"))
            people_type = _normalize_people_type(it.get("people_type"))
            tags_seen: set[str] = set()
            tags: List[str] = []
            for t in list(it.get("theme_tags") or []):
                tv = _normalize_theme_tag(t)
                if tv and tv not in tags_seen:
                    tags_seen.add(tv)
                    tags.append(tv)
            rows.append(
                {
                    "clip_id": clip_id,
                    "mood": mood,
                    "color_tone": color_tone,
                    "people_type": people_type or "none",
                    "theme_tags": tags,
                    "source_path": str(p),
                    "source_row": int(idx),
                }
            )
    return rows


def merge_footage_style_metadata_rows(
    rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        clip_id = str(row.get("clip_id") or "").strip()
        if not clip_id:
            continue
        current = merged.get(clip_id)
        if current is None:
            current = {
                "clip_id": clip_id,
                "mood": str(row.get("mood") or "").strip(),
                "color_tone": str(row.get("color_tone") or "").strip(),
                "people_type": str(row.get("people_type") or "").strip() or "none",
                "theme_tags": [],
            }
            merged[clip_id] = current
        else:
            if not str(current.get("mood") or "").strip():
                current["mood"] = str(row.get("mood") or "").strip()
            if not str(current.get("color_tone") or "").strip():
                current["color_tone"] = str(row.get("color_tone") or "").strip()
            if str(current.get("people_type") or "").strip() in {"", "none"}:
                cand_people = str(row.get("people_type") or "").strip()
                if cand_people:
                    current["people_type"] = cand_people
        seen = {str(x).strip() for x in list(current.get("theme_tags") or []) if str(x).strip()}
        for t in list(row.get("theme_tags") or []):
            tv = str(t).strip()
            if tv and tv not in seen:
                seen.add(tv)
                current.setdefault("theme_tags", []).append(tv)
        current["theme_tags"] = list(current.get("theme_tags") or [])
    return merged


def map_inventory_assets_with_style_metadata(
    *,
    assets: List[Dict[str, Any]],
    metadata_index: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    mapped: List[Dict[str, Any]] = []
    unmapped: List[str] = []
    for it in list(assets or []):
        if not isinstance(it, dict):
            continue
        file_name = str(it.get("file_name") or "").strip()
        if not file_name:
            continue
        clip_id = _extract_clip_id(file_name)
        meta = metadata_index.get(str(clip_id or "").strip()) if clip_id else None
        if not isinstance(meta, dict):
            unmapped.append(file_name)
            continue
        mapped.append(
            {
                **it,
                "clip_id": str(clip_id),
                "meta_mood": str(meta.get("mood") or "").strip(),
                "meta_color_tone": str(meta.get("color_tone") or "").strip(),
                "meta_people_type": str(meta.get("people_type") or "").strip() or "none",
                "meta_theme_tags": list(meta.get("theme_tags") or []),
            }
        )
    return mapped, unmapped


def resolve_style_pick_from_raw_filters(
    *,
    raw_pick: FootageStyleRawPayload,
    mapped_assets: List[Dict[str, Any]],
    seed_key: str,
    total_assets: int | None = None,
    unmapped_assets: int = 0,
    metadata_rows_merged: int = 0,
) -> Tuple[FootageStylePickPayload, FootageStyleRawAdapterDiagnostics]:
    total = int(total_assets if total_assets is not None else len(list(mapped_assets or [])))
    if total <= 0:
        raise RuntimeError("No mapped assets available for raw Stage2B adapter")

    mood = _normalize_mood(raw_pick.mood)
    candidates_mood = [it for it in mapped_assets if _normalize_mood(it.get("meta_mood")) == mood]
    if not candidates_mood:
        raise RuntimeError(f"No mapped assets match mood={mood!r}")

    exclude_set = {_normalize_people_type(x) for x in list(raw_pick.filters.exclude or [])}
    exclude_set.discard("")
    candidates_people = [
        it for it in candidates_mood if _normalize_people_type(it.get("meta_people_type")) not in exclude_set
    ]
    if not candidates_people:
        raise RuntimeError(
            f"No mapped assets remain after people exclusion for mood={mood!r}, exclude={sorted(exclude_set)!r}"
        )

    priority_tags = {_normalize_theme_tag(x) for x in list(raw_pick.filters.priority_theme_tags or [])}
    priority_tags.discard("")
    color_priority = {_normalize_color_tone(x) for x in list(raw_pick.filters.color_priority or [])}
    color_priority.discard("")

    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for it in candidates_people:
        genre = str(it.get("genre") or "").strip()
        tag = str(it.get("tag") or "").strip()
        if not genre or not tag:
            continue
        tags = {_normalize_theme_tag(x) for x in list(it.get("meta_theme_tags") or [])}
        tags.discard("")
        overlap = int(len(priority_tags.intersection(tags)))
        color_hit = 1 if _normalize_color_tone(it.get("meta_color_tone")) in color_priority else 0
        score = float(overlap * 100 + color_hit * 15)
        key = (genre, tag)
        row = grouped.get(key)
        if row is None:
            row = {
                "genre": genre,
                "tag": tag,
                "score": 0.0,
                "duration": 0.0,
                "assets_count": 0,
                "overlap_sum": 0,
                "color_hits": 0,
            }
            grouped[key] = row
        row["score"] = float(row["score"]) + float(score)
        row["duration"] = float(row["duration"]) + float(min(float(it.get("duration_sec") or 0.0), _MAX_SWITCH_SEC))
        row["assets_count"] = int(row["assets_count"]) + 1
        row["overlap_sum"] = int(row["overlap_sum"]) + int(overlap)
        row["color_hits"] = int(row["color_hits"]) + int(color_hit)

    if not grouped:
        raise RuntimeError("No valid genre/tag groups produced after raw Stage2B scoring")

    seed_value = deterministic_seed_from_key(seed_key)

    def _tie_hash(genre: str, tag: str) -> str:
        material = f"{seed_value}:{genre}:{tag}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    rows = list(grouped.values())
    rows.sort(
        key=lambda r: (
            -float(r["score"]),
            -float(r["duration"]),
            -int(r["assets_count"]),
            _tie_hash(str(r["genre"]), str(r["tag"])),
            str(r["genre"]),
            str(r["tag"]),
        )
    )
    best = rows[0]
    pick = FootageStylePickPayload.model_validate(
        {"genre": str(best["genre"]), "tag": str(best["tag"])}
    )

    diag = FootageStyleRawAdapterDiagnostics(
        total_assets=int(total),
        metadata_rows_merged=int(metadata_rows_merged),
        mapped_assets=int(len(mapped_assets)),
        unmapped_assets=int(unmapped_assets),
        mood_filtered_out=int(total - len(candidates_mood)),
        exclude_filtered_out=int(len(candidates_mood) - len(candidates_people)),
        scored_assets=int(len(candidates_people)),
        selected_genre=str(best["genre"]),
        selected_tag=str(best["tag"]),
        selected_group_score=float(best["score"]),
        selected_group_duration_sec=float(best["duration"]),
        selected_group_assets_count=int(best["assets_count"]),
        top_groups=[
            {
                "genre": str(r["genre"]),
                "tag": str(r["tag"]),
                "score": float(r["score"]),
                "duration_sec": float(r["duration"]),
                "assets_count": int(r["assets_count"]),
                "overlap_sum": int(r["overlap_sum"]),
                "color_hits": int(r["color_hits"]),
            }
            for r in rows[:10]
        ],
    )
    return pick, diag
