from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload


_EPS = 1e-6
_MAX_SWITCH_SEC = 4.0


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
