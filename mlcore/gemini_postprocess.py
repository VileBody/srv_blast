# mlcore/gemini_postprocess.py
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import os

from jinja2 import Environment, FileSystemLoader

from mlcore.cr_patch import normalize_segment_inplace, patch_payload_dict_inplace
from mlcore.timing_calc import compute_timings
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.subtitles_tokens import BlocksTokensPayload, Token
from mlcore.models.footage_plan import FootageSelectionPayload
from core.runtime_mode import MODE_DEV, get_runtime_mode

# ✅ single source of truth for FPS (matches AE dump)
from app.config import FPS as AE_FPS


# -------------------------
# Jinja env
# -------------------------
def _tojson_filter(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _env(repo_root: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(repo_root / "mlcore" / "templates")),
        autoescape=False,
    )
    env.filters["tojson"] = _tojson_filter
    return env


# -------------------------
# Audio source resolver
# -------------------------
def _pick_audio_files(audio_dir: Path) -> List[Path]:
    exts = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mov", ".mp4"}
    if not audio_dir.exists():
        return []
    files = [p for p in sorted(audio_dir.iterdir()) if p.is_file() and p.suffix.lower() in exts]
    return files


def _resolve_audio_source(repo_root: Path) -> tuple[str, str]:
    """
    Returns: (file_name, file_path)
    Priority:
      1) AUDIO_FILE_PATH env (explicit)
      2) first file found in AUDIO_DIR (defaults to repo_root/audio)
    """
    env_path = (os.environ.get("AUDIO_FILE_PATH") or "").strip()
    env_name = (os.environ.get("AUDIO_FILE_NAME") or "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        if not p.exists():
            raise FileNotFoundError(f"AUDIO_FILE_PATH points to missing file: {p}")
        return (env_name or p.name), str(p)

    audio_dir = Path(os.environ.get("AUDIO_DIR", str(repo_root / "audio"))).resolve()
    files = _pick_audio_files(audio_dir)
    if not files:
        raise FileNotFoundError(
            "No audio file found. Set AUDIO_FILE_PATH in .env "
            "or put an audio file into repo_root/audio/"
        )
    p0 = files[0].resolve()
    return (env_name or p0.name), str(p0)


def _media_mode() -> str:
    """
    Controls how we emit file_path for audio in step3 template.

    Modes:
      - "local" (default): keep absolute local path from container (/app/..)
      - "appdir"/"windows"/"win": emit empty file_path so JSX resolves via APP_DIR/media/audio/<file_name>
    """
    s = (os.environ.get("AE_MEDIA_MODE") or "").strip().lower()
    if s:
        return s
    mode = get_runtime_mode()
    return "local" if mode == MODE_DEV else "appdir"


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid {name}: {raw!r}")


def _overlay_enabled() -> bool:
    return _env_bool("OVERLAY_ENABLED", False)


def _overlay_match_mode() -> str:
    raw = (os.environ.get("OVERLAY_MATCH_MODE") or "by_style").strip().lower()
    if raw not in {"by_style", "global"}:
        raise RuntimeError("OVERLAY_MATCH_MODE must be one of: by_style | global")
    return raw


def _resolve_overlay_inventory_path(repo_root: Path) -> Path:
    raw = (os.environ.get("OVERLAY_INVENTORY_JSON") or "").strip()
    if not raw:
        raise RuntimeError("OVERLAY_ENABLED=1 but OVERLAY_INVENTORY_JSON is empty")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    if not p.exists():
        raise FileNotFoundError(f"OVERLAY_INVENTORY_JSON missing: {p}")
    return p.resolve()


def _overlay_seed_key(out_dir: Path) -> str:
    key = (os.environ.get("OVERLAY_SELECTION_SEED") or "").strip()
    if key:
        return key
    key = (os.environ.get("STAGE2_SELECTION_SEED") or "").strip()
    if key:
        return key
    key = (os.environ.get("JOB_ID") or "").strip()
    if key:
        return key
    return str(out_dir.resolve())


def _as_pos_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except Exception:
        return None
    if x <= 0.0:
        return None
    return x


def _style_from_file_path(file_path: str) -> Optional[Tuple[str, str]]:
    parts = [p for p in str(file_path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return None
    if "pinterest_collection" in parts:
        i = parts.index("pinterest_collection")
        if len(parts) > i + 2:
            return parts[i + 1], parts[i + 2]
    return None


def load_overlay_assets_from_inventory(overlay_inventory_json: Path) -> List[Dict[str, Any]]:
    inv = json.loads(overlay_inventory_json.read_text(encoding="utf-8"))
    assets = inv.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError(f"Overlay inventory must contain assets[]: {overlay_inventory_json}")

    out: List[Dict[str, Any]] = []
    for it in assets:
        if not isinstance(it, dict):
            continue
        file_name = str(it.get("file_name") or "").strip()
        file_path = str(it.get("file_path") or "").strip()
        src_w = int(it.get("src_w") or 0)
        src_h = int(it.get("src_h") or 0)
        duration_sec = _as_pos_float(it.get("duration_sec"))
        if not file_name or not file_path:
            continue
        if src_w <= 0 or src_h <= 0:
            continue
        if duration_sec is None:
            continue

        genre = str(it.get("genre") or "").strip() or None
        tag = str(it.get("tag") or "").strip() or None
        if genre is None or tag is None:
            parsed = _style_from_file_path(file_path)
            if parsed is not None:
                genre, tag = parsed

        out.append(
            {
                "file_name": file_name,
                "file_path": file_path,
                "src_w": int(src_w),
                "src_h": int(src_h),
                "duration_sec": float(duration_sec),
                "genre": genre,
                "tag": tag,
            }
        )

    if not out:
        raise RuntimeError(f"No valid overlay assets in {overlay_inventory_json}")
    return out


def _resolve_selected_footage_style_key(
    *,
    footage_abs: FootageSelectionPayload,
    assets_map: Dict[str, Dict[str, Any]],
) -> Tuple[str, str]:
    clips = sorted(list(footage_abs.clips), key=lambda c: float(c.in_point))
    for clip in clips:
        asset = assets_map.get(str(clip.file_name))
        if not isinstance(asset, dict):
            continue
        style_key = _style_from_file_path(str(asset.get("file_path") or ""))
        if style_key is not None:
            return style_key
    raise RuntimeError(
        "OVERLAY_MATCH_MODE=by_style requires style-resolvable footage file_path (genre/tag) "
        "for the selected footage clips"
    )


def pick_overlay_asset_deterministic(
    *,
    overlay_assets: List[Dict[str, Any]],
    match_mode: str,
    target_style_key: Optional[Tuple[str, str]],
    seed_key: str,
) -> Dict[str, Any]:
    if match_mode == "global":
        pool = list(overlay_assets)
    elif match_mode == "by_style":
        if target_style_key is None:
            raise RuntimeError("OVERLAY_MATCH_MODE=by_style requires target style key")
        g, t = target_style_key
        pool = [
            a for a in overlay_assets
            if str(a.get("genre") or "") == str(g) and str(a.get("tag") or "") == str(t)
        ]
        if not pool:
            raise RuntimeError(
                f"No overlays for style genre={g!r} tag={t!r} in OVERLAY_INVENTORY_JSON"
            )
    else:
        raise RuntimeError(f"Unsupported overlay match mode: {match_mode!r}")

    digest = hashlib.sha256(f"{seed_key}|overlay".encode("utf-8")).digest()
    idx = int.from_bytes(digest[:8], byteorder="big", signed=False) % len(pool)
    return dict(pool[idx])


def build_overlay_tiled_layers(*, overlay_asset: Dict[str, Any], clip_dur: float) -> List[Dict[str, Any]]:
    dur = _as_pos_float(overlay_asset.get("duration_sec"))
    if dur is None:
        raise RuntimeError(f"overlay duration_sec must be > 0: {overlay_asset!r}")
    if clip_dur <= 0.0:
        raise RuntimeError(f"clip_dur must be > 0, got {clip_dur}")

    file_name = str(overlay_asset.get("file_name") or "").strip()
    file_path = str(overlay_asset.get("file_path") or "").strip()
    src_w = int(overlay_asset.get("src_w") or 0)
    src_h = int(overlay_asset.get("src_h") or 0)
    if not file_name or not file_path or src_w <= 0 or src_h <= 0:
        raise RuntimeError(f"Invalid overlay asset payload: {overlay_asset!r}")

    out: List[Dict[str, Any]] = []
    t0 = 0.0
    idx = 0
    while t0 < float(clip_dur) - 1e-9:
        t1 = min(float(clip_dur), float(t0 + dur))
        out.append(
            {
                "layer_id": f"overlay_{idx}",
                "name": f"overlay_{idx}_{file_name}",
                "file_name": file_name,
                "file_path": file_path,
                "src_w": int(src_w),
                "src_h": int(src_h),
                "fit_mode": "cover",
                "in_point": float(t0),
                "out_point": float(t1),
                "start_time": float(t0),
                "enabled": True,
                "audio_enabled": False,
                "video_enabled": True,
                "target_comp": "Comp 1",
            }
        )
        idx += 1
        t0 = t1
    return out


# -------------------------
# Subtitles sanitation (minimal + deterministic)
# -------------------------
def _legacy_repair_mine_drop_to_mine(d: Dict[str, Any]) -> None:
    """
    Если вдруг где-то прилетел старый формат mine_drop, конвертим в новый mine.
    """
    b5 = d.get("block_5")
    if not isinstance(b5, dict):
        return

    if isinstance(b5.get("mine"), dict):
        return

    md = b5.get("mine_drop")
    if not isinstance(md, dict):
        return

    txt = str(md.get("text", "") or "")
    try:
        t0 = float(md.get("t_start"))
        t1 = float(md.get("t_end"))
    except Exception:
        return

    b5["mine"] = {
        "phrase": ("\r" + txt) if txt else "",
        "tokens": [{"text": txt, "t_start": t0, "t_end": t1, "trailing": ""}],
    }


def sanitize_subtitles_dict_inplace(d: Dict[str, Any]) -> Dict[str, Any]:
    _legacy_repair_mine_drop_to_mine(d)

    if isinstance(d.get("block_1"), dict):
        normalize_segment_inplace(d["block_1"], force_two_line=False, mine_mode=False)

    b2 = d.get("block_2")
    if isinstance(b2, dict):
        if isinstance(b2.get("p1"), dict):
            normalize_segment_inplace(b2["p1"], force_two_line=False, mine_mode=False)
        if isinstance(b2.get("p2"), dict):
            normalize_segment_inplace(b2["p2"], force_two_line=False, mine_mode=False)

    if isinstance(d.get("block_3"), dict):
        normalize_segment_inplace(d["block_3"], force_two_line=False, mine_mode=False)

    b4 = d.get("block_4")
    if isinstance(b4, dict):
        if isinstance(b4.get("p1"), dict):
            normalize_segment_inplace(b4["p1"], force_two_line=False, mine_mode=False)
        if isinstance(b4.get("p2"), dict):
            normalize_segment_inplace(b4["p2"], force_two_line=False, mine_mode=False)

    b5 = d.get("block_5")
    if isinstance(b5, dict):
        for k in ("slowly_in", "fast_reveal", "glitch_peak"):
            if isinstance(b5.get(k), dict):
                normalize_segment_inplace(b5[k], force_two_line=False, mine_mode=False)
        if isinstance(b5.get("mine"), dict):
            normalize_segment_inplace(b5["mine"], force_two_line=False, mine_mode=True)

    if isinstance(d.get("block_6"), dict):
        normalize_segment_inplace(d["block_6"], force_two_line=False, mine_mode=False)

    b7 = d.get("block_7")
    if isinstance(b7, dict):
        if isinstance(b7.get("part1"), dict):
            normalize_segment_inplace(b7["part1"], force_two_line=False, mine_mode=False)
        if isinstance(b7.get("part2"), dict):
            normalize_segment_inplace(b7["part2"], force_two_line=False, mine_mode=False)

    def _uppercase_segment(seg: Dict[str, Any]) -> None:
        phrase = seg.get("phrase")
        if phrase is not None:
            seg["phrase"] = str(phrase).upper()
        toks = seg.get("tokens")
        if isinstance(toks, list):
            for t in toks:
                if not isinstance(t, dict):
                    continue
                if "text" in t and t.get("text") is not None:
                    t["text"] = str(t.get("text")).upper()

    if isinstance(d.get("block_1"), dict):
        _uppercase_segment(d["block_1"])
    if isinstance(b2, dict):
        if isinstance(b2.get("p1"), dict):
            _uppercase_segment(b2["p1"])
        if isinstance(b2.get("p2"), dict):
            _uppercase_segment(b2["p2"])
    if isinstance(d.get("block_3"), dict):
        _uppercase_segment(d["block_3"])
    if isinstance(b4, dict):
        if isinstance(b4.get("p1"), dict):
            _uppercase_segment(b4["p1"])
        if isinstance(b4.get("p2"), dict):
            _uppercase_segment(b4["p2"])
    if isinstance(b5, dict):
        for k in ("slowly_in", "fast_reveal", "glitch_peak", "mine"):
            if isinstance(b5.get(k), dict):
                _uppercase_segment(b5[k])
    if isinstance(d.get("block_6"), dict):
        _uppercase_segment(d["block_6"])
    if isinstance(b7, dict):
        if isinstance(b7.get("part1"), dict):
            _uppercase_segment(b7["part1"])
        if isinstance(b7.get("part2"), dict):
            _uppercase_segment(b7["part2"])

    # deterministic layout pass:
    # - puts \r only where style contract expects it
    # - recomputes trailing from token words
    patch_payload_dict_inplace(d)
    return d


def _shift_token(t: Token, delta: float) -> Token:
    return Token(
        text=t.text,
        t_start=float(t.t_start) - delta,
        t_end=float(t.t_end) - delta,
        trailing=t.trailing,
    )


def normalize_subtitles_to_clip_zero(payload_abs: BlocksTokensPayload, clip_start_abs: float, clip_end_abs: float) -> BlocksTokensPayload:
    """
    Convert ABSOLUTE (full-track) token times to CLIP-ZERO times by subtracting clip_start_abs.
    The resulting payload.clip becomes [0..clip_duration].
    """
    clip_start = float(clip_start_abs)
    clip_end = float(clip_end_abs)
    clip_dur = clip_end - clip_start

    def seg_phrase_tokens(seg):
        return {"phrase": seg.phrase, "tokens": [_shift_token(t, clip_start) for t in seg.tokens]}

    norm_dict: Dict[str, Any] = {
        "clip": {"start": 0.0, "end": float(clip_dur)},

        "block_1": {"phrase": payload_abs.block_1.phrase, "tokens": [_shift_token(t, clip_start) for t in payload_abs.block_1.tokens]},
        "block_2": {"p1": seg_phrase_tokens(payload_abs.block_2.p1), "p2": seg_phrase_tokens(payload_abs.block_2.p2)},
        "block_3": {"phrase": payload_abs.block_3.phrase, "tokens": [_shift_token(t, clip_start) for t in payload_abs.block_3.tokens]},
        "block_4": {"p1": seg_phrase_tokens(payload_abs.block_4.p1), "p2": seg_phrase_tokens(payload_abs.block_4.p2)},
        "block_5": {
            "slowly_in": seg_phrase_tokens(payload_abs.block_5.slowly_in),
            "fast_reveal": seg_phrase_tokens(payload_abs.block_5.fast_reveal),
            "glitch_peak": seg_phrase_tokens(payload_abs.block_5.glitch_peak),
            "mine": seg_phrase_tokens(payload_abs.block_5.mine),
        },
        "block_6": {"phrase": payload_abs.block_6.phrase, "tokens": [_shift_token(t, clip_start) for t in payload_abs.block_6.tokens]},
        "block_7": {"part1": seg_phrase_tokens(payload_abs.block_7.part1), "part2": seg_phrase_tokens(payload_abs.block_7.part2)},
    }

    return BlocksTokensPayload.model_validate(norm_dict)


# -------------------------
# Footage: absolute -> clip-zero (comp) + coverage checks
# -------------------------
def _shift_footage_to_clip_zero(
    footage_abs: FootageSelectionPayload,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
) -> FootageSelectionPayload:
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    dur = ce - cs
    if dur <= 0:
        raise ValueError(f"Invalid clip window for footage shift: {cs}..{ce}")

    clips_in = list(footage_abs.clips)
    clips_in.sort(key=lambda c: float(c.in_point))

    shifted_clips: List[Dict[str, Any]] = []
    for c in clips_in:
        in0 = float(c.in_point) - cs
        out0 = float(c.out_point) - cs

        if in0 < -1e-6 or out0 > dur + 1e-6:
            raise ValueError(
                f"Footage clip out of audio window after shift: "
                f"abs={c.in_point}..{c.out_point} window={cs}..{ce} -> rel={in0}..{out0}"
            )

        shifted_clips.append(
            {
                "file_name": c.file_name,
                "fit_mode": c.fit_mode,
                "in_point": in0,
                "out_point": out0,
                "start_time": in0,
            }
        )

    payload = {"clips": shifted_clips, "allow_gaps": False}
    shifted = FootageSelectionPayload.model_validate(payload)

    clips = list(shifted.clips)
    clips.sort(key=lambda c: float(c.in_point))
    if not clips:
        raise ValueError("FootageSelectionPayload.clips is empty after shift")

    if abs(float(clips[0].in_point) - 0.0) > 1e-6:
        raise ValueError(f"Footage must start at 0.0 (got {clips[0].in_point})")

    for i in range(len(clips) - 1):
        a = clips[i]
        b = clips[i + 1]
        if abs(float(b.in_point) - float(a.out_point)) > 1e-6:
            raise ValueError(
                f"Footage gap/overlap detected: clip[{i}].out={a.out_point} != clip[{i+1}].in={b.in_point}"
            )

    if abs(float(clips[-1].out_point) - float(dur)) > 1e-6:
        raise ValueError(f"Footage must end at duration={dur} (got {clips[-1].out_point})")

    return shifted


def load_assets_map_from_inventory(footage_inventory_json: Path) -> Dict[str, Dict[str, Any]]:
    inv = json.loads(footage_inventory_json.read_text(encoding="utf-8"))

    assets = inv.get("assets")
    if isinstance(assets, list):
        out: Dict[str, Dict[str, Any]] = {}
        for it in assets:
            if not isinstance(it, dict):
                continue
            fn = str(it.get("file_name") or "").strip()
            fp = str(it.get("file_path") or "").strip()
            sw = it.get("src_w")
            sh = it.get("src_h")
            if not fn or not fp or sw is None or sh is None:
                continue
            out[fn] = {"file_name": fn, "file_path": fp, "src_w": int(sw), "src_h": int(sh)}
        return out

    layers = list(inv.get("layers") or [])
    out2: Dict[str, Dict[str, Any]] = {}
    for it in layers:
        if not isinstance(it, dict):
            continue
        if str(it.get("type")) != "footage":
            continue
        fn = str(it.get("file_name") or it.get("name") or "").strip()
        fp = str(it.get("file_path") or "").strip()
        sw = it.get("src_w")
        sh = it.get("src_h")
        if not fn or not fp or sw is None or sh is None:
            continue
        if fn not in out2:
            out2[fn] = {"file_name": fn, "file_path": fp, "src_w": int(sw), "src_h": int(sh)}
    return out2


def read_adjustment_preset_from_inventory(footage_inventory_json: Path) -> Dict[str, Any]:
    inv = json.loads(footage_inventory_json.read_text(encoding="utf-8"))
    preset = inv.get("adjustment_preset") or {}
    if not isinstance(preset, dict) or not preset:
        preset = {
            "id": "ADJ_LAYER_16",
            "name": "Adjustment Layer 16",
            "dump_file": "data/0_4.504505__Adjustment Layer 16__adjustment.json",
            "time_warp_mode": "pin_edges_v1",
        }
    return preset


def _resolve_data_dir(repo_root: Path, data_dir: Path | None) -> Path:
    if data_dir is not None:
        return data_dir.resolve()

    raw = (os.environ.get("DATA_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        return p.resolve()

    return (repo_root / "data").resolve()


def render_all_steps(
    *,
    repo_root: Path,
    plan: FullPlanPayload,
    footage_inventory_json: Path,
    out_dir: Path,
    data_dir: Path | None = None,
) -> Dict[str, Path]:
    repo_root = repo_root.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    data_dir = _resolve_data_dir(repo_root, data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    env = _env(repo_root)

    # -------------------------
    # STEP 1 (deterministic AE mapping)
    # -------------------------
    # IMPORTANT: Stage3 must treat Stage2 subtitles clip window as the single source of truth.
    # Stage1 audio window may be stale if Stage2 was re-generated/edited.
    subs_dict = plan.subtitles.model_dump(mode="json")
    sanitize_subtitles_dict_inplace(subs_dict)
    subs_abs = BlocksTokensPayload.model_validate(subs_dict)

    clip_start = float(subs_abs.clip.start)
    clip_end = float(subs_abs.clip.end)
    clip_dur = clip_end - clip_start
    if clip_dur <= 0:
        raise ValueError(f"Invalid subtitles clip window: {clip_start}..{clip_end}")

    # Deterministic diagnostics: log mismatch, but always follow Stage2.
    try:
        s1s = float(plan.audio.clip_start_abs)
        s1e = float(plan.audio.clip_end_abs)
        if abs(s1s - clip_start) > 1e-6 or abs(s1e - clip_end) > 1e-6:
            print(
                "[stage3] clip window override: "
                f"stage1_audio={s1s}..{s1e} "
                f"stage2_subtitles={clip_start}..{clip_end} "
                "(using stage2_subtitles)"
            )
    except Exception:
        # Never fail Stage3 due to diagnostics.
        pass

    audio_obj = {
        "audio": {
            "clip_start_abs": clip_start,
            "clip_end_abs": clip_end,
            "layer_start_time": -clip_start,
            "layer_in_point": 0.0,
            "layer_out_point": float(clip_dur),
            "moment_of_interest_sec": plan.audio.moment_of_interest_sec,
        }
    }

    # -------------------------
    # STEP 2 (absolute -> clip-zero)
    # -------------------------
    subs_clip_zero = normalize_subtitles_to_clip_zero(
        subs_abs,
        clip_start_abs=clip_start,
        clip_end_abs=clip_end,
    )

    timings, comp_dur = compute_timings(subs_clip_zero)

    t2 = env.get_template("step2_template.j2")
    full_edit_str = t2.render(
        fps=float(AE_FPS),
        comp_dur=float(comp_dur),
        t=timings,
        blocks=subs_clip_zero.model_dump(mode="json"),
    )
    full_edit_obj = json.loads(full_edit_str)

    # -------------------------
    # STEP 3 (footage): absolute -> clip-zero (comp)
    # -------------------------
    footage_clip_zero = _shift_footage_to_clip_zero(
        plan.footage,
        clip_start_abs=clip_start,
        clip_end_abs=clip_end,
    )

    # -------------------------
    # Render footage_config.json for AE
    # -------------------------
    assets_map = load_assets_map_from_inventory(footage_inventory_json)
    preset = read_adjustment_preset_from_inventory(footage_inventory_json)
    overlay_layers: List[Dict[str, Any]] = []

    if _overlay_enabled():
        overlay_inventory_path = _resolve_overlay_inventory_path(repo_root)
        overlay_assets = load_overlay_assets_from_inventory(overlay_inventory_path)
        overlay_mode = _overlay_match_mode()
        target_style_key: Optional[Tuple[str, str]] = None
        if overlay_mode == "by_style":
            target_style_key = _resolve_selected_footage_style_key(
                footage_abs=footage_clip_zero,
                assets_map=assets_map,
            )
        seed_key = _overlay_seed_key(out_dir)
        overlay_asset = pick_overlay_asset_deterministic(
            overlay_assets=overlay_assets,
            match_mode=overlay_mode,
            target_style_key=target_style_key,
            seed_key=seed_key,
        )
        overlay_layers = build_overlay_tiled_layers(
            overlay_asset=overlay_asset,
            clip_dur=float(clip_dur),
        )

    audio_file_name, audio_file_path_local = _resolve_audio_source(repo_root)

    # ✅ Windows/appdir mode: keep file_name, but blank file_path so JSX resolves via APP_DIR/media/audio/<file_name>
    mode = _media_mode()
    if mode in {"appdir", "win", "windows"}:
        audio_file_path = ""
    else:
        audio_file_path = audio_file_path_local

    t3 = env.get_template("step3_template.j2")
    footage_str = t3.render(
        main_comp_w=1080,
        main_comp_h=1960,
        main_comp_fps=float(AE_FPS),  # ✅ FIX: was missing (caused UndefinedError)
        text_dur_hint=float(comp_dur),
        adjustment_preset=preset,
        footage=footage_clip_zero,
        assets_map=assets_map,
        overlay_layers=overlay_layers,
        audio=audio_obj["audio"],
        audio_file_name=audio_file_name,
        audio_file_path=audio_file_path,
    )
    footage_obj = json.loads(footage_str)

    # -------------------------
    # Write to DATA_DIR + mirror to OUT_DIR
    # -------------------------
    p_audio = data_dir / "audio_plan.json"
    p_step2 = data_dir / "full_edit_config.json"
    p_step3 = data_dir / "footage_config.json"

    p_audio.write_text(json.dumps(audio_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    p_step2.write_text(json.dumps(full_edit_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    p_step3.write_text(json.dumps(footage_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    (out_dir / "audio_plan.json").write_text(json.dumps(audio_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "full_edit_config.json").write_text(json.dumps(full_edit_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "footage_config.json").write_text(json.dumps(footage_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "audio_plan": p_audio,
        "full_edit_config": p_step2,
        "footage_config": p_step3,
    }
