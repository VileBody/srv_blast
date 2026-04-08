# mlcore/gemini_postprocess.py
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import os

from jinja2 import Environment, FileSystemLoader

from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS, normalize_subtitles_mode
from mlcore.cr_patch import normalize_segment_inplace, patch_payload_dict_inplace
from mlcore.timing_calc import compute_timings
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.subtitles_flow import SubtitleFlowPlan
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
    # Product decision: overlays are globally disabled for all templates.
    # Keep helper explicit to avoid accidental re-enable via environment.
    return False


def _overlay_source_mode() -> str:
    raw = (os.environ.get("OVERLAY_SOURCE_MODE") or "inventory").strip().lower()
    if raw not in {"inventory", "s3_prefix"}:
        raise RuntimeError("OVERLAY_SOURCE_MODE must be one of: inventory | s3_prefix")
    return raw


def _overlay_match_mode() -> str:
    raw = (os.environ.get("OVERLAY_MATCH_MODE") or "by_style").strip().lower()
    if raw not in {"by_style", "global"}:
        raise RuntimeError("OVERLAY_MATCH_MODE must be one of: by_style | global")
    return raw


def _overlay_s3_bucket() -> str:
    direct = (os.environ.get("OVERLAY_S3_BUCKET") or "").strip()
    if direct:
        return direct
    inherited = (os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
    if inherited:
        return inherited
    raise RuntimeError("OVERLAY_SOURCE_MODE=s3_prefix requires OVERLAY_S3_BUCKET or S3_BUCKET_ASSET_STORAGE")


def _overlay_s3_prefix() -> str:
    raw = (os.environ.get("OVERLAY_S3_PREFIX") or "overlays/").strip().strip("/")
    if not raw:
        raise RuntimeError("OVERLAY_SOURCE_MODE=s3_prefix requires non-empty OVERLAY_S3_PREFIX")
    return raw + "/"


def _overlay_target_size() -> Tuple[int, int]:
    tw = int((os.environ.get("TARGET_WIDTH") or "1080").strip() or "1080")
    th = int((os.environ.get("TARGET_HEIGHT") or "1920").strip() or "1920")
    if tw <= 0 or th <= 0:
        raise RuntimeError(f"TARGET_WIDTH/TARGET_HEIGHT must be positive, got {tw}x{th}")
    return tw, th


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


def _make_overlay_s3_client():
    # Lazy import so local/dev runs without overlay s3 mode don't require boto3 at import time.
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore

    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    if bool(access_key) != bool(secret_key):
        raise RuntimeError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be both set or both empty")

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def _iter_overlay_s3_keys(*, s3_client: Any, bucket: str, prefix: str) -> List[str]:
    video_exts = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
    out: List[str] = []
    token: Optional[str] = None

    while True:
        req: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            req["ContinuationToken"] = token
        resp = s3_client.list_objects_v2(**req)
        contents = resp.get("Contents")
        if isinstance(contents, list):
            for row in contents:
                key = str((row or {}).get("Key") or "").strip()
                if not key or key.endswith("/"):
                    continue
                if Path(key).suffix.lower() not in video_exts:
                    continue
                out.append(key)

        if not bool(resp.get("IsTruncated")):
            break
        token = str(resp.get("NextContinuationToken") or "").strip() or None
        if token is None:
            break

    return out


def _collect_overlay_metadata_indexes(repo_root: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_url: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}

    candidates = [
        (os.environ.get("FOOTAGE_INVENTORY_JSON") or "").strip(),
        (os.environ.get("STATIC_ASSETS_INDEX_JSON") or "").strip(),
        str((repo_root / "data" / "footage_inventory.json").resolve()),
        str((repo_root / "data" / "static_assets_index.json").resolve()),
    ]

    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        if not p.exists():
            continue

        obj = json.loads(p.read_text(encoding="utf-8"))
        assets = obj.get("assets") if isinstance(obj, dict) else None
        if not isinstance(assets, list):
            continue

        for it in assets:
            if not isinstance(it, dict):
                continue
            file_name = str(it.get("file_name") or "").strip()
            file_path = str(it.get("file_path") or "").strip()
            if not file_name and not file_path:
                continue

            src_w = int(it.get("src_w") or 0)
            src_h = int(it.get("src_h") or 0)
            duration_sec = _as_pos_float(it.get("duration_sec"))

            genre = str(it.get("genre") or "").strip() or None
            tag = str(it.get("tag") or "").strip() or None
            if (genre is None or tag is None) and file_path:
                parsed = _style_from_file_path(file_path)
                if parsed is not None:
                    genre, tag = parsed

            meta = {
                "src_w": src_w,
                "src_h": src_h,
                "duration_sec": duration_sec,
                "genre": genre,
                "tag": tag,
            }

            if file_path.startswith("s3://"):
                by_url[file_path] = meta
            if file_name and file_name not in by_name:
                by_name[file_name] = meta

    return by_url, by_name


def load_overlay_assets_from_s3_prefix(*, repo_root: Path, bucket: str, prefix: str) -> List[Dict[str, Any]]:
    s3_client = _make_overlay_s3_client()
    keys = _iter_overlay_s3_keys(s3_client=s3_client, bucket=bucket, prefix=prefix)
    if not keys:
        raise RuntimeError(f"No overlay videos found in s3://{bucket}/{prefix}")

    by_url, by_name = _collect_overlay_metadata_indexes(repo_root)
    out: List[Dict[str, Any]] = []

    for key in sorted(set(keys)):
        file_name = Path(key).name
        if not file_name:
            continue
        file_path = f"s3://{bucket}/{key}"

        meta = by_url.get(file_path) or by_name.get(file_name) or {}
        src_w = int(meta.get("src_w") or 0)
        src_h = int(meta.get("src_h") or 0)
        duration_sec = _as_pos_float(meta.get("duration_sec"))
        genre = str(meta.get("genre") or "").strip() or None
        tag = str(meta.get("tag") or "").strip() or None
        if (genre is None or tag is None):
            parsed = _style_from_file_path(file_path)
            if parsed is not None:
                genre, tag = parsed

        out.append(
            {
                "file_name": file_name,
                "file_path": file_path,
                "src_w": src_w,
                "src_h": src_h,
                "duration_sec": float(duration_sec) if duration_sec is not None else None,
                "genre": genre,
                "tag": tag,
            }
        )

    if not out:
        raise RuntimeError(f"No valid overlay assets in s3://{bucket}/{prefix}")
    return out


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
                f"No overlays for style genre={g!r} tag={t!r} in overlay assets source"
            )
    else:
        raise RuntimeError(f"Unsupported overlay match mode: {match_mode!r}")

    digest = hashlib.sha256(f"{seed_key}|overlay".encode("utf-8")).digest()
    idx = int.from_bytes(digest[:8], byteorder="big", signed=False) % len(pool)
    return dict(pool[idx])


def build_overlay_tiled_layers(*, overlay_asset: Dict[str, Any], clip_dur: float) -> List[Dict[str, Any]]:
    if clip_dur <= 0.0:
        raise RuntimeError(f"clip_dur must be > 0, got {clip_dur}")
    dur = _as_pos_float(overlay_asset.get("duration_sec"))

    file_name = str(overlay_asset.get("file_name") or "").strip()
    file_path = str(overlay_asset.get("file_path") or "").strip()
    src_w = int(overlay_asset.get("src_w") or 0)
    src_h = int(overlay_asset.get("src_h") or 0)
    if src_w <= 0 or src_h <= 0:
        tw, th = _overlay_target_size()
        src_w, src_h = int(tw), int(th)
    if not file_name or not file_path:
        raise RuntimeError(f"Invalid overlay asset payload: {overlay_asset!r}")

    # Always tile overlays in AE using actual imported source.duration.
    # This avoids black seams when metadata duration drifts from factual media duration.
    # If metadata duration is known, keep it as a fallback hint for AE-side tiling.
    max_repeats = 100
    if dur is not None and dur > 0.0:
        needed = int(math.ceil(float(clip_dur) / float(dur))) + 2
        max_repeats = max(100, min(500, needed))

    return [
        {
            "layer_id": "overlay_0",
            "name": f"overlay_0_{file_name}",
            "file_name": file_name,
            "file_path": file_path,
            "src_w": int(src_w),
            "src_h": int(src_h),
            "duration_sec": float(dur) if dur is not None else None,
            "fit_mode": "cover",
            "in_point": 0.0,
            "out_point": float(clip_dur),
            "start_time": 0.0,
            "enabled": True,
            "audio_enabled": False,
            "video_enabled": True,
            "target_comp": "Comp 1",
            "tile_in_ae": True,
            "tile_max_repeats": int(max_repeats),
        }
    ]


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


def normalize_subtitle_flow_to_clip_zero(
    payload_abs: SubtitleFlowPlan,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
) -> SubtitleFlowPlan:
    clip_start = float(clip_start_abs)
    clip_end = float(clip_end_abs)
    clip_dur = clip_end - clip_start
    if clip_dur <= 0.0:
        raise ValueError(f"Invalid clip window for subtitle flow shift: {clip_start}..{clip_end}")

    segments: List[Dict[str, Any]] = []
    for seg in payload_abs.segments:
        tokens = [
            {
                "text": str(t.text),
                "t_start": float(t.t_start) - clip_start,
                "t_end": float(t.t_end) - clip_start,
            }
            for t in seg.tokens
        ]
        segments.append(
            {
                "id": str(seg.segment_id),
                "text": str(seg.text),
                "in_point": float(seg.in_point) - clip_start,
                "out_point": float(seg.out_point) - clip_start,
                "style_tag": str(seg.style_tag),
                "lines": [str(x) for x in seg.lines],
                "tokens": tokens,
                "focus_word": seg.focus_word,
                "focus_style": seg.focus_style,
            }
        )

    return SubtitleFlowPlan.model_validate(
        {
            "mode": str(payload_abs.mode),
            "clip": {"start": 0.0, "end": float(clip_dur)},
            "segments": segments,
        }
    )


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

        src_off = float(getattr(c, "source_offset_sec", 0.0) or 0.0)
        shifted_clips.append(
            {
                "file_name": c.file_name,
                "fit_mode": c.fit_mode,
                "in_point": in0,
                "out_point": out0,
                "source_offset_sec": src_off,
                "start_time": in0 - src_off,
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

    subtitles_mode = normalize_subtitles_mode(
        str(getattr(plan, "subtitles_mode", "") or ""),
        default=SUBTITLES_MODE_LEGACY_BLOCKS,
    )

    # -------------------------
    # STEP 1 (deterministic AE mapping)
    # -------------------------
    # IMPORTANT: Stage3 must treat Stage2 subtitles clip window as the single source of truth.
    # Stage1 audio window may be stale if Stage2 was re-generated/edited.
    subs_abs_legacy: BlocksTokensPayload | None = None
    flow_abs: SubtitleFlowPlan | None = None

    if subtitles_mode == SUBTITLES_MODE_LEGACY_BLOCKS:
        subs_dict = plan.subtitles.model_dump(mode="json")
        sanitize_subtitles_dict_inplace(subs_dict)
        subs_abs_legacy = BlocksTokensPayload.model_validate(subs_dict)
        clip_start = float(subs_abs_legacy.clip.start)
        clip_end = float(subs_abs_legacy.clip.end)
    else:
        if not isinstance(plan.subtitles, SubtitleFlowPlan):
            raise RuntimeError(
                f"subtitles_mode={subtitles_mode!r} requires SubtitleFlowPlan payload at stage3"
            )
        flow_abs = plan.subtitles
        if str(flow_abs.mode) != subtitles_mode:
            raise RuntimeError(
                f"subtitle flow mode mismatch at stage3: payload={flow_abs.mode!r} expected={subtitles_mode!r}"
            )
        clip_start = float(flow_abs.clip.start)
        clip_end = float(flow_abs.clip.end)

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
    if subtitles_mode == SUBTITLES_MODE_LEGACY_BLOCKS:
        if subs_abs_legacy is None:
            raise RuntimeError("legacy subtitles payload is empty at stage3")
        subs_clip_zero = normalize_subtitles_to_clip_zero(
            subs_abs_legacy,
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
    else:
        if flow_abs is None:
            raise RuntimeError("subtitle flow payload is empty at stage3")
        flow_clip_zero = normalize_subtitle_flow_to_clip_zero(
            flow_abs,
            clip_start_abs=clip_start,
            clip_end_abs=clip_end,
        )
        comp_dur = float(clip_dur)
        full_edit_obj = {
            "composition": {
                "fps": float(AE_FPS),
                "dur": float(comp_dur),
            },
            "subtitles_mode": subtitles_mode,
            "subtitle_flow_plan": flow_clip_zero.model_dump(mode="json"),
            "macro_blocks": [],
        }

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
        overlay_source_mode = _overlay_source_mode()
        if overlay_source_mode == "inventory":
            overlay_inventory_path = _resolve_overlay_inventory_path(repo_root)
            overlay_assets = load_overlay_assets_from_inventory(overlay_inventory_path)
        elif overlay_source_mode == "s3_prefix":
            overlay_assets = load_overlay_assets_from_s3_prefix(
                repo_root=repo_root,
                bucket=_overlay_s3_bucket(),
                prefix=_overlay_s3_prefix(),
            )
        else:
            raise RuntimeError(f"Unsupported OVERLAY_SOURCE_MODE: {overlay_source_mode!r}")

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
