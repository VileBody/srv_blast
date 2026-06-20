from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config.styles.artist_presets_loader import find_preset_by_artist_id
from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload, FootageStyleRawPayload


_EPS = 1e-6
_MAX_SWITCH_SEC = 4.0
# Uniform jitter added to selection score per (seed, interval_idx, file_name)
# to break score-dominance and produce variety across reruns with different
# seed AND across intervals within one run. Magnitude is tuned to overpower
# typical overlap-score differences (priority-tag overlap is usually 1-3),
# so that "the one obvious top file" doesn't dominate the very first interval
# across all batch versions.
_SCORE_JITTER_MAG = 2.5

# Weighted softmax sampling within top-K candidates (instead of strict argmax).
# Top files remain probable, but with non-trivial probability the runner-up
# wins instead. This produces real variety across reruns even when the
# overlap-score gap exceeds _SCORE_JITTER_MAG. Determinism is preserved via
# a seeded RNG keyed by (seed_value, interval_idx, interval_start).
#   _SOFTMAX_TEMP : higher  -> flatter distribution (more variety)
#   _SOFTMAX_TOP_K: how many candidates compete (cuts long tail of bad files)
_SOFTMAX_TEMP = 2.0
_SOFTMAX_TOP_K = 5
# Safety margin (seconds) kept at the head/tail of a source video when
# picking a random in-source offset. Relaxed from 0.1 to 0.05 to unlock
# offsets for sources whose duration is just barely above the interval.
_SOURCE_OFFSET_SAFETY = 0.05
_STYLE_COLOR_ALLOWED = {"dark", "light", "warm", "cold", "neutral"}
_STYLE_MOOD_ALLOWED = {"major", "minor"}
_STYLE_PEOPLE_ALLOWED = {"none", "girls", "guys", "couple", "crowd", "driver"}
_CLIP_ID_RE = re.compile(r"(\d{8,})")
_GENRE_NORMALIZE_RE = re.compile(r"[^0-9a-zа-я]+", flags=re.IGNORECASE)

# artist style -> inventory genre matching aliases
_STYLE_GENRE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "hiphop": ("hiphop", "hip-hop", "хип-хоп", "хипхоп", "хип хоп", "хипхоп"),
    "pop": ("pop", "поп"),
    "rock": ("rock", "рок"),
    "alternative": ("alternative", "альтернатива", "alt"),
    "electronic": ("electronic", "electro", "электроника", "электро"),
}

# deterministic genre-level similarity fallback chain.
_STYLE_GENRE_SIMILARITY: Dict[str, Tuple[str, ...]] = {
    "hiphop": ("alternative", "electronic", "pop", "rock"),
    "pop": ("electronic", "alternative", "hiphop", "rock"),
    "rock": ("alternative", "hiphop", "electronic", "pop"),
    "alternative": ("rock", "electronic", "hiphop", "pop"),
    "electronic": ("alternative", "hiphop", "pop", "rock"),
}


def _load_global_ban_tags() -> frozenset:
    """Parse globally banned tags from the footage selection prompt.

    Reads the line:
        NEVER use these globally banned tags: tag1, tag2, tag3.
    Falls back to an empty set if the file or pattern is missing, and emits
    a warning so operators can see that global-ban filtering is disabled.
    """
    src = Path(__file__).resolve().parents[1] / "3rd_footage_selection_prompt" / "prompt.md"
    if not src.exists():
        warnings.warn(
            f"Global ban tags source is missing: {src}",
            RuntimeWarning,
            stacklevel=2,
        )
        return frozenset()
    text = src.read_text(encoding="utf-8")
    match = re.search(r"NEVER use these globally banned tags:\s*(.+)", text)
    if not match:
        warnings.warn(
            f"Global ban tags line is missing in prompt: {src}",
            RuntimeWarning,
            stacklevel=2,
        )
        return frozenset()
    raw = match.group(1).rstrip(".").strip()
    tags = frozenset(t.strip().lower() for t in raw.split(",") if t.strip())
    if not tags:
        warnings.warn(
            f"Global ban tags parsed as empty from prompt: {src}",
            RuntimeWarning,
            stacklevel=2,
        )
    return tags


_GLOBAL_BAN_TAGS: frozenset = _load_global_ban_tags()
_SELECTION_RANK_SCORE_KEY = "_selection_rank_score"


def _load_tag_aliases() -> Dict[str, str]:
    """Load free-form -> canonical taxonomy tag aliases from data/tag_aliases.json.

    The vision tagger (scan.py) emits open-vocabulary tags (e.g. "rainy",
    "mountains") while the LLM footage prompt picks from a curated taxonomy
    (e.g. "rain", "mountain"). Without aliasing, ~43% of clip tag-instances are
    invisible to the picker. This map is applied to clip meta_theme_tags only,
    so a clip tagged "rainy" matches an LLM pick of "rain". Missing file or
    parse error degrades gracefully to no aliasing (picker keeps working).
    """
    src = Path(__file__).resolve().parents[1] / "data" / "tag_aliases.json"
    if not src.exists():
        warnings.warn(f"Tag aliases source is missing: {src}", RuntimeWarning, stacklevel=2)
        return {}
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        warnings.warn(f"Tag aliases parse failed ({src}): {e!r}", RuntimeWarning, stacklevel=2)
        return {}
    raw = data.get("aliases") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        warnings.warn(f"Tag aliases missing 'aliases' object: {src}", RuntimeWarning, stacklevel=2)
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        key = " ".join(str(k or "").strip().lower().split())
        val = " ".join(str(v or "").strip().lower().split())
        if key and val and key != val:
            out[key] = val
    return out


_TAG_ALIASES: Dict[str, str] = _load_tag_aliases()


def _load_tag_overrides() -> Dict[str, Any]:
    """Load user tag overrides from asset_tag_overrides.json (if exists)."""
    src = Path(__file__).resolve().parents[1] / "data" / "asset_tag_overrides.json"
    if not src.exists():
        return {}
    try:
        return json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_global_tag_overrides() -> Dict[str, Any]:
    """Load global tag-level overrides (blacklist + assignments)."""
    src = Path(__file__).resolve().parents[1] / "data" / "tag_overrides.json"
    if not src.exists():
        return {"blacklisted_tags": [], "tag_assignments": []}
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        data.setdefault("blacklisted_tags", [])
        data.setdefault("tag_assignments", [])
        return data
    except Exception:
        return {"blacklisted_tags": [], "tag_assignments": []}


def _apply_tag_overrides(
    assets: List[Dict[str, Any]],
    overrides: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Filter excluded assets and merge custom tags from overrides."""
    if overrides is None:
        overrides = _load_tag_overrides()
    if not overrides:
        return assets
    out: List[Dict[str, Any]] = []
    for a in assets:
        ov = overrides.get(a.get("file_name", ""), {})
        if ov.get("excluded"):
            continue
        # Merge custom tags into meta_theme_tags
        assignments = ov.get("theme_assignments") or []
        if assignments:
            extra_tags: List[str] = []
            for ta in assignments:
                extra_tags.extend(ta.get("tags") or [])
            if extra_tags:
                existing = list(a.get("meta_theme_tags") or [])
                merged = list(dict.fromkeys(existing + extra_tags))
                a = {**a, "meta_theme_tags": merged}
        out.append(a)
    return out


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
    selection_mode: str = "classic"
    subgroup_order: List[Dict[str, Any]] = field(default_factory=list)
    interval_trace: List[Dict[str, Any]] = field(default_factory=list)
    # Quality/uniqueness signals used by bot rotation advance logic.
    # primary_pool_avg_score: avg base score of selected clips (overlap+color_bonus, pre-jitter)
    # primary_pool_repeat_ratio: fraction of intervals whose file_name repeats at least once
    primary_pool_avg_score: float = 0.0
    primary_pool_repeat_ratio: float = 0.0


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
    requested_style_id: str
    requested_style_genre_key: str
    resolved_style_genre_key: str
    resolved_similarity_rank: int
    similarity_fallback_used: bool
    similarity_chain: List[str]
    top_groups: List[Dict[str, Any]]


def _as_pos_float(v: Any) -> float:
    try:
        x = float(v)
    except Exception as e:
        raise RuntimeError(f"Invalid float value: {v!r}") from e
    if x <= 0:
        raise RuntimeError(f"Expected positive float, got {x!r}")
    return x


def _normalize_genre_name(v: Any) -> str:
    raw = " ".join(str(v or "").strip().lower().split())
    if not raw:
        return ""
    return _GENRE_NORMALIZE_RE.sub("", raw)


def _resolve_style_genre_key(style_id: str) -> str:
    preset = find_preset_by_artist_id(style_id)
    if not isinstance(preset, dict):
        raise RuntimeError(f"Unknown footage style id: {style_id!r}")
    genre_key = str(preset.get("genre_key") or "").strip().lower()
    if not genre_key:
        raise RuntimeError(f"Style preset has empty genre_key: {style_id!r}")
    return genre_key


def _genre_similarity_chain(primary_genre_key: str) -> List[str]:
    base = list(_STYLE_GENRE_SIMILARITY.get(primary_genre_key, ()))
    chain_raw = [primary_genre_key] + base + list(_STYLE_GENRE_ALIASES.keys())
    out: List[str] = []
    seen: set[str] = set()
    for it in chain_raw:
        key = str(it or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _row_matches_style_genre_key(row_genre: str, style_genre_key: str) -> bool:
    row_norm = _normalize_genre_name(row_genre)
    if not row_norm:
        return False
    aliases = _STYLE_GENRE_ALIASES.get(style_genre_key, (style_genre_key,))
    alias_norm = {_normalize_genre_name(x) for x in aliases}
    alias_norm.discard("")
    if not alias_norm:
        return False
    for a in alias_norm:
        if row_norm == a or row_norm.startswith(a) or a.startswith(row_norm):
            return True
    return False


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


def _score_jitter(seed_value: int, file_name: str, interval_idx: int = -1) -> float:
    """Uniform [0, _SCORE_JITTER_MAG) jitter, stable per (seed, interval_idx, file_name).

    Depends on interval_idx so the same file gets a different effective rank
    across different intervals within ONE run — this prevents "the one
    high-overlap file" from dominating the first interval across multiple
    batch versions (the symptom: v3 first 4-5s identical to v1 first 4-5s).
    Different seeds also yield different orderings across reruns.
    """
    material = f"jitter:{seed_value}:{int(interval_idx)}:{file_name}"
    h = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    frac = h / (2 ** 64)
    return float(frac) * _SCORE_JITTER_MAG


def _seeded_unit_random(seed_value: int, interval_idx: int, interval_start: float, salt: str = "") -> float:
    """Deterministic uniform random in [0, 1) keyed by (seed, interval_idx, start, salt)."""
    material = f"sample:{seed_value}:{int(interval_idx)}:{float(interval_start):.6f}:{salt}"
    h = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    return float(h) / float(2 ** 64)


def _softmax_pick_index(
    *,
    scores: List[float],
    seed_value: int,
    interval_idx: int,
    interval_start: float,
    salt: str = "",
    top_k: int = _SOFTMAX_TOP_K,
    temperature: float = _SOFTMAX_TEMP,
) -> int:
    """Pick an index into `scores` via softmax-weighted sampling within top-K.

    Top-K filter (by score, descending) keeps low-quality candidates out of the
    distribution. Softmax with temperature spreads probability across remaining
    candidates: top file is most probable but not guaranteed. Determinism comes
    from a seeded uniform draw — the same seed -> same pick.
    """
    n = len(scores)
    if n <= 0:
        raise RuntimeError("softmax pick on empty scores")
    # Build (orig_idx, score) sorted by score desc; ties broken by deterministic hash
    indexed = list(enumerate(scores))

    def _tie_break(item: Tuple[int, float]) -> Tuple[float, str]:
        idx, sc = item
        material = f"tie:{seed_value}:{interval_idx}:{interval_start:.6f}:{idx}"
        h = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return -float(sc), h

    indexed.sort(key=_tie_break)
    k = max(1, min(int(top_k), n))
    top = indexed[:k]
    top_scores = [s for _, s in top]
    max_s = max(top_scores)
    t = max(1e-3, float(temperature))
    weights = [math.exp((s - max_s) / t) for s in top_scores]
    wsum = sum(weights) or 1.0
    probs = [w / wsum for w in weights]
    u = _seeded_unit_random(seed_value, interval_idx, interval_start, salt=salt)
    acc = 0.0
    for (orig_idx, _), p in zip(top, probs):
        acc += p
        if u <= acc:
            return orig_idx
    return top[-1][0]


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

    def _score(it: Dict[str, Any]) -> float:
        try:
            base = float(it.get(_SELECTION_RANK_SCORE_KEY) or 0.0)
        except Exception:
            base = 0.0
        return base + _score_jitter(
            seed_value, str(it.get("file_name") or ""), interval_idx
        )

    avoid = str(avoid_file_name or "").strip()
    pool = list(candidates)
    if avoid and len(pool) > 1:
        pool = [it for it in pool if str(it.get("file_name") or "") != avoid] or list(candidates)

    scores = [_score(it) for it in pool]
    pick_idx = _softmax_pick_index(
        scores=scores,
        seed_value=seed_value,
        interval_idx=interval_idx,
        interval_start=interval_start,
        salt="choose",
    )
    return pool[pick_idx]


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
    scores_by_name: Dict[str, float] | None = None,
) -> List[str]:
    """Return file_names ordered by descending preference.

    The first element comes from a softmax-weighted pick within top-K, so
    different seeds produce genuinely different "winners" of an interval
    (not just hash-tie-break orderings). The remaining elements are filled
    by deterministic strict-rank order so callers that walk down the list
    on conflicts still see a sensible fallback chain.
    """
    if not file_names:
        return []

    def _score(name: str) -> float:
        base = 0.0
        if scores_by_name:
            try:
                base = float(scores_by_name.get(name) or 0.0)
            except Exception:
                base = 0.0
        return base + _score_jitter(seed_value, name, interval_idx)

    scores = [_score(n) for n in file_names]
    pick_idx = _softmax_pick_index(
        scores=scores,
        seed_value=seed_value,
        interval_idx=interval_idx,
        interval_start=interval_start,
        salt="filename",
    )
    head = file_names[pick_idx]

    def _tail_key(name: str) -> Tuple[float, str, str]:
        material = f"{seed_value}:{interval_idx}:{interval_start:.6f}:{name}"
        h = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return -_score(name), h, name

    rest = [n for i, n in enumerate(file_names) if i != pick_idx]
    rest_sorted = sorted(rest, key=_tail_key)
    return [head, *rest_sorted]


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
    scores_by_name: Dict[str, float] = {}
    for nm, it in by_name.items():
        try:
            scores_by_name[nm] = float(it.get(_SELECTION_RANK_SCORE_KEY) or 0.0)
        except Exception:
            scores_by_name[nm] = 0.0

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
                scores_by_name=scores_by_name,
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


def _build_raw_pool(
    raw_pick: FootageStyleRawPayload,
    assets: List[Dict[str, Any]],
    *,
    style_genre: Optional[str] = None,
    style_tag: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build a scored clip pool from a single raw subgroup payload."""
    assets = _apply_tag_overrides(assets)
    global_tag_ov = _load_global_tag_overrides()
    blacklisted = {_normalize_theme_tag(t) for t in global_tag_ov.get("blacklisted_tags", [])}
    blacklisted.discard("")

    # Expand priority_tags with globally assigned tags for this theme/group
    priority_tags = {_normalize_theme_tag(x) for x in raw_pick.filters.priority_theme_tags}
    for ta in global_tag_ov.get("tag_assignments", []):
        if ta.get("theme") == raw_pick.theme and ta.get("group") == raw_pick.tags_group:
            norm = _normalize_theme_tag(ta.get("tag", ""))
            if norm:
                priority_tags.add(norm)
    priority_tags.discard("")
    priority_tags -= blacklisted
    exclude_people = {_normalize_people_type(x) for x in (raw_pick.filters.exclude or [])}
    exclude_people.discard("")
    exclude_terms = {_normalize_theme_tag(x) for x in (raw_pick.filters.exclude_tags or [])}
    exclude_terms.discard("")
    require_people = _normalize_people_type(raw_pick.filters.require_people or "") or None
    color_priority = {_normalize_color_tone(x) for x in list(raw_pick.filters.color_priority or [])}
    color_priority.discard("")

    pool: List[Dict[str, Any]] = []
    for it in assets:
        if style_genre is not None and style_tag is not None:
            if str(it.get("genre") or "").strip() != str(style_genre).strip():
                continue
            if str(it.get("tag") or "").strip() != str(style_tag).strip():
                continue
        meta_tags = {_normalize_meta_tag(x) for x in (it.get("meta_theme_tags") or [])}
        meta_tags.discard("")
        meta_tags -= blacklisted
        overlap = len(priority_tags.intersection(meta_tags))
        if overlap <= 0:
            continue
        people = _normalize_people_type(it.get("meta_people_type"))
        if (
            (people and people in exclude_people)
            or exclude_terms.intersection(meta_tags)
            or _GLOBAL_BAN_TAGS.intersection(meta_tags)
        ):
            continue
        if require_people and people != require_people:
            continue
        # Color bonus: +0.5 when clip color_tone matches LLM color_priority
        color_bonus = 0.0
        clip_color = _normalize_color_tone(it.get("meta_color_tone"))
        if clip_color and clip_color in color_priority:
            color_bonus = 0.5
        row = dict(it)
        row[_SELECTION_RANK_SCORE_KEY] = float(overlap) + color_bonus
        pool.append(row)
    return pool


def _source_offset_enabled() -> bool:
    import os
    return os.environ.get("FOOTAGE_SOURCE_OFFSET_ENABLED", "1").strip() not in ("0", "false", "no", "off")


def _deterministic_source_offset(
    *,
    file_name: str,
    asset_duration_sec: float,
    interval_len: float,
    seed_value: int,
    interval_idx: int,
) -> float:
    """Return a deterministic random start offset within the source video.

    The offset is clamped so that the source has enough remaining footage to
    cover the full interval duration.  A small safety margin is kept
    (see _SOURCE_OFFSET_SAFETY). Returns 0.0 when there is no room to offset.
    """
    safety = _SOURCE_OFFSET_SAFETY
    max_offset = float(asset_duration_sec) - float(interval_len) - safety
    if max_offset < safety:
        return 0.0
    material = f"srcoff:{seed_value}:{interval_idx}:{file_name}"
    h = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    frac = h / (2 ** 64)
    return round(frac * max_offset, 3)


def _compute_pool_stats(
    *,
    selected_names: List[str],
    assets_by_name: Dict[str, Dict[str, Any]],
) -> Tuple[float, float]:
    """Return (avg_score, repeat_ratio) for a list of chosen file_names.

    avg_score averages base selection score (overlap+color_bonus, pre-jitter).
    repeat_ratio is the fraction of positions whose file_name occurs more than
    once in the list (0.0 if all unique; up to 1.0 if everything repeats).
    """
    n = len(selected_names or [])
    if n <= 0:
        return 0.0, 0.0
    total = 0.0
    count = 0
    for nm in selected_names:
        asset = assets_by_name.get(str(nm))
        if not isinstance(asset, dict):
            continue
        try:
            total += float(asset.get(_SELECTION_RANK_SCORE_KEY) or 0.0)
            count += 1
        except Exception:
            continue
    avg = (total / count) if count > 0 else 0.0
    counts: Dict[str, int] = {}
    for nm in selected_names:
        key = str(nm)
        counts[key] = counts.get(key, 0) + 1
    repeated_positions = sum(1 for nm in selected_names if counts.get(str(nm), 0) > 1)
    ratio = float(repeated_positions) / float(n)
    return float(avg), float(ratio)


def _assign_rotation_file_names(
    *,
    intervals: List[Tuple[float, float]],
    subgroup_pools: List[List[Dict[str, Any]]],
    seed_value: int,
    excluded_names: set,
) -> Tuple[List[str], bool]:
    """
    Assign file names to intervals using per-block subgroup rotation.

    Intervals are split into len(subgroup_pools) roughly equal blocks.
    Each block pulls from its subgroup's pool first; falls back to merged pool,
    then allows repeats as last resort.

    Returns (assigned_file_names, repeats_used).
    """
    n = len(intervals)
    k = len(subgroup_pools)
    block_size = math.ceil(n / k)

    # Merged fallback pool (all subgroups combined, deduped)
    merged_all = _dedupe_assets_by_file_name([it for pool in subgroup_pools for it in pool])
    merged = [it for it in merged_all if str(it.get("file_name") or "") not in excluded_names]

    all_assigned: List[str] = []
    repeats_used = False

    for block_idx in range(k):
        start = block_idx * block_size
        end = min(start + block_size, n)
        if start >= n:
            break
        block_intervals = intervals[start:end]

        pool_all = _dedupe_assets_by_file_name(subgroup_pools[block_idx])
        pool = [it for it in pool_all if str(it.get("file_name") or "") not in excluded_names]

        # Prefer assets not already used in previous blocks
        used = set(all_assigned)
        pool_fresh = [it for it in pool if str(it.get("file_name") or "") not in used]
        merged_fresh = [it for it in merged if str(it.get("file_name") or "") not in used]

        block_assigned: Optional[List[str]] = None
        for candidate_pool in [pool_fresh, pool, merged_fresh, merged]:
            if not candidate_pool:
                continue
            try:
                block_assigned = _assign_unique_file_names_for_intervals(
                    intervals=block_intervals,
                    pool=candidate_pool,
                    seed_value=seed_value,
                )
                break
            except RuntimeError:
                continue

        if block_assigned is None:
            # Last resort: allow repeats via deterministic choice
            fallback_pool = pool or merged
            if not fallback_pool:
                raise RuntimeError(
                    "No assets available for rotation block after applying exclusions "
                    f"(block={block_idx}, block_intervals={len(block_intervals)}, excluded={len(excluded_names)})"
                )
            repeats_used = True
            block_assigned = []
            prev: Optional[str] = all_assigned[-1] if all_assigned else None
            for gi, (a, b) in enumerate(block_intervals):
                need = float(b - a)
                candidates = [it for it in fallback_pool if _fits_interval(it, interval_len=need)]
                if not candidates:
                    candidates = fallback_pool
                chosen = _deterministic_choose(
                    candidates=candidates,
                    seed_value=seed_value,
                    interval_idx=start + gi,
                    interval_start=float(a),
                    avoid_file_name=prev,
                )
                nm = str(chosen["file_name"])
                block_assigned.append(nm)
                prev = nm

        all_assigned.extend(block_assigned)

    return all_assigned, repeats_used


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
    raw_pick: FootageStyleRawPayload | None = None,
    raw_picks: List[FootageStyleRawPayload] | None = None,
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

    # ── Ordered subgroup priority path (raw_picks) ────────────────────────────
    # The model returns 1..3 subgroups in strict priority order.
    # Picker consumes clips from the first subgroup until it can no longer
    # provide suitable unseen clips for the current interval, then moves
    # forward to the next subgroup.
    if raw_picks is not None and len(raw_picks) > 0:
        seed_value = deterministic_seed_from_key(seed_key)
        excluded_names = {str(x).strip() for x in list(exclude_file_names or []) if str(x).strip()}
        k = len(raw_picks)

        def _scores_by_name(pool: List[Dict[str, Any]]) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for it in pool:
                nm = str(it.get("file_name") or "").strip()
                if not nm:
                    continue
                try:
                    out[nm] = float(it.get(_SELECTION_RANK_SCORE_KEY) or 0.0)
                except Exception:
                    out[nm] = 0.0
            return out

        def _ordered_names_for_pool(
            *,
            pool: List[Dict[str, Any]],
            subgroup_idx: int,
            scores_map: Dict[str, float],
        ) -> List[str]:
            names = [str(it.get("file_name") or "").strip() for it in pool if str(it.get("file_name") or "").strip()]
            if not names:
                return []
            return _deterministic_file_name_order(
                file_names=names,
                seed_value=seed_value,
                interval_idx=-(subgroup_idx + 1),
                interval_start=float(subgroup_idx),
                scores_by_name=scores_map,
            )

        subgroup_defs = list(raw_picks)
        subgroup_pools_all: List[List[Dict[str, Any]]] = []
        subgroup_pools_selected: List[List[Dict[str, Any]]] = []
        subgroup_maps_all: List[Dict[str, Dict[str, Any]]] = []
        subgroup_maps_selected: List[Dict[str, Dict[str, Any]]] = []
        subgroup_scores_all: List[Dict[str, float]] = []
        subgroup_scores_selected: List[Dict[str, float]] = []
        subgroup_order_all: List[List[str]] = []
        subgroup_order_selected: List[List[str]] = []
        subgroup_order_diag: List[Dict[str, Any]] = []
        primary_pool_names: set[str] = set()
        selected_pool_names: set[str] = set()
        for subgroup_idx, subgroup in enumerate(subgroup_defs):
            # v2 rotation: build pool purely from theme parameters (mood +
            # priority_theme_tags + exclusions). Legacy inventory genre/tag
            # filter is NOT passed — assets are already scoped to the artist
            # by the orchestrator, and within an artist we route by
            # (theme, tags_group). Same fix as commit ab21a63 applied to the
            # ordered-subgroup path.
            pool_all = _dedupe_assets_by_file_name(
                _build_raw_pool(subgroup, assets)
            )
            pool_selected = [
                it for it in pool_all if str(it.get("file_name") or "").strip() not in excluded_names
            ]
            scores_all = _scores_by_name(pool_all)
            scores_selected = _scores_by_name(pool_selected)
            ordered_all = _ordered_names_for_pool(
                pool=pool_all,
                subgroup_idx=subgroup_idx,
                scores_map=scores_all,
            )
            ordered_selected = _ordered_names_for_pool(
                pool=pool_selected,
                subgroup_idx=subgroup_idx,
                scores_map=scores_selected,
            )
            map_all = {str(it.get("file_name") or "").strip(): it for it in pool_all if str(it.get("file_name") or "").strip()}
            map_selected = {str(it.get("file_name") or "").strip(): it for it in pool_selected if str(it.get("file_name") or "").strip()}
            subgroup_pools_all.append(pool_all)
            subgroup_pools_selected.append(pool_selected)
            subgroup_maps_all.append(map_all)
            subgroup_maps_selected.append(map_selected)
            subgroup_scores_all.append(scores_all)
            subgroup_scores_selected.append(scores_selected)
            subgroup_order_all.append(ordered_all)
            subgroup_order_selected.append(ordered_selected)
            primary_pool_names.update(map_all.keys())
            selected_pool_names.update(map_selected.keys())
            subgroup_order_diag.append(
                {
                    "index": int(subgroup_idx),
                    "theme": str(subgroup.theme),
                    "tags_group": str(subgroup.tags_group or ""),
                    "priority_theme_tags": list(subgroup.filters.priority_theme_tags or []),
                    "exclude_people": list(subgroup.filters.exclude or []),
                    "exclude_tags": list(subgroup.filters.exclude_tags or []),
                    "color_priority": list(subgroup.filters.color_priority or []),
                    "pool_all_count": int(len(pool_all)),
                    "pool_selected_count": int(len(pool_selected)),
                    "excluded_by_input_count": int(max(0, len(pool_all) - len(pool_selected))),
                    "ordered_all_head": list(ordered_all[:10]),
                    "ordered_selected_head": list(ordered_selected[:10]),
                }
            )

        if not primary_pool_names:
            subgroup_summary = ", ".join(
                f"{idx}:theme={sg.theme!r}/group={(sg.tags_group or '')!r}"
                for idx, sg in enumerate(subgroup_defs)
            )
            raise RuntimeError(
                "No assets satisfy ordered raw subgroup filters "
                f"(subgroups={k}; {subgroup_summary})"
            )

        exclude_relaxed = False
        active_maps = subgroup_maps_selected
        active_scores = subgroup_scores_selected
        active_order = subgroup_order_selected
        effective_pool_names = set(selected_pool_names)
        if not effective_pool_names and excluded_names:
            exclude_relaxed = True
            active_maps = subgroup_maps_all
            active_scores = subgroup_scores_all
            active_order = subgroup_order_all
            effective_pool_names = set(primary_pool_names)

        repeats_used = False
        used_names: set[str] = set()
        selected_file_names: List[str] = []
        interval_trace: List[Dict[str, Any]] = []
        current_subgroup_idx = 0

        def _pick_name_from_subgroup(
            *,
            subgroup_idx: int,
            interval_idx: int,
            interval_start: float,
            interval_len: float,
            allow_reuse: bool,
            avoid_name: str,
        ) -> Tuple[str | None, int]:
            ordered = list(active_order[subgroup_idx] or [])
            if not ordered:
                return None, 0
            subgroup_map = active_maps[subgroup_idx]
            subgroup_scores = active_scores[subgroup_idx]
            viable_names: List[str] = []
            for nm in ordered:
                if not allow_reuse and nm in used_names:
                    continue
                asset = subgroup_map.get(nm)
                if not isinstance(asset, dict):
                    continue
                if _fits_interval(asset, interval_len=interval_len):
                    viable_names.append(nm)
            if not viable_names:
                return None, 0
            ranked = _deterministic_file_name_order(
                file_names=viable_names,
                seed_value=seed_value,
                interval_idx=interval_idx,
                interval_start=interval_start,
                scores_by_name=subgroup_scores,
            )
            avoid = str(avoid_name or "").strip()
            if avoid and len(ranked) > 1:
                for nm in ranked:
                    if nm != avoid:
                        return nm, len(viable_names)
            return ranked[0], len(viable_names)

        for idx, (a, b) in enumerate(intervals):
            need = float(b - a)
            prev_name = selected_file_names[-1] if selected_file_names else ""
            phase = "unique"
            trace_attempts: List[Dict[str, Any]] = []

            def _try_pick(*, start_idx: int, allow_reuse: bool, phase_name: str) -> Tuple[str | None, int]:
                nonlocal trace_attempts
                for subgroup_idx in range(start_idx, k):
                    picked_name, candidate_count = _pick_name_from_subgroup(
                        subgroup_idx=subgroup_idx,
                        interval_idx=idx,
                        interval_start=float(a),
                        interval_len=need,
                        allow_reuse=allow_reuse,
                        avoid_name=prev_name,
                    )
                    trace_attempts.append(
                        {
                            "phase": phase_name,
                            "subgroup_idx": int(subgroup_idx),
                            "theme": str(subgroup_defs[subgroup_idx].theme),
                            "tags_group": str(subgroup_defs[subgroup_idx].tags_group or ""),
                            "candidate_count": int(candidate_count),
                            "picked": str(picked_name or ""),
                        }
                    )
                    if picked_name:
                        return picked_name, subgroup_idx
                return None, -1

            chosen_name, chosen_subgroup_idx = _try_pick(
                start_idx=current_subgroup_idx,
                allow_reuse=False,
                phase_name=phase,
            )

            if not chosen_name and excluded_names and not exclude_relaxed:
                exclude_relaxed = True
                active_maps = subgroup_maps_all
                active_scores = subgroup_scores_all
                active_order = subgroup_order_all
                effective_pool_names = set(primary_pool_names)
                phase = "unique_exclude_relaxed"
                chosen_name, chosen_subgroup_idx = _try_pick(
                    start_idx=current_subgroup_idx,
                    allow_reuse=False,
                    phase_name=phase,
                )

            if not chosen_name:
                repeats_used = True
                phase = "reuse"
                chosen_name, chosen_subgroup_idx = _try_pick(
                    start_idx=current_subgroup_idx,
                    allow_reuse=True,
                    phase_name=phase,
                )

            if not chosen_name and current_subgroup_idx > 0:
                repeats_used = True
                phase = "reuse_full_chain"
                chosen_name, chosen_subgroup_idx = _try_pick(
                    start_idx=0,
                    allow_reuse=True,
                    phase_name=phase,
                )

            if not chosen_name or chosen_subgroup_idx < 0:
                raise RuntimeError(
                    "No footage asset can cover interval in ordered subgroup selection "
                    f"(idx={idx}, interval={a:.3f}..{b:.3f}, dur={need:.3f}, start_subgroup={current_subgroup_idx})"
                )

            if chosen_name in used_names:
                repeats_used = True
            else:
                used_names.add(chosen_name)
            selected_file_names.append(chosen_name)
            current_subgroup_idx = int(chosen_subgroup_idx)
            interval_trace.append(
                {
                    "interval_idx": int(idx),
                    "in_point": float(a),
                    "out_point": float(b),
                    "duration": float(need),
                    "phase": phase,
                    "selected_subgroup_idx": int(chosen_subgroup_idx),
                    "selected_theme": str(subgroup_defs[chosen_subgroup_idx].theme),
                    "selected_tags_group": str(subgroup_defs[chosen_subgroup_idx].tags_group or ""),
                    "selected_file_name": chosen_name,
                    "exclude_relaxed": bool(exclude_relaxed),
                    "attempts": trace_attempts,
                }
            )

        all_assets_by_name: Dict[str, Dict[str, Any]] = {}
        for subgroup_map in subgroup_maps_all:
            for nm, row in subgroup_map.items():
                if nm and nm not in all_assets_by_name:
                    all_assets_by_name[nm] = row

        offset_enabled = _source_offset_enabled()
        clips: List[Dict[str, Any]] = []
        for idx, (a, b) in enumerate(intervals):
            chosen_name = str(selected_file_names[idx])
            asset_dur = float((all_assets_by_name.get(chosen_name) or {}).get("duration_sec") or 0.0)
            src_off = (
                _deterministic_source_offset(
                    file_name=chosen_name,
                    asset_duration_sec=asset_dur,
                    interval_len=float(b - a),
                    seed_value=seed_value,
                    interval_idx=idx,
                )
                if offset_enabled and asset_dur > 0
                else 0.0
            )
            clips.append(
                {
                    "file_name": chosen_name,
                    "fit_mode": fit_mode,
                    "in_point": float(a),
                    "out_point": float(b),
                    "source_offset_sec": src_off,
                    "start_time": float(a) - src_off,
                }
            )

        selected_excluded_count = sum(1 for x in selected_file_names if str(x) in excluded_names)
        payload = FootageSelectionPayload.model_validate({"clips": clips, "allow_gaps": False})
        avg_score, repeat_ratio = _compute_pool_stats(
            selected_names=[str(x) for x in selected_file_names],
            assets_by_name=all_assets_by_name,
        )
        diag = FootageIntervalPickerDiagnostics(
            genre="__raw_priority_v2__",
            tag=str(subgroup_defs[0].theme),
            intervals_count=len(intervals),
            max_interval_sec=max(float(b - a) for a, b in intervals),
            primary_pool_count=int(len(primary_pool_names)),
            selected_pool_count=int(len(effective_pool_names)),
            widened_to_genre=False,
            widened_to_global=False,
            repeats_used=bool(repeats_used),
            excluded_input_count=len(excluded_names),
            selected_excluded_count=int(selected_excluded_count),
            exclude_relaxed=bool(exclude_relaxed),
            deterministic_seed=int(seed_value),
            seed_key=str(seed_key),
            selected_file_names=[str(x) for x in selected_file_names],
            selection_mode="raw_priority_v2",
            subgroup_order=subgroup_order_diag,
            interval_trace=interval_trace,
            primary_pool_avg_score=float(avg_score),
            primary_pool_repeat_ratio=float(repeat_ratio),
        )
        return payload, diag
    # ── End rotation path ─────────────────────────────────────────────────────

    use_raw_global = raw_pick is not None

    if use_raw_global:
        priority_tags = {_normalize_theme_tag(x) for x in list(raw_pick.filters.priority_theme_tags or [])}
        priority_tags.discard("")
        if not priority_tags:
            raise RuntimeError("Raw footage selection requires non-empty priority_theme_tags")
        exclude_people = {_normalize_people_type(x) for x in list(raw_pick.filters.exclude or [])}
        exclude_people.discard("")
        exclude_terms = {_normalize_theme_tag(x) for x in list(raw_pick.filters.exclude_tags or [])}
        exclude_terms.discard("")
        require_people = _normalize_people_type(raw_pick.filters.require_people or "") or None

        # Raw tag-first mode:
        # - strict ban if asset matches exclude (people type OR metadata tag OR inventory tag),
        # - score is pure overlap count with priority_theme_tags.
        primary_pool = []
        for it in assets:
            meta_tags = {_normalize_meta_tag(x) for x in list(it.get("meta_theme_tags") or [])}
            meta_tags.discard("")
            overlap = int(len(priority_tags.intersection(meta_tags)))
            if overlap <= 0:
                continue
            people = _normalize_people_type(it.get("meta_people_type"))
            inv_tag = _normalize_theme_tag(it.get("tag"))
            excluded = bool(
                (people and people in exclude_people)
                or (exclude_terms.intersection(meta_tags))
                or (inv_tag and inv_tag in exclude_terms)
                or bool(_GLOBAL_BAN_TAGS.intersection(meta_tags))
            )
            if excluded:
                continue
            if require_people and people != require_people:
                continue
            score = float(overlap)
            row = dict(it)
            row[_SELECTION_RANK_SCORE_KEY] = score
            primary_pool.append(row)
        if not primary_pool:
            raise RuntimeError(
                "No mapped assets satisfy raw filters after strict exclude ban "
                "(priority_theme_tags overlap required) "
                f"tags={sorted(priority_tags)!r} exclude={sorted(exclude_terms)!r}"
            )
    else:
        primary_pool = [
            it for it in assets
            if str(it["genre"]) == genre and str(it["tag"]) == tag
            and not _GLOBAL_BAN_TAGS.intersection({_normalize_theme_tag(x) for x in list(it.get("meta_theme_tags") or [])})
        ]
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

    if assigned_file_names is None and not use_raw_global:
        widen_pool = [it for it in assets if str(it["genre"]) == genre and str(it["tag"]) != tag]
        if widen_pool:
            selected_pool_all = _dedupe_assets_by_file_name(selected_pool_all + widen_pool)
            selected_pool = [it for it in selected_pool_all if str(it.get("file_name") or "") not in excluded_names]
            widened_to_genre = True
            assigned_file_names = _try_assign(selected_pool)

    if assigned_file_names is None and not use_raw_global:
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
    offset_enabled = _source_offset_enabled()

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
            asset_dur = float(chosen.get("duration_sec") or 0.0)
            src_off = (
                _deterministic_source_offset(
                    file_name=chosen_name,
                    asset_duration_sec=asset_dur,
                    interval_len=need,
                    seed_value=seed_value,
                    interval_idx=idx,
                )
                if offset_enabled and asset_dur > 0
                else 0.0
            )
            clips.append(
                {
                    "file_name": chosen_name,
                    "fit_mode": fit_mode,
                    "in_point": float(a),
                    "out_point": float(b),
                    "source_offset_sec": src_off,
                    "start_time": float(a) - src_off,
                }
            )
            prev_file_name = chosen_name
            assigned_file_names = [str(c["file_name"]) for c in clips]
    else:
        for idx, (a, b) in enumerate(intervals):
            chosen_name = str(assigned_file_names[idx])
            if chosen_name not in by_name:
                raise RuntimeError(f"assigned file_name not present in selected pool: {chosen_name!r}")
            asset_dur = float((by_name.get(chosen_name) or {}).get("duration_sec") or 0.0)
            src_off = (
                _deterministic_source_offset(
                    file_name=chosen_name,
                    asset_duration_sec=asset_dur,
                    interval_len=float(b - a),
                    seed_value=seed_value,
                    interval_idx=idx,
                )
                if offset_enabled and asset_dur > 0
                else 0.0
            )
            clips.append(
                {
                    "file_name": chosen_name,
                    "fit_mode": fit_mode,
                    "in_point": float(a),
                    "out_point": float(b),
                    "source_offset_sec": src_off,
                    "start_time": float(a) - src_off,
                }
            )

    selected_excluded_count = 0
    if excluded_names:
        selected_excluded_count = sum(1 for x in assigned_file_names if str(x) in excluded_names)

    diag_genre = genre
    diag_tag = tag
    if use_raw_global and raw_pick is not None:
        diag_genre = "__raw_global__"
        diag_tag = str(raw_pick.theme)
        selection_mode = "raw_global"
    else:
        selection_mode = "classic_interval"

    payload = FootageSelectionPayload.model_validate({"clips": clips, "allow_gaps": False})
    avg_score, repeat_ratio = _compute_pool_stats(
        selected_names=[str(x) for x in assigned_file_names],
        assets_by_name=by_name,
    )
    diag = FootageIntervalPickerDiagnostics(
        genre=diag_genre,
        tag=diag_tag,
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
        selection_mode=selection_mode,
        primary_pool_avg_score=float(avg_score),
        primary_pool_repeat_ratio=float(repeat_ratio),
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


def _normalize_meta_tag(v: Any) -> str:
    """Normalize a CLIP-side theme tag and remap free-form -> canonical taxonomy.

    Use this (not _normalize_theme_tag) for clip meta_theme_tags so the
    open-vocabulary tagger output matches the curated taxonomy the LLM picks
    from. LLM-side tags (priority_theme_tags / exclude_tags) stay on
    _normalize_theme_tag — they are already canonical and must not be remapped.
    """
    base = _normalize_theme_tag(v)
    return _TAG_ALIASES.get(base, base)


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
    requested_style_id: str = "",
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
        tags = {_normalize_meta_tag(x) for x in list(it.get("meta_theme_tags") or [])}
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
    requested_style = str(requested_style_id or "").strip()
    requested_style_genre_key = ""
    resolved_style_genre_key = ""
    resolved_similarity_rank = 0
    similarity_chain: List[str] = []
    best: Optional[Dict[str, Any]] = None

    if requested_style:
        requested_style_genre_key = _resolve_style_genre_key(requested_style)
        similarity_chain = _genre_similarity_chain(requested_style_genre_key)
        for idx, genre_key in enumerate(similarity_chain, start=1):
            filtered = [r for r in rows if _row_matches_style_genre_key(str(r["genre"]), genre_key)]
            if not filtered:
                continue
            best = filtered[0]
            resolved_style_genre_key = genre_key
            resolved_similarity_rank = idx
            break
        if best is None:
            raise RuntimeError(
                "style_selection_exhausted "
                f"requested_style_id={requested_style!r} "
                f"requested_style_genre={requested_style_genre_key!r} "
                f"available_genres={sorted({str(r['genre']) for r in rows})!r}"
            )
    else:
        best = rows[0]

    if best is None:
        raise RuntimeError("style_selection_internal_error: best row is empty")

    pick = FootageStylePickPayload.model_validate(
        {"genre": str(best["genre"]), "tag": str(best["tag"])}
    )

    fallback_used = bool(requested_style and resolved_similarity_rank > 1)
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
        requested_style_id=requested_style,
        requested_style_genre_key=requested_style_genre_key,
        resolved_style_genre_key=resolved_style_genre_key,
        resolved_similarity_rank=int(resolved_similarity_rank),
        similarity_fallback_used=fallback_used,
        similarity_chain=list(similarity_chain),
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
