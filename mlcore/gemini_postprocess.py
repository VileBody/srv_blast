# mlcore/gemini_postprocess.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
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
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        if not p.exists():
            raise FileNotFoundError(f"AUDIO_FILE_PATH points to missing file: {p}")
        return p.name, str(p)

    audio_dir = Path(os.environ.get("AUDIO_DIR", str(repo_root / "audio"))).resolve()
    files = _pick_audio_files(audio_dir)
    if not files:
        raise FileNotFoundError(
            "No audio file found. Set AUDIO_FILE_PATH in .env "
            "or put an audio file into repo_root/audio/"
        )
    p0 = files[0].resolve()
    return p0.name, str(p0)


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


def read_adj16_base_start_time(repo_root: Path, dump_file: str) -> float:
    p = (repo_root / dump_file).resolve()
    d = json.loads(p.read_text(encoding="utf-8"))
    return float(d["meta"]["startTime"])


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
    clip_start = float(plan.audio.clip_start_abs)
    clip_end = float(plan.audio.clip_end_abs)
    clip_dur = clip_end - clip_start
    if clip_dur <= 0:
        raise ValueError(f"Invalid audio clip window: {clip_start}..{clip_end}")

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
    subs_dict = plan.subtitles.model_dump(mode="json")
    sanitize_subtitles_dict_inplace(subs_dict)
    subs_abs = BlocksTokensPayload.model_validate(subs_dict)

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
    dump_file = str(preset.get("dump_file") or "data/0_4.504505__Adjustment Layer 16__adjustment.json")
    base_adj_start = read_adj16_base_start_time(repo_root, dump_file)

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
        base_adj_start_time=float(base_adj_start),
        footage=footage_clip_zero,
        assets_map=assets_map,
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
