from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from core.telegram_api import build_aiogram_session, make_telegram_api
from core.clip_window import CLIP_WINDOW_RANGE_S_LABEL
from core.filesystem_hygiene import cleanup_jobs_artifacts, cleanup_tmp_chat_dirs
from core.queue_estimate import format_queue_estimate_lines, pick_queue_estimate_job_id
from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    normalize_subtitles_mode,
)
from config.styles.artist_presets_loader import get_artists, get_genres

from config.styles.artist_presets_loader import get_artists, get_genres, get_preset
from config.styles.theme_groups import (
    get_artist_rotation_slots,
    get_rotation_slot,
    get_theme_groups,
)

from .audio_prepare import AudioPrepareResult, prepare_audio_best_effort
from .config import SETTINGS, Settings
from .orchestrator_client import OrchestratorClient
from .referral_store import ReferralStore
from .s3_client import S3Client, make_s3_url
from .state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_IDLE,
    STAGE_LOCKED,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_BG_COLOR,
    STAGE_WAIT_BG_MODE,
    STAGE_WAIT_FOOTAGE_ARTIST,
    STAGE_WAIT_FOOTAGE_GENRE,
    STAGE_WAIT_FRAGMENT_CHOICE,
    STAGE_WAIT_TIMING_CHOICE,
    STAGE_WAIT_TIMING_INPUT,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
    STAGE_WAIT_NEXT,
    STAGE_WAIT_SUBTITLES_MODE,
    STAGE_WAIT_VERSIONS,
    STAGE_WAITING_REFERRAL,
)
from .user_store import UserStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_bot: %(message)s",
)
log = logging.getLogger("tg_bot")


BTN_SEND_TRACK = "Отправить трек"
BTN_SEND_LYRICS = "Отправить текст"
BTN_SKIP_LYRICS = "Не присылать текст"
BTN_SEND_FRAGMENT = "Отправить интересующий фрагмент"
BTN_SKIP_FRAGMENT = "На усмотрение ИИ"
BTN_SET_TIMING = "Указать тайминг"
BTN_SKIP_TIMING = "Весь трек / на усмотрение ИИ"
BTN_BACK = "Назад"
BTN_BG_FOOTAGE = "Футажи"
BTN_BG_SOLID = "Цветной фон"
BTN_BG_WHITE = "Белый"
BTN_BG_BLACK = "Чёрный"
BTN_BG_GREEN = "Зелёный (хромакей)"
BTN_LAUNCH = "Запустить"
BTN_NEXT = "Сделать следующий"
BTN_VER_1 = "1"
BTN_VER_2 = "2"
BTN_VER_3 = "3"
BTN_VER_4 = "4"
BTN_VER_5 = "5"
BTN_SUB_MODE_LEGACY = "Обычные blocks"
BTN_SUB_MODE_IMPULSE = "Impulse 2nd"
BTN_SUB_MODE_SCENES = "Scenes 3rd"
BTN_SUB_MODE_SCENES_SINGLE = "Scenes 3rd Single-Step"
BTN_SUB_MODE_4TH = "Template 4th"
VERSION_BUTTONS = [BTN_VER_1, BTN_VER_2, BTN_VER_3, BTN_VER_4, BTN_VER_5]
SUBTITLES_MODE_BUTTONS = [
    BTN_SUB_MODE_LEGACY,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_SUB_MODE_SCENES_SINGLE,
    BTN_SUB_MODE_4TH,
]
_SUBTITLES_MODE_BY_BUTTON = {
    BTN_SUB_MODE_LEGACY: SUBTITLES_MODE_LEGACY_BLOCKS,
    BTN_SUB_MODE_IMPULSE: SUBTITLES_MODE_IMPULSE_2ND,
    BTN_SUB_MODE_SCENES: SUBTITLES_MODE_SCENES_3RD,
    BTN_SUB_MODE_SCENES_SINGLE: SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    BTN_SUB_MODE_4TH: SUBTITLES_MODE_TEMPLATE_4TH,
}
_CONTROL_BUTTONS = {
    BTN_SEND_TRACK,
    BTN_SEND_LYRICS,
    BTN_SKIP_LYRICS,
    BTN_SEND_FRAGMENT,
    BTN_SKIP_FRAGMENT,
    BTN_SET_TIMING,
    BTN_SKIP_TIMING,
    BTN_BACK,
    BTN_BG_FOOTAGE,
    BTN_BG_SOLID,
    BTN_BG_WHITE,
    BTN_BG_BLACK,
    BTN_BG_GREEN,
    BTN_SUB_MODE_LEGACY,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_SUB_MODE_SCENES_SINGLE,
    BTN_SUB_MODE_4TH,
    BTN_LAUNCH,
    BTN_NEXT,
    *VERSION_BUTTONS,
}


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
_RE_CELERY_RETRIES = re.compile(r"\bretries=(\d+)\b")
_TG_AUDIO_DOWNLOAD_RETRIES = 3
_TG_AUDIO_DOWNLOAD_TIMEOUT_S = 180.0
_TG_AUDIO_DOWNLOAD_BACKOFF_BASE_S = 2.0
_TG_VIDEO_COMPRESS_CRF_STEPS = (30, 32, 34, 36)


def _kb(*rows: list[str]) -> ReplyKeyboardMarkup:
    keyboard = []
    for row in rows:
        keyboard.append([KeyboardButton(text=str(x)) for x in row])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def _safe_name(name: str) -> str:
    out = []
    for ch in str(name or ""):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "audio.bin"


def _now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _extract_audio_spec(message: Message) -> Optional[Tuple[str, str]]:
    if message.audio:
        file_id = str(message.audio.file_id)
        file_name = str(message.audio.file_name or "audio.mp3")
        return file_id, file_name

    if message.document:
        file_id = str(message.document.file_id)
        mime = str(message.document.mime_type or "").lower()
        file_name = str(message.document.file_name or "audio.bin")
        ext = Path(file_name).suffix.lower()
        if mime.startswith("audio/") or ext in _AUDIO_EXTS:
            return file_id, file_name

    return None


def _is_tg_file_too_big_error(err: Exception) -> bool:
    msg = str(err or "").lower()
    return "file is too big" in msg


def _resolve_job_video_source(job: dict[str, Any], settings: Settings) -> str:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    windows = result.get("windows") if isinstance(result.get("windows"), dict) else {}

    candidates = [
        str(result.get("output_url") or "").strip(),
        str(windows.get("output_url") or "").strip(),
        str(windows.get("output_s3_url") or "").strip(),
    ]
    for u in candidates:
        if u:
            return u

    bucket = str(settings.s3_bucket_output_video or "").strip()
    job_id = str(job.get("job_id") or "").strip()
    if bucket and job_id:
        return make_s3_url(bucket, f"renders/{job_id}/output.mp4")

    return ""


def _extract_project_archive_source(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    direct_candidates = [
        payload.get("project_archive_url"),
        payload.get("artifacts_s3_uri"),
        payload.get("artifacts_s3_url"),
        payload.get("artifacts_url"),
    ]
    for raw in direct_candidates:
        u = str(raw or "").strip()
        if u.startswith("s3://") or u.startswith("http://") or u.startswith("https://"):
            return u

    msg = str(payload.get("message") or "").strip()
    if not msg:
        return ""
    m = re.search(r"artifacts=(s3://[^;\s]+|https?://[^;\s]+)", msg, flags=re.IGNORECASE)
    if not m:
        return ""
    u = str(m.group(1) or "").strip().rstrip(".,;")
    if u.startswith("s3://") or u.startswith("http://") or u.startswith("https://"):
        return u
    return ""


def _resolve_job_project_archive_source(job: dict[str, Any]) -> str:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    windows = result.get("windows") if isinstance(result.get("windows"), dict) else {}

    candidates = [
        _extract_project_archive_source(result),
        _extract_project_archive_source(windows),
    ]
    for u in candidates:
        if u:
            return u
    return ""


def _compact_text(s: str, *, limit: int = 500) -> str:
    t = " ".join(str(s or "").split())
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 3)] + "..."


def _extract_celery_retries(error_text: str) -> Optional[int]:
    m = _RE_CELERY_RETRIES.search(str(error_text or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _mask_proxy_url(raw: str) -> str:
    proxy = str(raw or "").strip()
    if not proxy:
        return ""
    # Keep only scheme/host[:port] in logs to avoid leaking credentials.
    m = re.match(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*):\/\/(?P<rest>.+)$", proxy)
    if not m:
        return "<redacted>"
    scheme = str(m.group("scheme") or "").lower()
    rest = str(m.group("rest") or "")
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    host_port = rest.split("/", 1)[0]
    return f"{scheme}://{host_port}"


_SCENES_STYLE_TAGS = {"TYPE_1", "TYPE_2", "TYPE_3", "TYPE_4", "TYPE_5", "TYPE_6"}
_IMPULSE_STYLE_TAGS = {"long", "short"}
_TEMPLATE_4TH_STYLE_TAGS = {"TAPE_4TH"}


def _to_float_or_none(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _fmt_sec(v: Any) -> str:
    n = _to_float_or_none(v)
    if n is None:
        return "n/a"
    return f"{n:.3f}"


def _load_json_dict(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
        return None
    except Exception:
        return None


def _jobs_output_roots() -> List[Path]:
    roots: List[Path] = []
    raw_env_root = str(os.environ.get("BOT_JOBS_OUTPUT_DIR") or "").strip()
    if raw_env_root:
        roots.append(Path(raw_env_root).expanduser())
    roots.append(Path("/app/output/jobs"))
    roots.append(Path.cwd() / "output" / "jobs")

    seen: set[str] = set()
    out: List[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _logs_dir_candidates_for_job(job_id: str) -> List[Path]:
    jid = str(job_id or "").strip()
    if not jid:
        return []
    return [root / jid / "out" / "logs" for root in _jobs_output_roots()]


def _latest_file_by_pattern(*, directory: Path, pattern: str) -> Optional[Path]:
    try:
        matches = [p for p in directory.glob(pattern) if p.is_file()]
    except Exception:
        return None
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def _pick_stage2_payload_files_for_job(job_id: str) -> Tuple[Optional[Path], Optional[Path]]:
    for logs_dir in _logs_dir_candidates_for_job(job_id):
        if not logs_dir.exists() or not logs_dir.is_dir():
            continue

        final_path = logs_dir / "stage2_subtitles.json"
        if not final_path.exists():
            final_path = _latest_file_by_pattern(directory=logs_dir, pattern="stage2_subtitles_*.json") or final_path
            if not final_path.exists():
                final_path = None

        raw_path = _latest_file_by_pattern(directory=logs_dir, pattern="gemini_raw_stage2_subtitles_*.json")
        if final_path is not None or raw_path is not None:
            return final_path, raw_path
    return None, None


def _pick_stage2_footage_file_for_job(job_id: str) -> Optional[Path]:
    for logs_dir in _logs_dir_candidates_for_job(job_id):
        if not logs_dir.exists() or not logs_dir.is_dir():
            continue

        final_path = logs_dir / "stage2_footage.json"
        if not final_path.exists():
            final_path = _latest_file_by_pattern(directory=logs_dir, pattern="stage2_footage_*.json") or final_path
            if not final_path.exists():
                final_path = None
        if final_path is not None:
            return final_path
    return None


def _extract_footage_file_names(payload: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(payload, dict):
        return []
    clips = payload.get("clips")
    if not isinstance(clips, list):
        return []

    out: List[str] = []
    seen: set[str] = set()
    for it in clips:
        if not isinstance(it, dict):
            continue
        name = str(it.get("file_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _load_used_footage_file_names_for_job(job_id: str) -> List[str]:
    fp = _pick_stage2_footage_file_for_job(job_id)
    if not isinstance(fp, Path):
        return []
    payload = _load_json_dict(fp)
    return _extract_footage_file_names(payload)


# Advance triggers for per-user rotation cursor.
# Any ONE of these firing after a completed SUCCEEDED job -> advance cursor by 1.
_ROTATION_ADVANCE_AVG_SCORE_MIN = 1.5
_ROTATION_ADVANCE_REPEAT_RATIO = 0.75


def _pick_rotation_diag_file_for_job(job_id: str) -> Optional[Path]:
    for logs_dir in _logs_dir_candidates_for_job(job_id):
        if not logs_dir.exists() or not logs_dir.is_dir():
            continue
        final_path = logs_dir / "stage2_footage_rotation_diag.json"
        if not final_path.exists():
            final_path = (
                _latest_file_by_pattern(
                    directory=logs_dir,
                    pattern="stage2_footage_rotation_diag_*.json",
                )
                or final_path
            )
            if not final_path.exists():
                final_path = None
        if final_path is not None:
            return final_path
    return None


def _load_rotation_diag_for_job(job_id: str) -> Dict[str, Any]:
    fp = _pick_rotation_diag_file_for_job(job_id)
    if not isinstance(fp, Path):
        return {}
    payload = _load_json_dict(fp)
    return payload if isinstance(payload, dict) else {}


def _should_advance_rotation(diag: Dict[str, Any]) -> Tuple[bool, str]:
    """Always advance rotation cursor by 1 after a SUCCEEDED job.

    Earlier policy advanced only on bad-run signals (low avg_score, high
    repeat_ratio, or exclude_relaxed). On clean pools that triggered nothing
    and the cursor stayed in place forever, so the same (theme, group) was
    used over and over -> same source files in the same intervals across
    different videos. That's exactly the symptom we're fixing.

    The diagnostics signals are now embedded in the reason code for
    observability only — they do not gate the advance.
    """
    reason_parts: List[str] = ["batch_completed"]
    if isinstance(diag, dict) and diag:
        try:
            avg = float(diag.get("primary_pool_avg_score") or 0.0)
        except Exception:
            avg = 0.0
        try:
            repeat_ratio = float(diag.get("primary_pool_repeat_ratio") or 0.0)
        except Exception:
            repeat_ratio = 0.0
        exclude_relaxed = bool(diag.get("exclude_relaxed"))
        if avg < _ROTATION_ADVANCE_AVG_SCORE_MIN:
            reason_parts.append(f"low_avg_score({avg:.2f})")
        if repeat_ratio >= _ROTATION_ADVANCE_REPEAT_RATIO:
            reason_parts.append(f"repeat_ratio({repeat_ratio:.2f})")
        if exclude_relaxed:
            reason_parts.append("exclude_relaxed")
    return True, "+".join(reason_parts)


def _describe_rotation_transition(
    *,
    artist_id: str,
    old_cursor: int,
    new_cursor: int,
) -> Optional[str]:
    """Build a short Russian user-facing message about the rotation move.

    Returns None if the rotation slots cannot be resolved for the artist.
    Three transition types:
      - same theme, next group within theme
      - new theme within the profile
      - wrap-around to first slot (full cycle completed)
    """
    slots = get_artist_rotation_slots(artist_id)
    if not slots:
        return None
    n = len(slots)
    old_slot = slots[int(old_cursor) % n]
    new_slot = slots[int(new_cursor) % n]
    old_theme, old_group = old_slot
    new_theme, new_group = new_slot
    wrapped = (int(new_cursor) // n) > (int(old_cursor) // n)
    if wrapped:
        return (
            "Прошёл полный круг тем для этого артиста — начинаю новый круг. "
            f"Следующий ролик: тема «{new_theme}», подгруппа «{new_group}»."
        )
    if old_theme == new_theme:
        return (
            f"Перехожу на следующую подгруппу внутри темы «{new_theme}»: "
            f"«{old_group}» → «{new_group}»."
        )
    return (
        f"Меняю тему для следующего ролика: «{old_theme}» → «{new_theme}» "
        f"(подгруппа «{new_group}»)."
    )


def _detect_subtitles_debug_mode(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    mode = str(payload.get("mode") or "").strip()
    if mode in {
        SUBTITLES_MODE_IMPULSE_2ND,
        SUBTITLES_MODE_SCENES_3RD,
        SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
        SUBTITLES_MODE_TEMPLATE_4TH,
    }:
        return mode

    scenes = payload.get("scenes")
    if isinstance(scenes, list) and scenes:
        return SUBTITLES_MODE_SCENES_3RD

    segs = payload.get("segments")
    if not isinstance(segs, list) or not segs:
        return None
    first = segs[0] if isinstance(segs[0], dict) else {}
    style = str(first.get("style_tag") or first.get("type") or "").strip()
    if style in _IMPULSE_STYLE_TAGS:
        return SUBTITLES_MODE_IMPULSE_2ND
    if style in _SCENES_STYLE_TAGS:
        return SUBTITLES_MODE_SCENES_3RD
    if style in _TEMPLATE_4TH_STYLE_TAGS:
        return SUBTITLES_MODE_TEMPLATE_4TH
    if payload.get("anchor_in_abs") is not None:
        return SUBTITLES_MODE_IMPULSE_2ND
    return None


def _normalize_impulse_rows(
    *,
    payload: Dict[str, Any],
    raw_payload: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    segs = payload.get("segments")
    if not isinstance(segs, list) or not segs:
        segs = raw_payload.get("segments") if isinstance(raw_payload, dict) else []
    raw_segments = raw_payload.get("segments") if isinstance(raw_payload, dict) else []
    if not isinstance(raw_segments, list):
        raw_segments = []

    out: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segs):
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        in_point = _to_float_or_none(seg.get("in"))
        if in_point is None:
            in_point = _to_float_or_none(seg.get("in_point"))
        out_point = _to_float_or_none(seg.get("out"))
        if out_point is None:
            out_point = _to_float_or_none(seg.get("out_point"))
        if in_point is None or out_point is None:
            continue

        style = str(seg.get("type") or seg.get("style_tag") or "").strip().lower()
        if style not in _IMPULSE_STYLE_TAGS:
            style = "long"

        reason = str(seg.get("reason") or "").strip()
        if not reason and idx < len(raw_segments) and isinstance(raw_segments[idx], dict):
            reason = str(raw_segments[idx].get("reason") or "").strip()

        out.append(
            {
                "idx": idx + 1,
                "style": style,
                "text": _compact_text(text, limit=220),
                "in_point": float(in_point),
                "out_point": float(out_point),
                "reason": _compact_text(reason, limit=180) if reason else "",
            }
        )
    out.sort(key=lambda x: (float(x["in_point"]), int(x["idx"])))
    return out


def _normalize_scene_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def _lines_to_text(lines_obj: Any) -> str:
        if not isinstance(lines_obj, list):
            return ""
        if lines_obj and isinstance(lines_obj[0], list):
            rows: List[str] = []
            for row in lines_obj:
                if not isinstance(row, list):
                    continue
                text = " ".join(str(w).strip() for w in row if str(w).strip())
                if text:
                    rows.append(text)
            return " / ".join(rows)
        rows2 = [str(x).strip() for x in lines_obj if str(x).strip()]
        return " / ".join(rows2)

    scenes = payload.get("scenes")
    if isinstance(scenes, list) and scenes:
        for idx, sc in enumerate(scenes, start=1):
            if not isinstance(sc, dict):
                continue
            in_point = _to_float_or_none(sc.get("start"))
            out_point = _to_float_or_none(sc.get("end"))
            if in_point is None or out_point is None:
                continue
            text = _lines_to_text(sc.get("lines"))
            if not text:
                words = sc.get("words")
                if isinstance(words, list):
                    text = " ".join(str(w).strip() for w in words if str(w).strip())
            if not text:
                text = str(sc.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "idx": int(sc.get("id") or idx),
                    "style": str(sc.get("type") or "").strip() or "TYPE_1",
                    "text": _compact_text(text, limit=220),
                    "in_point": float(in_point),
                    "out_point": float(out_point),
                    "focus_word": str(sc.get("focus_word") or "").strip(),
                    "focus_style": str(sc.get("focus_style") or "").strip(),
                }
            )
        out.sort(key=lambda x: (float(x["in_point"]), int(x["idx"])))
        return out

    segs = payload.get("segments")
    if not isinstance(segs, list):
        return out
    for idx, seg in enumerate(segs, start=1):
        if not isinstance(seg, dict):
            continue
        in_point = _to_float_or_none(seg.get("in_point"))
        out_point = _to_float_or_none(seg.get("out_point"))
        if in_point is None or out_point is None:
            continue
        text = _lines_to_text(seg.get("lines"))
        if not text:
            text = str(seg.get("text") or "").strip()
        if not text:
            continue
        seg_id = str(seg.get("segment_id") or seg.get("id") or "")
        seg_num = idx
        if seg_id:
            m = re.search(r"(\d+)$", seg_id)
            if m:
                try:
                    seg_num = int(m.group(1))
                except Exception:
                    seg_num = idx
        out.append(
            {
                "idx": seg_num,
                "style": str(seg.get("style_tag") or seg.get("type") or "").strip() or "TYPE_1",
                "text": _compact_text(text, limit=220),
                "in_point": float(in_point),
                "out_point": float(out_point),
                "focus_word": str(seg.get("focus_word") or "").strip(),
                "focus_style": str(seg.get("focus_style") or "").strip(),
            }
        )
    out.sort(key=lambda x: (float(x["in_point"]), int(x["idx"])))
    return out


def _resolve_clip_bounds(payload: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    clip = payload.get("clip") if isinstance(payload, dict) and isinstance(payload.get("clip"), dict) else {}
    clip_start = _to_float_or_none(clip.get("start")) if isinstance(clip, dict) else None
    clip_end = _to_float_or_none(clip.get("end")) if isinstance(clip, dict) else None
    if clip_start is not None and clip_end is not None:
        return clip_start, clip_end
    if not rows:
        return None, None
    starts = [float(r["in_point"]) for r in rows]
    ends = [float(r["out_point"]) for r in rows]
    return min(starts), max(ends)


def _build_impulse_debug_text(
    *,
    ver_label: str,
    payload: Dict[str, Any],
    raw_payload: Optional[Dict[str, Any]],
) -> str:
    rows = _normalize_impulse_rows(payload=payload, raw_payload=raw_payload)
    if not rows:
        return ""
    clip_start, clip_end = _resolve_clip_bounds(payload, rows)
    lines = [
        f"<b>{html.escape(ver_label)}</b>: <b>Разметка Impulse 2nd</b>",
    ]
    if clip_start is not None and clip_end is not None:
        lines.append(
            f"clip: <code>{_fmt_sec(clip_start)}..{_fmt_sec(clip_end)}</code> "
            f"dur=<code>{_fmt_sec(float(clip_end) - float(clip_start))}s</code>"
        )
    lines.append(f"segments: <code>{len(rows)}</code>")
    lines.append("Критерий: <b>SHORT</b> = акцент/рефрен, <b>LONG</b> = основная строка.")
    for row in rows:
        seg_dur = float(row["out_point"]) - float(row["in_point"])
        lines.append(
            f"{int(row['idx']):02d}. <b>{str(row['style']).upper()}</b> "
            f"<code>{_fmt_sec(row['in_point'])}..{_fmt_sec(row['out_point'])}</code> "
            f"(<code>{_fmt_sec(seg_dur)}s</code>) — {html.escape(str(row['text']))}"
        )
        reason = str(row.get("reason") or "").strip()
        if reason:
            lines.append(f"    reason: <code>{html.escape(reason)}</code>")
    return "\n".join(lines)


def _build_scenes_debug_text(*, ver_label: str, payload: Dict[str, Any]) -> str:
    rows = _normalize_scene_rows(payload)
    if not rows:
        return ""
    clip_start, clip_end = _resolve_clip_bounds(payload, rows)
    lines = [
        f"<b>{html.escape(ver_label)}</b>: <b>Разметка Scenes 3rd</b>",
    ]
    if clip_start is not None and clip_end is not None:
        lines.append(
            f"clip: <code>{_fmt_sec(clip_start)}..{_fmt_sec(clip_end)}</code> "
            f"dur=<code>{_fmt_sec(float(clip_end) - float(clip_start))}s</code>"
        )
    lines.append(f"scenes: <code>{len(rows)}</code>")
    lines.append("Критерий: TYPE_4 = red focus, TYPE_2 = italic focus, остальные TYPE_* = композиционные сцены.")
    for row in rows:
        seg_dur = float(row["out_point"]) - float(row["in_point"])
        line = (
            f"{int(row['idx']):02d}. <b>{html.escape(str(row['style']))}</b> "
            f"<code>{_fmt_sec(row['in_point'])}..{_fmt_sec(row['out_point'])}</code> "
            f"(<code>{_fmt_sec(seg_dur)}s</code>) — {html.escape(str(row['text']))}"
        )
        focus_word = str(row.get("focus_word") or "").strip()
        focus_style = str(row.get("focus_style") or "").strip()
        if focus_word:
            if focus_style:
                line += f" | focus=<code>{html.escape(focus_word)}:{html.escape(focus_style)}</code>"
            else:
                line += f" | focus=<code>{html.escape(focus_word)}</code>"
        lines.append(line)
    return "\n".join(lines)


def _build_subtitles_debug_text(
    *,
    ver_label: str,
    final_payload: Optional[Dict[str, Any]],
    raw_payload: Optional[Dict[str, Any]],
) -> str:
    mode = _detect_subtitles_debug_mode(final_payload) or _detect_subtitles_debug_mode(raw_payload)
    if mode == SUBTITLES_MODE_IMPULSE_2ND:
        payload = final_payload if isinstance(final_payload, dict) else (raw_payload or {})
        return _build_impulse_debug_text(ver_label=ver_label, payload=payload, raw_payload=raw_payload)
    if mode in {SUBTITLES_MODE_SCENES_3RD, SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP, SUBTITLES_MODE_TEMPLATE_4TH}:
        payload = final_payload if isinstance(final_payload, dict) else (raw_payload or {})
        return _build_scenes_debug_text(ver_label=ver_label, payload=payload)
    return ""


def _build_subtitles_debug_text_for_job(*, job_id: str, ver_label: str) -> str:
    final_path, raw_path = _pick_stage2_payload_files_for_job(job_id)
    final_payload = _load_json_dict(final_path) if isinstance(final_path, Path) else None
    raw_payload = _load_json_dict(raw_path) if isinstance(raw_path, Path) else None
    return _build_subtitles_debug_text(
        ver_label=ver_label,
        final_payload=final_payload,
        raw_payload=raw_payload,
    )


def _split_telegram_chunks(text: str, *, max_chars: int = 3600) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    out: List[str] = []
    buf: List[str] = []
    cur = 0
    for line in raw.splitlines():
        ln = line.rstrip()
        add = len(ln) + (1 if buf else 0)
        if cur + add > max_chars and buf:
            out.append("\n".join(buf))
            buf = [ln]
            cur = len(ln)
        else:
            buf.append(ln)
            cur += add
    if buf:
        out.append("\n".join(buf))
    return out


def _parse_versions_choice(text: str) -> Optional[int]:
    raw = str(text or "").strip()
    if raw in VERSION_BUTTONS:
        try:
            n = int(raw)
        except Exception:
            return None
        if 1 <= n <= 5:
            return n
    return None


def _parse_subtitles_mode_choice(text: str) -> Optional[str]:
    raw = str(text or "").strip()
    mode = _SUBTITLES_MODE_BY_BUTTON.get(raw)
    if not mode:
        return None
    return normalize_subtitles_mode(mode, default=SUBTITLES_MODE_LEGACY_BLOCKS)


def _normalize_username(raw: str) -> str:
    u = str(raw or "").strip().lower()
    if not u:
        return ""
    if not u.startswith("@"):
        u = "@" + u
    return u


def _is_username_allowed(*, username: str, allowlist: Tuple[str, ...]) -> bool:
    if not allowlist:
        return False
    return _normalize_username(username) in set(allowlist)


def _is_control_button_text(text: str) -> bool:
    return str(text or "").strip() in _CONTROL_BUTTONS


class BlastBotApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.telegram_api = make_telegram_api(settings.tg_bot_api_env)
        self.store = RedisChatStateStore(settings)
        self.s3 = S3Client(settings)
        self.orchestrator = OrchestratorClient(base_url=settings.orchestrator_public_url, timeout_s=60.0)
        self.users: UserStore | None = None

        # Credit / referral subsystems — initialized in _on_startup.
        self.users: UserStore | None = None
        self.referrals: ReferralStore | None = None

        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)

        self._processing_task: asyncio.Task[None] | None = None
        self._state_cleanup_task: asyncio.Task[None] | None = None
        self._fs_cleanup_task: asyncio.Task[None] | None = None
        self._bot: Bot | None = None

        self._register_handlers()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

    def _allow_archive_for_state(self, st: ChatState) -> bool:
        return _is_username_allowed(
            username=st.chat_username,
            allowlist=tuple(self.settings.artifacts_allowlist or tuple()),
        )

    def _version_num_for_job(self, st: ChatState, job_id: str) -> int:
        jid = str(job_id or "").strip()
        if not jid:
            return 0
        ids = list(st.job_order or [])
        if not ids:
            ids = list(st.active_job_ids or [])
        try:
            return ids.index(jid) + 1
        except Exception:
            return 0

    def _sync_state_user_from_message(self, st: ChatState, message: Message) -> bool:
        username = ""
        if message.from_user is not None:
            username = _normalize_username(getattr(message.from_user, "username", "") or "")
        if username and username != str(st.chat_username or ""):
            st.chat_username = username
            return True
        return False

    async def _ensure_user_profile(self, st: ChatState) -> None:
        """Keep user profile and username index up-to-date on every interaction."""
        if self.users is None:
            return
        try:
            await self.users.ensure_profile(int(st.chat_id), st.chat_username)
        except Exception as exc:
            log.warning("ensure_user_profile chat=%s err=%r", st.chat_id, exc)

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def _on_start(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            user_changed = self._sync_state_user_from_message(st, message)
            if user_changed:
                await self.store.set(st)
            await self._ensure_user_profile(st)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return
            await self._move_to_wait_audio(chat_id, message)

        @self.router.message()
        async def _on_any_message(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            user_changed = self._sync_state_user_from_message(st, message)
            if user_changed:
                await self.store.set(st)
            await self._ensure_user_profile(st)

            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            if st.stage == STAGE_LOCKED:
                await message.answer(
                    "Для генерации нужна оплата. Когда кредиты будут зачислены — напиши боту снова."
                )
                return

            if st.stage == STAGE_WAITING_REFERRAL:
                await message.answer(
                    "Ожидаю, пока твой друг активирует свой первый ролик. "
                    "Как только это произойдёт — ты получишь доступ автоматически."
                )
                return

            if st.stage in {STAGE_IDLE, ""}:
                await self._move_to_wait_audio(chat_id, message)
                return

            if st.stage == STAGE_WAIT_AUDIO:
                await self._handle_wait_audio(message, st)
                return

            if st.stage == STAGE_WAIT_LYRICS_CHOICE:
                await self._handle_wait_lyrics_choice(message, st)
                return

            if st.stage == STAGE_WAIT_LYRICS_TEXT:
                await self._handle_wait_lyrics_text(message, st)
                return

            if st.stage == STAGE_WAIT_FRAGMENT_CHOICE:
                await self._handle_wait_fragment_choice(message, st)
                return

            if st.stage == STAGE_WAIT_FRAGMENT_TEXT:
                await self._handle_wait_fragment_text(message, st)
                return

            if st.stage == STAGE_WAIT_BG_MODE:
                await self._handle_wait_bg_mode(message, st)
                return

            if st.stage == STAGE_WAIT_BG_COLOR:
                await self._handle_wait_bg_color(message, st)
                return

            if st.stage == STAGE_WAIT_FOOTAGE_GENRE:
                await self._handle_wait_footage_genre(message, st)
                return

            if st.stage == STAGE_WAIT_FOOTAGE_ARTIST:
                await self._handle_wait_footage_artist(message, st)
                return

            if st.stage == STAGE_WAIT_TIMING_CHOICE:
                await self._handle_wait_timing_choice(message, st)
                return

            if st.stage == STAGE_WAIT_TIMING_INPUT:
                await self._handle_wait_timing_input(message, st)
                return

            if st.stage == STAGE_WAIT_SUBTITLES_MODE:
                await self._handle_wait_subtitles_mode(message, st)
                return

            if st.stage == STAGE_WAIT_VERSIONS:
                await self._handle_wait_versions(message, st)
                return

            if st.stage == STAGE_WAIT_CONFIRM:
                await self._handle_wait_confirm(message, st)
                return

            if st.stage == STAGE_WAIT_NEXT:
                await self._handle_wait_next(message, st)
                return

            # Unknown stage -> reset deterministically.
            await self._move_to_wait_audio(chat_id, message)

    async def _on_startup(self, bot: Bot) -> None:
        self._bot = bot
        if not self.settings.tg_bot_token:
            raise RuntimeError("TG_BOT_TOKEN is empty")

        self.s3.validate_core()

        if not self.settings.s3_bucket_raw_audio:
            raise RuntimeError("S3_BUCKET_RAW_AUDIO is empty")

        self.settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.credits_db_url:
            self.users = UserStore(self.settings.credits_db_url)
            await self.users.init()
            log.info("startup: user_store active")
        elif self.settings.credits_required:
            raise RuntimeError("CREDITS_REQUIRED=true but CREDITS_DB_URL (or POSTGRES_*) is not set")

        # PostgreSQL — required when credits are enabled, optional otherwise.
        if self.settings.credits_db_url:
            self.users = UserStore(self.settings.credits_db_url)
            await self.users.init()
            self.referrals = ReferralStore(
                self.users,
                referral_bonus_credits=self.settings.referral_bonus_credits,
            )
            log.info("startup: PostgreSQL pool ready, user_store active")
        elif self.settings.credits_required:
            raise RuntimeError("CREDITS_REQUIRED=true but CREDITS_DB_URL (or POSTGRES_*) is not set")
        else:
            log.warning("startup: CREDITS_DB_URL not set — credit system disabled")

        self._processing_task = asyncio.create_task(self._processing_loop(), name="tg_bot_processing_loop")
        self._state_cleanup_task = asyncio.create_task(self._state_cleanup_loop(), name="tg_bot_state_cleanup_loop")
        self._fs_cleanup_task = asyncio.create_task(self._fs_cleanup_loop(), name="tg_bot_fs_cleanup_loop")
        log.info("startup complete: polling loop started")

    async def _on_shutdown(self, bot: Bot) -> None:
        del bot
        for task in [self._processing_task, self._state_cleanup_task, self._fs_cleanup_task]:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await self.orchestrator.close()
        await self.store.close()
        if self.users is not None:
            await self.users.close()
        self._bot = None
        log.info("shutdown complete")

    async def _move_to_wait_audio(self, chat_id: int, message: Message) -> None:
        await self.store.reset_to_wait_audio(chat_id)
        await message.answer(
            "Привет. Отправь трек аудио-файлом, и я соберу клип.",
            reply_markup=_kb([BTN_SEND_TRACK]),
        )
        await message.answer("Пришли аудио (audio/document).")

    async def _ask_versions(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_VERSIONS
        await self.store.set(st)
        await message.answer(
            "Сколько версий сгенерировать?",
            reply_markup=_kb([BTN_VER_1, BTN_VER_2, BTN_VER_3, BTN_VER_4, BTN_VER_5]),
        )

    @staticmethod
    def _parse_timing(text: str) -> tuple[float, float] | None:
        text = text.strip()
        parts = re.split(r"[\-\u2013\u2014]+|\s+", text, maxsplit=1)
        if len(parts) != 2:
            return None

        def _to_sec(raw: str) -> float | None:
            v = str(raw or "").strip()
            if not v:
                return None
            m = re.fullmatch(r"(\d{1,3}):(\d{1,2})", v)
            if m:
                return float(int(m.group(1))) * 60.0 + float(int(m.group(2)))
            try:
                out = float(v)
            except ValueError:
                return None
            return out if out >= 0.0 else None

        start_sec = _to_sec(parts[0])
        end_sec = _to_sec(parts[1])
        if start_sec is None or end_sec is None or end_sec <= start_sec:
            return None
        return start_sec, end_sec

    @staticmethod
    def _fmt_timing(sec: float) -> str:
        m = int(sec) // 60
        s = int(sec) % 60
        return f"{m}:{s:02d}"

    async def _ask_timing_choice(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_TIMING_CHOICE
        st.user_clip_start_sec = 0.0
        st.user_clip_end_sec = 0.0
        await self.store.set(st)
        await message.answer(
            "Хочешь указать конкретный тайминг трека для клипа?\n"
            "Например: 1:20-1:50 или 80-110 (в секундах).",
            reply_markup=_kb([BTN_SET_TIMING, BTN_SKIP_TIMING]),
        )

    async def _handle_wait_timing_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SET_TIMING:
            st.stage = STAGE_WAIT_TIMING_INPUT
            await self.store.set(st)
            await message.answer(
                "Отправь тайминг в формате: 1:20-1:50 или 80-110",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        if text == BTN_SKIP_TIMING:
            st.user_clip_start_sec = 0.0
            st.user_clip_end_sec = 0.0
            await self._ask_bg_mode(message, st)
            return
        await message.answer(
            "Выбери кнопку: «Указать тайминг» или «Весь трек / на усмотрение ИИ».",
        )

    async def _handle_wait_timing_input(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if not text:
            await message.answer("Отправь тайминг текстом, например: 1:20-1:50")
            return
        parsed = self._parse_timing(text)
        if parsed is None:
            await message.answer(
                "Не удалось распознать тайминг. Формат: 1:20-1:50 или 80-110 (начало-конец в секундах)."
            )
            return
        start_sec, end_sec = parsed
        duration = end_sec - start_sec
        if duration < 5.0:
            await message.answer("Слишком короткий фрагмент (минимум 5 сек). Попробуй ещё раз.")
            return
        if duration > 120.0:
            await message.answer("Слишком длинный фрагмент (максимум 120 сек). Попробуй ещё раз.")
            return
        st.user_clip_start_sec = round(start_sec, 3)
        st.user_clip_end_sec = round(end_sec, 3)
        await message.answer(
            f"Тайминг установлен: {self._fmt_timing(start_sec)} – {self._fmt_timing(end_sec)} ({duration:.0f} сек)."
        )
        await self._ask_bg_mode(message, st)

    async def _ask_bg_mode(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_BG_MODE
        st.bg_mode = "footage"
        st.bg_solid_color = ""
        await self.store.set(st)
        await message.answer(
            "Что будет на фоне?",
            reply_markup=_kb([BTN_BG_FOOTAGE], [BTN_BG_SOLID], [BTN_BACK]),
        )

    async def _handle_wait_bg_mode(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_timing_choice(message, st)
            return
        if text == BTN_BG_FOOTAGE:
            st.bg_mode = "footage"
            st.bg_solid_color = ""
            await self.store.set(st)
            await self._ask_footage_genre(message, st)
            return
        if text == BTN_BG_SOLID:
            st.bg_mode = "solid"
            await self.store.set(st)
            await self._ask_bg_color(message, st)
            return
        await message.answer(
            f"Выбери кнопкой: «{BTN_BG_FOOTAGE}» или «{BTN_BG_SOLID}».",
        )

    async def _ask_bg_color(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_BG_COLOR
        await self.store.set(st)
        await message.answer(
            "Выбери цвет фона:",
            reply_markup=_kb([BTN_BG_WHITE], [BTN_BG_BLACK], [BTN_BG_GREEN], [BTN_BACK]),
        )

    async def _handle_wait_bg_color(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_bg_mode(message, st)
            return
        color_by_btn = {BTN_BG_WHITE: "white", BTN_BG_BLACK: "black", BTN_BG_GREEN: "green"}
        if text not in color_by_btn:
            await message.answer(
                f"Выбери цвет кнопкой: «{BTN_BG_WHITE}», «{BTN_BG_BLACK}» или «{BTN_BG_GREEN}».",
            )
            return
        st.bg_solid_color = color_by_btn[text]
        # Solid bg still needs a footage_artist_id so Stage 2 footage planner
        # runs without errors — its picks are dropped at AE composition time.
        # Pick the first available artist as a deterministic placeholder.
        if not str(st.footage_artist_id or "").strip():
            try:
                first_genre = get_genres()[0]
                first_artist_key = str(first_genre["artists"][0]["key"])
                st.footage_genre_key = str(first_genre["key"])
                st.footage_artist_key = first_artist_key
                st.footage_artist_id = first_artist_key
            except Exception as exc:
                log.exception("solid_bg_default_artist_pick_failed: %s", exc)
                await message.answer("Внутренняя ошибка при выборе фона. Попробуй ещё раз позже.")
                return
        await self.store.set(st)
        await self._ask_subtitles_mode(message, st)

    async def _ask_footage_genre(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_FOOTAGE_GENRE
        st.footage_genre_key = ""
        st.footage_artist_key = ""
        st.footage_artist_id = ""
        await self.store.set(st)
        genres = get_genres()
        labels = [g["label"] for g in genres]
        await message.answer(
            "Выбери жанр исходников:",
            reply_markup=_kb(*[[label] for label in labels], [BTN_BACK]),
        )

    async def _handle_wait_footage_genre(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_bg_mode(message, st)
            return
        genres = get_genres()
        genre_by_label = {g["label"]: g for g in genres}
        if text not in genre_by_label:
            labels = ", ".join(f"«{g['label']}»" for g in genres)
            await message.answer(f"Выбери жанр кнопкой: {labels} или «{BTN_BACK}».")
            return
        genre = genre_by_label[text]
        st.footage_genre_key = genre["key"]
        st.stage = STAGE_WAIT_FOOTAGE_ARTIST
        await self.store.set(st)
        artists = list(genre["artists"])
        artist_labels = [a["label"] for a in artists]
        await message.answer(
            f"Жанр: {genre['label']}. Выбери стиль исходников:",
            reply_markup=_kb(*[[label] for label in artist_labels], [BTN_BACK]),
        )
        for artist in artists:
            preview_fid = str(artist.get("preview_file_id") or "").strip()
            preview_url = str(artist.get("preview_s3_url") or "").strip()
            description = str(artist.get("description") or "")
            if preview_fid:
                try:
                    await message.answer_video(video=preview_fid, caption=f"{artist['label']}: {description}")
                except Exception:
                    log.warning("failed to send preview for %s (file_id)", artist["key"])
            elif preview_url:
                try:
                    await message.answer_video(video=preview_url, caption=f"{artist['label']}: {description}")
                except Exception:
                    log.warning("failed to send preview for %s (url)", artist["key"])

    async def _handle_wait_footage_artist(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_footage_genre(message, st)
            return
        try:
            artists = get_artists(st.footage_genre_key)
        except KeyError:
            await self._ask_footage_genre(message, st)
            return
        artist_by_label = {a["label"]: a for a in artists}
        if text not in artist_by_label:
            labels = ", ".join(f"«{a['label']}»" for a in artists)
            await message.answer(f"Выбери стиль кнопкой: {labels} или «{BTN_BACK}».")
            return
        artist = artist_by_label[text]
        st.footage_artist_key = artist["key"]
        st.footage_artist_id = artist["key"]
        await self._ask_subtitles_mode(message, st)

    async def _ask_subtitles_mode(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_SUBTITLES_MODE
        if not str(st.subtitles_mode or "").strip():
            st.subtitles_mode = SUBTITLES_MODE_LEGACY_BLOCKS
        await self.store.set(st)
        await message.answer(
            "Выбери режим субтитров:",
            reply_markup=_kb(
                [BTN_SUB_MODE_LEGACY],
                [BTN_SUB_MODE_IMPULSE],
                [BTN_SUB_MODE_SCENES],
                [BTN_SUB_MODE_SCENES_SINGLE],
                [BTN_SUB_MODE_4TH],
            ),
        )

    async def _handle_wait_audio(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_TRACK:
            await message.answer("Жду аудио-файл.")
            return

        spec = _extract_audio_spec(message)
        if spec is None:
            await message.answer("Нужен аудио-файл. Нажми «Отправить трек» и пришли файл.")
            return

        if message.chat is None:
            return

        chat_id = int(message.chat.id)
        file_id, original_name = spec

        incoming_dir = self.settings.tmp_dir / str(chat_id) / "incoming"
        prepared_dir = self.settings.tmp_dir / str(chat_id) / "prepared"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        prepared_dir.mkdir(parents=True, exist_ok=True)

        src_name = f"{_now_tag()}_{uuid.uuid4().hex[:8]}_{_safe_name(original_name)}"
        src_path = incoming_dir / src_name

        try:
            await message.answer("Скачиваю файл и готовлю mp3…")
            await self._download_telegram_audio_with_retry(
                bot=message.bot,
                file_id=file_id,
                dest=src_path,
                chat_id=chat_id,
                original_name=original_name,
            )

            prep: AudioPrepareResult = await asyncio.to_thread(
                prepare_audio_best_effort,
                src=src_path,
                work_dir=prepared_dir,
                ffmpeg_bin=self.settings.ffmpeg_bin,
                max_audio_mb=self.settings.bot_max_audio_mb,
            )
        except TelegramBadRequest as e:
            log.exception(
                "audio_prepare_tg_bad_request chat=%s file_id=%s name=%s err=%s",
                chat_id,
                file_id,
                original_name,
                str(e),
            )
            if _is_tg_file_too_big_error(e):
                await message.answer(
                    "Не удалось подготовить аудио: Telegram не дает скачать этот файл (слишком большой).\n"
                    "Пришли, пожалуйста, более легкий файл: лучше mp3/m4a или обрезанный фрагмент."
                )
            else:
                await message.answer(f"Не удалось подготовить аудио (Telegram): {e}")
            return
        except Exception as e:
            log.exception(
                "audio_prepare_failed chat=%s file_id=%s name=%s err=%s",
                chat_id,
                file_id,
                original_name,
                str(e),
            )
            await message.answer(f"Не удалось подготовить аудио: {e}")
            return

        st.pending_audio_file_id = file_id
        st.pending_audio_filename = _safe_name(original_name)
        st.prepared_audio_local_path = str(prep.output_path)
        st.lyrics_text = ""
        st.target_fragment = ""
        st.subtitles_mode = SUBTITLES_MODE_LEGACY_BLOCKS
        st.versions_count = 1
        st.batch_id = ""
        st.batch_audio_s3_url = ""
        st.batch_total_versions = 1
        st.next_version_to_enqueue = 1
        st.master_job_id = ""
        st.job_order = []
        st.used_footage_file_names = []
        st.active_job_id = ""
        st.active_job_ids = []
        st.completed_job_ids = []
        st.stage = STAGE_WAIT_LYRICS_CHOICE
        await self.store.set(st)

        size_mb = prep.size_bytes / (1024 * 1024)
        limit_note = "<= лимита" if prep.under_limit else "> лимита (best-effort)"
        await message.answer(
            f"Трек готов: mp3 {prep.bitrate}, {size_mb:.2f}MB ({limit_note}).\n"
            "Хочешь прислать текст песни для субтитров?",
            reply_markup=_kb([BTN_SEND_LYRICS, BTN_SKIP_LYRICS]),
        )

    async def _handle_wait_lyrics_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_LYRICS:
            st.stage = STAGE_WAIT_LYRICS_TEXT
            await self.store.set(st)
            await message.answer(
                "Пришли текст песни обычным сообщением (не кнопкой).",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if text == BTN_SKIP_LYRICS:
            st.lyrics_text = ""
            st.target_fragment = ""
            await self._ask_subtitles_mode(message, st)
            return

        await message.answer("Выбери кнопку: «Отправить текст» или «Не присылать текст».")

    async def _handle_wait_lyrics_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if not text:
            await message.answer("Жду текст песни сообщением.")
            return
        if _is_control_button_text(text):
            await message.answer("Нужен именно текст песни сообщением. После этого перейду к следующему шагу.")
            return

        st.lyrics_text = text
        st.target_fragment = ""
        st.stage = STAGE_WAIT_FRAGMENT_CHOICE
        await self.store.set(st)
        await message.answer(
            "Текст получил. Хочешь указать интересующий фрагмент?",
            reply_markup=_kb([BTN_SEND_FRAGMENT, BTN_SKIP_FRAGMENT]),
        )

    async def _handle_wait_fragment_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_FRAGMENT:
            st.stage = STAGE_WAIT_FRAGMENT_TEXT
            await self.store.set(st)
            await message.answer(
                "Пришли интересующий фрагмент текста. "
                f"Рабочее окно всё равно будет {CLIP_WINDOW_RANGE_S_LABEL}, но модель постарается максимизировать overlap.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if text == BTN_SKIP_FRAGMENT:
            st.target_fragment = ""
            await self._ask_timing_choice(message, st)
            return

        await message.answer("Выбери кнопку: «Отправить интересующий фрагмент» или «На усмотрение ИИ».")

    async def _handle_wait_fragment_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if not text:
            await message.answer("Жду интересующий фрагмент обычным текстовым сообщением.")
            return
        if _is_control_button_text(text):
            await message.answer("Нужен именно текст фрагмента сообщением. После этого перейду к следующему шагу.")
            return

        st.target_fragment = text
        await self._ask_timing_choice(message, st)

    async def _handle_wait_subtitles_mode(self, message: Message, st: ChatState) -> None:
        mode = _parse_subtitles_mode_choice(message.text or "")
        if mode is None:
            await message.answer(
                "Выбери режим кнопкой: «Обычные blocks», «Impulse 2nd», "
                "«Scenes 3rd», «Scenes 3rd Single-Step» или «Template 4th»."
            )
            return
        st.subtitles_mode = mode
        await self._ask_versions(message, st)

    async def _handle_wait_versions(self, message: Message, st: ChatState) -> None:
        n = _parse_versions_choice(message.text or "")
        if n is None:
            await message.answer("Выбери количество версий: 1, 2, 3, 4 или 5.")
            return
        st.versions_count = int(n)
        st.stage = STAGE_WAIT_CONFIRM
        await self.store.set(st)
        await message.answer(
            f"Ок, режим субтитров: {st.subtitles_mode}, версий: {n}. Запустить генерацию?",
            reply_markup=_kb([BTN_LAUNCH]),
        )

    async def _handle_wait_confirm(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text != BTN_LAUNCH:
            await message.answer("Нажми «Запустить», когда будешь готов.")
            return

        if message.chat is None:
            return

        chat_id = int(message.chat.id)
        prepared_path = Path(st.prepared_audio_local_path).expanduser().resolve()
        if not prepared_path.exists():
            await message.answer("Подготовленный mp3 не найден. Пришли трек заново.")
            await self._move_to_wait_audio(chat_id, message)
            return

        key = self._build_raw_audio_key(chat_id=chat_id, file_name=prepared_path.name)
        versions = max(1, min(5, int(st.versions_count or 1)))
        batch_id = f"tg-{chat_id}-{uuid.uuid4().hex[:12]}"
        deduction_ref = f"batch:{batch_id}"

        if self.settings.credits_required and self.users is not None:
            await self.users.ensure_profile(chat_id, st.chat_username)
            deduct_ok, _ = await self.users.deduct_credit(
                chat_id,
                ref_id=deduction_ref,
                amount=self.settings.credits_per_generation,
                note=f"generation batch={batch_id}",
            )
            if not deduct_ok:
                profile = await self.users.get_profile(chat_id)
                current = profile.credits if profile else 0
                await message.answer(
                    f"Недостаточно кредитов для генерации (нужно {self.settings.credits_per_generation}, "
                    f"у тебя {current}). Пополни баланс и попробуй снова."
                )
                return
            st.pending_deduction_ref_id = deduction_ref
            await self.store.set(st)
        try:
            await message.answer(f"Заливаю аудио в S3 и ставлю задачи в очередь… (версий: {versions})")
            audio_s3_url = await asyncio.to_thread(
                self.s3.upload_file,
                path=prepared_path,
                bucket=self.settings.s3_bucket_raw_audio,
                key=key,
                content_type="audio/mpeg",
            )
            job_order: List[str] = []
            next_version_to_enqueue = 2
            enqueue_failed_from_version: int | None = None
            enqueue_failed_error: str = ""

            if self.settings.bot_enqueue_all_versions_async:
                next_version_to_enqueue = int(versions) + 1
                for version_index in range(1, int(versions) + 1):
                    try:
                        job_id = await self._enqueue_batch_version(
                            st=st,
                            audio_s3_url=audio_s3_url,
                            version_index=version_index,
                            versions_total=versions,
                            batch_id=batch_id,
                            # For parallel batch enqueue we intentionally do not
                            # depend on stage1 artifacts from a master job.
                            reuse_text_job_id="",
                            exclude_file_names=[],
                        )
                    except Exception as e:
                        if version_index == 1:
                            raise RuntimeError(f"Не удалось поставить в очередь Версию 1/{versions}: {e}") from e
                        enqueue_failed_from_version = version_index
                        enqueue_failed_error = str(e)
                        break
                    job_order.append(job_id)
            else:
                master_job_id = await self._enqueue_batch_version(
                    st=st,
                    audio_s3_url=audio_s3_url,
                    version_index=1,
                    versions_total=versions,
                    batch_id=batch_id,
                    reuse_text_job_id="",
                    exclude_file_names=[],
                )
                job_order = [master_job_id]

            if not job_order:
                raise RuntimeError("Не удалось поставить в очередь ни одной версии.")
            master_job_id = job_order[0]

            st.pending_deduction_ref_id = ""
            st.stage = STAGE_PROCESSING
            st.batch_id = batch_id
            st.batch_audio_s3_url = audio_s3_url
            st.batch_total_versions = int(versions)
            st.next_version_to_enqueue = int(next_version_to_enqueue)
            st.master_job_id = master_job_id
            st.job_order = list(job_order)
            st.used_footage_file_names = []
            st.active_job_id = master_job_id
            st.active_job_ids = list(job_order)
            st.completed_job_ids = []
            st.active_job_started_at = time.time()
            st.last_status_msg_at = 0.0
            st.status_message_id = 0
            st.last_status_text = ""
            st.poll_attempts = 0
            st.last_job_stage = ""
            st.last_job_error = ""
            st.last_result_url = ""

            initial_rows = []
            for idx, jid in enumerate(job_order, start=1):
                initial_rows.append(
                    {"job_id": jid, "status": "QUEUED", "stage": "build", "error": "", "version": idx}
                )
            initial_text = self._jobs_progress_message(
                rows=initial_rows,
                poll_attempts=0,
                total_versions=versions,
                queue_estimate=await self._queue_estimate_for_rows(initial_rows),
            )
            sent = await message.answer(initial_text)
            st.status_message_id = int(getattr(sent, "message_id", 0) or 0)
            st.last_status_text = initial_text
            st.last_status_msg_at = time.time()
            await self.store.set(st)

            if enqueue_failed_from_version is not None:
                await message.answer(
                    "Часть версий не поставилась в очередь: "
                    f"начиная с v{enqueue_failed_from_version}/{versions}. "
                    f"Ошибка: {_compact_text(enqueue_failed_error, limit=180)}"
                )
        except Exception as e:
            if self.settings.credits_required and self.users is not None and st.pending_deduction_ref_id:
                await self.users.refund_credit(
                    chat_id,
                    ref_id=deduction_ref,
                    amount=self.settings.credits_per_generation,
                    note=f"refund: enqueue failed batch={batch_id}",
                )
                st.pending_deduction_ref_id = ""
                await self.store.set(st)
            await message.answer(f"Не удалось запустить задачу: {e}")

    async def _handle_wait_next(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text != BTN_NEXT:
            await message.answer("Если хочешь новый ролик, нажми «Сделать следующий».", reply_markup=_kb([BTN_NEXT]))
            return

        if message.chat is None:
            return

        await self._move_to_wait_audio(int(message.chat.id), message)

    def _build_raw_audio_key(self, *, chat_id: int, file_name: str) -> str:
        safe = _safe_name(file_name)
        return f"{self.settings.s3_raw_audio_prefix.strip('/')}/{chat_id}/{_now_tag()}_{uuid.uuid4().hex[:10]}_{safe}"

    async def _resolve_rotation_slot_for_enqueue(
        self, *, st: ChatState
    ) -> Tuple[str, str, List[str]]:
        """Return (theme, group, persistent_history_names) for the current user.

        Returns empty ("", "", []) when artist_id has no rotation slots
        (unknown artist or no themes) — callers should then skip override.
        """
        artist_id = str(st.footage_artist_id or "").strip()
        if not artist_id:
            return "", "", []
        slots = get_artist_rotation_slots(artist_id)
        if not slots:
            return "", "", []
        cursor = await self.store.get_rotation_cursor(int(st.chat_id), artist_id)
        slot = slots[int(cursor) % len(slots)]
        history = await self.store.get_rotation_history(int(st.chat_id), artist_id)
        return slot[0], slot[1], history

    async def _enqueue_batch_version(
        self,
        *,
        st: ChatState,
        audio_s3_url: str,
        version_index: int,
        versions_total: int,
        batch_id: str,
        reuse_text_job_id: str = "",
        exclude_file_names: Optional[List[str]] = None,
    ) -> str:
        idem = f"tg-{st.chat_id}-batch-{batch_id}-v{int(version_index)}"
        user_clip_start_sec: float | None = None
        user_clip_end_sec: float | None = None
        start = float(st.user_clip_start_sec or 0.0)
        end = float(st.user_clip_end_sec or 0.0)
        if end > start >= 0.0:
            user_clip_start_sec = start
            user_clip_end_sec = end
        rotation_theme, rotation_group, rotation_history = (
            await self._resolve_rotation_slot_for_enqueue(st=st)
        )
        merged_exclude_seen: set[str] = set()
        merged_exclude: List[str] = []
        for name in list(exclude_file_names or []) + list(rotation_history or []):
            clean = str(name or "").strip()
            if not clean or clean in merged_exclude_seen:
                continue
            merged_exclude_seen.add(clean)
            merged_exclude.append(clean)
        enqueue = await self.orchestrator.send_audio_s3(
            audio_s3_url=audio_s3_url,
            mode="with_gemini",
            lyrics_text=st.lyrics_text,
            target_fragment=st.target_fragment,
            subtitles_mode=st.subtitles_mode,
            footage_artist_id=st.footage_artist_id,
            user_clip_start_sec=user_clip_start_sec,
            user_clip_end_sec=user_clip_end_sec,
            idempotency_key=idem,
            project_id=batch_id or None,
            reuse_text_job_id=str(reuse_text_job_id or "") or None,
            exclude_file_names=merged_exclude,
            variant_index=int(version_index),
            variants_total=int(versions_total),
            rotation_theme=rotation_theme,
            rotation_tags_group=rotation_group,
            bg_mode=str(st.bg_mode or "footage"),
            bg_solid_color=str(st.bg_solid_color or ""),
        )
        job_id = str(enqueue.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError(f"enqueue response has no job_id: {enqueue}")
        return job_id

    def _progress_interval_s(self) -> float:
        return max(1.0, float(self.settings.bot_status_update_interval_s))

    async def _queue_estimate_for_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        job_id = pick_queue_estimate_job_id(rows)
        if not job_id:
            return {}
        getter = getattr(self.orchestrator, "get_queue_estimate", None)
        if not callable(getter):
            return {}
        try:
            out = await getter(job_id)
        except Exception as e:
            log.warning("queue_estimate_fetch_failed job=%s err=%s", job_id, str(e))
            return {}
        return out if isinstance(out, dict) else {}

    def _jobs_progress_message(
        self,
        *,
        rows: List[Dict[str, Any]],
        poll_attempts: int,
        total_versions: int,
        queue_estimate: Dict[str, Any] | None = None,
    ) -> str:
        total = max(1, int(total_versions))
        succ = 0
        fail = 0
        active = 0
        for r in rows:
            status = str(r.get("status") or "").upper()
            if status == "SUCCEEDED":
                succ += 1
            elif status == "FAILED":
                fail += 1
            else:
                active += 1

        done = succ + fail
        pending = max(0, total - done - active)
        lines = [
            "Прогресс задач:",
            f"versions={done}/{total} ok={succ} fail={fail} active={active} pending={pending}",
            f"poll_attempts={max(0, int(poll_attempts))}",
        ]
        queue_lines = format_queue_estimate_lines(queue_estimate)
        if queue_lines:
            lines.extend(queue_lines)
        for i, r in enumerate(rows, start=1):
            ver = int(r.get("version") or i)
            status = str(r.get("status") or "UNKNOWN").upper()
            stage = str(r.get("stage") or "-")
            err = str(r.get("error") or "")
            line = f"v{ver}: {status} / {stage}"
            if status == "FAILED" and err:
                line += f" / err={_compact_text(err, limit=120)}"
            lines.append(line)

        return "\n".join(lines)

    async def _upsert_status_message(self, *, bot: Bot, st: ChatState, text: str) -> None:
        new_text = str(text or "").strip()
        if not new_text:
            return

        if new_text == str(st.last_status_text or "") and int(st.status_message_id or 0) > 0:
            return

        msg_id = int(st.status_message_id or 0)
        if msg_id > 0:
            try:
                await bot.edit_message_text(
                    chat_id=st.chat_id,
                    message_id=msg_id,
                    text=new_text,
                )
                st.last_status_text = new_text
                return
            except Exception as e:
                em = str(e).lower()
                if "message is not modified" in em:
                    st.last_status_text = new_text
                    return
                log.warning(
                    "status_message_edit_failed chat=%s msg_id=%s err=%s",
                    st.chat_id,
                    msg_id,
                    str(e),
                )

        try:
            sent = await bot.send_message(st.chat_id, new_text)
            st.status_message_id = int(getattr(sent, "message_id", 0) or 0)
            st.last_status_text = new_text
        except Exception as e:
            log.warning("status_message_send_failed chat=%s err=%s", st.chat_id, str(e))

    def _reset_processing_state(self, st: ChatState) -> None:
        st.stage = STAGE_WAIT_NEXT
        st.active_job_id = ""
        st.active_job_ids = []
        st.completed_job_ids = []
        st.job_order = []
        st.batch_id = ""
        st.batch_audio_s3_url = ""
        st.batch_total_versions = 1
        st.next_version_to_enqueue = 1
        st.master_job_id = ""
        st.used_footage_file_names = []
        st.active_job_started_at = 0.0
        st.last_status_msg_at = 0.0
        st.status_message_id = 0
        st.last_status_text = ""
        st.poll_attempts = 0
        st.last_job_stage = ""
        st.last_job_error = ""
        st.target_fragment = ""
        st.footage_genre_key = ""
        st.footage_artist_key = ""
        st.footage_artist_id = ""
        st.bg_mode = "footage"
        st.bg_solid_color = ""
        st.user_clip_start_sec = 0.0
        st.user_clip_end_sec = 0.0
        st.subtitles_mode = SUBTITLES_MODE_LEGACY_BLOCKS
        st.pending_deduction_ref_id = ""

    async def _send_long_html_message(self, *, bot: Bot, chat_id: int, text: str) -> None:
        chunks = _split_telegram_chunks(text)
        for part in chunks:
            if not part:
                continue
            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)

    def _state_cleanup_interval_s(self) -> float:
        return max(60.0, float(self.settings.tg_state_cleanup_interval_s))

    def _state_ttl_s(self) -> float:
        return max(3600.0, float(self.settings.tg_state_ttl_h) * 3600.0)

    def _fs_cleanup_interval_s(self) -> float:
        return max(60.0, float(self.settings.bot_fs_cleanup_interval_s))

    def _tmp_retention_by_subdir_s(self) -> Dict[str, float]:
        return {
            "incoming": max(300.0, float(self.settings.bot_tmp_incoming_retention_h) * 3600.0),
            "prepared": max(300.0, float(self.settings.bot_tmp_prepared_retention_h) * 3600.0),
            "result": max(300.0, float(self.settings.bot_tmp_result_retention_h) * 3600.0),
        }

    def _output_artifact_retention_s(self) -> float:
        return max(300.0, float(self.settings.bot_output_artifact_retention_h) * 3600.0)

    def _output_debug_artifact_retention_s(self) -> float:
        return max(300.0, float(self.settings.bot_output_debug_artifact_retention_h) * 3600.0)

    async def _state_cleanup_loop(self) -> None:
        while True:
            try:
                now = time.time()
                cutoff = now - self._state_ttl_s()
                stale_ids = await self.store.list_stale_chat_ids(
                    cutoff,
                    limit=max(1, int(self.settings.tg_state_cleanup_batch_size)),
                )
                removed_states = 0
                for chat_id in stale_ids:
                    await self.store.delete_state(chat_id)
                    removed_states += 1

                removed_indexes = await self.store.cleanup_index_members(
                    limit=max(1, int(self.settings.tg_state_index_cleanup_batch_size))
                )
                if removed_states or removed_indexes:
                    log.info(
                        "state_cleanup summary removed_states=%s removed_orphan_indexes=%s",
                        removed_states,
                        removed_indexes,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("state_cleanup_loop_iteration_error err=%r", e)
            await asyncio.sleep(self._state_cleanup_interval_s())

    async def _fs_cleanup_loop(self) -> None:
        while True:
            try:
                now = time.time()
                batch_size = max(1, int(self.settings.bot_fs_cleanup_batch_size))
                tmp_stats = await asyncio.to_thread(
                    cleanup_tmp_chat_dirs,
                    tmp_root=self.settings.tmp_dir,
                    retention_by_subdir_s=self._tmp_retention_by_subdir_s(),
                    now_ts=now,
                    max_scan_files=batch_size,
                    max_scan_dirs=batch_size,
                )
                jobs_stats = await asyncio.to_thread(
                    cleanup_jobs_artifacts,
                    jobs_roots=_jobs_output_roots(),
                    regular_retention_s=self._output_artifact_retention_s(),
                    debug_retention_s=self._output_debug_artifact_retention_s(),
                    debug_allowlist_patterns=tuple(self.settings.bot_output_artifact_allowlist or tuple()),
                    now_ts=now,
                    max_scan_files=batch_size,
                    max_scan_dirs=batch_size,
                )
                removed_files = int(tmp_stats.get("removed_files", 0)) + int(jobs_stats.get("removed_files", 0))
                removed_dirs = int(tmp_stats.get("removed_dirs", 0)) + int(jobs_stats.get("removed_dirs", 0))
                if removed_files or removed_dirs:
                    log.info(
                        "fs_cleanup summary tmp=%s jobs=%s",
                        tmp_stats,
                        jobs_stats,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("fs_cleanup_loop_iteration_error err=%r", e)
            await asyncio.sleep(self._fs_cleanup_interval_s())

    async def _processing_loop(self) -> None:
        _recovery_check_counter = 0
        while True:
            try:
                states = await self.store.list_processing()
                for st in states:
                    try:
                        await self._process_chat_job(st)
                    except Exception as e:
                        log.warning("processing loop chat=%s err=%r", st.chat_id, e)

                # Recovery check: every ~60 iterations (~5 min at 5s interval),
                # look for chats stuck in PROCESSING for >2 hours.
                _recovery_check_counter += 1
                if _recovery_check_counter >= 60:
                    _recovery_check_counter = 0
                    await self._recover_stuck_processing()

                # Recovery for chats stuck in WAITING_REFERRAL.
                waiting_states = await self.store.list_waiting_referral()
                for st in waiting_states:
                    try:
                        await self._recover_waiting_referral(st)
                    except Exception as e:
                        log.warning("referral_recovery chat=%s err=%r", st.chat_id, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("processing loop iteration error=%r", e)

            await asyncio.sleep(max(1.0, float(self.settings.bot_poll_interval_s)))

    async def _recover_stuck_processing(self) -> None:
        """
        Recovery policy: if a chat has been in PROCESSING for >2 hours
        with no progress, reset it so the user isn't stuck forever.
        """
        try:
            stuck = await self.store.list_processing_stuck(max_age_s=7200.0)
            if not stuck:
                return
            bot = self._require_bot()
            for st in stuck:
                log.warning(
                    "stuck_processing_recovery chat=%s batch=%s age_s=%.0f jobs=%s",
                    st.chat_id, st.batch_id,
                    time.time() - (st.active_job_started_at or st.updated_at or 0.0),
                    st.active_job_ids,
                )
                try:
                    await bot.send_message(
                        st.chat_id,
                        "Задачи зависли (> 2 часов без прогресса). Сброс состояния. Попробуй ещё раз.",
                        reply_markup=_kb([BTN_NEXT]),
                    )
                except Exception as e:
                    log.warning("stuck_recovery_msg_failed chat=%s err=%r", st.chat_id, e)
                self._reset_processing_state(st)
                await self.store.set(st)
        except Exception as e:
            log.warning("stuck_processing_recovery_error err=%r", e)

    async def _maybe_recover_stuck_processing(self, st: ChatState) -> bool:
        """Reset a chat stuck in PROCESSING beyond the timeout. Returns True if recovered."""
        timeout_s = float(self.settings.bot_job_timeout_h) * 3600.0
        started_at = float(st.active_job_started_at or 0.0)
        if started_at <= 0.0 or (time.time() - started_at) < timeout_s:
            return False
        log.warning(
            "stuck_processing_recovery chat=%s batch=%s started_at=%.0f timeout_h=%.1f — resetting",
            st.chat_id, st.batch_id, started_at, self.settings.bot_job_timeout_h,
        )
        bot = self._require_bot()
        try:
            await bot.send_message(
                st.chat_id,
                "Генерация зависла слишком долго и была сброшена автоматически. "
                "Попробуй отправить трек заново.",
                reply_markup=_kb([BTN_NEXT]),
            )
        except Exception as e:
            log.warning("stuck_recovery_notify_failed chat=%s err=%r", st.chat_id, e)
        self._reset_processing_state(st)
        await self.store.set(st)
        return True

    async def _recover_waiting_referral(self, st: ChatState) -> None:
        """Reset a chat stuck in WAITING_REFERRAL beyond the timeout."""
        timeout_s = float(self.settings.bot_referral_timeout_h) * 3600.0
        since = float(st.waiting_referral_since or 0.0)
        if since <= 0.0:
            return
        if (time.time() - since) < timeout_s:
            return
        log.warning(
            "stuck_waiting_referral_recovery chat=%s since=%.0f timeout_h=%.1f — resetting",
            st.chat_id, since, self.settings.bot_referral_timeout_h,
        )
        bot = self._require_bot()
        try:
            await bot.send_message(
                st.chat_id,
                "Ожидание активации реферала истекло. Чтобы начать, отправь трек.",
                reply_markup=_kb([BTN_SEND_TRACK]),
            )
        except Exception as e:
            log.warning("referral_timeout_notify_failed chat=%s err=%r", st.chat_id, e)
        await self.store.reset_to_wait_audio(st.chat_id)

    async def _maybe_grant_referral_bonus_after_generation(self, st: ChatState) -> None:
        """
        Best-effort referral bonus grant for the referee's first successful generation.
        This method must never break the processing loop.
        """
        if self.referrals is None:
            return
        referee_chat_id = int(st.chat_id)
        try:
            inviter_chat_id = await self.referrals.maybe_grant_referral_bonus(referee_chat_id)
        except Exception as e:
            log.warning(
                "referral_bonus_grant_failed referee=%s err=%r",
                referee_chat_id,
                e,
            )
            return

        if not inviter_chat_id:
            return

        try:
            bot = self._require_bot()
            await bot.send_message(
                int(inviter_chat_id),
                f"Твой реферал @{(st.chat_username or '').lstrip('@') or referee_chat_id} "
                f"сделал первый ролик. Бонус +{self.settings.referral_bonus_credits} кредит.",
            )
        except Exception as e:
            log.warning(
                "referral_bonus_notify_failed inviter=%s referee=%s err=%r",
                inviter_chat_id,
                referee_chat_id,
                e,
            )

    def _current_job_ids(self, st: ChatState) -> List[str]:
        raw = list(st.active_job_ids or [])
        if not raw and st.active_job_id:
            raw = [str(st.active_job_id)]
        out: List[str] = []
        seen: set[str] = set()
        for it in raw:
            jid = str(it or "").strip()
            if not jid or jid in seen:
                continue
            seen.add(jid)
            out.append(jid)
        return out

    async def _finalize_one_job(self, *, bot: Bot, st: ChatState, job_id: str, job: Dict[str, Any]) -> None:
        total = max(1, int(st.batch_total_versions or len(st.job_order or st.active_job_ids or []) or 1))
        ver = self._version_num_for_job(st, job_id)
        ver_label = f"Версия {ver}/{total}" if ver > 0 else f"job_id={job_id}"

        status = str(job.get("status") or "").upper()
        stage = str(job.get("stage") or "").strip()
        error_text = str(job.get("error") or "").strip()

        if status == "FAILED":
            retries = _extract_celery_retries(error_text)
            fail_lines = [
                f"{ver_label}: задача завершилась с ошибкой.",
                f"Стадия: {stage or '-'}",
            ]
            if retries is not None:
                fail_lines.append(f"Celery retries: {retries}")
            if error_text:
                fail_lines.append(f"Последняя ошибка: {_compact_text(error_text, limit=1000)}")
            else:
                fail_lines.append("Последняя ошибка: без деталей.")
            await bot.send_message(st.chat_id, "\n".join(fail_lines))
            return

        source = _resolve_job_video_source(job, self.settings)
        if not source:
            await bot.send_message(
                st.chat_id,
                f"{ver_label}: готово, но не нашёл ссылку на видео в ответе оркестратора.",
            )
            return

        st.last_result_url = source

        video_path = self.settings.tmp_dir / str(st.chat_id) / "result" / f"{job_id}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        send_video_path = video_path

        file_sent = False
        send_file_error = ""
        try:
            await self._download_result_video(source=source, dest=video_path)
            send_video_path = await self._prepare_result_video_for_tg(
                source_path=video_path,
                chat_id=st.chat_id,
                job_id=job_id,
            )
            await self._send_result_video_with_retry(
                bot=bot,
                chat_id=st.chat_id,
                job_id=job_id,
                video_path=send_video_path,
                caption=f"{ver_label}: вот твой трек.",
            )
            file_sent = True
        except Exception as e:
            send_file_error = str(e)
            log.warning("send file failed chat=%s job=%s err=%s", st.chat_id, job_id, send_file_error)

        if not file_sent:
            fallback_link = await self._build_fallback_link(source)
            msg = f"{ver_label}: не смог отправить файл видео."
            if fallback_link:
                msg += f"\nСсылка: {fallback_link}"
            if send_file_error:
                msg += f"\nОшибка: {send_file_error}"
            await bot.send_message(st.chat_id, msg)

        if self.settings.tg_send_project_archive and self._allow_archive_for_state(st):
            archive_source = _resolve_job_project_archive_source(job)
            if archive_source:
                archive_link = await self._build_fallback_link(archive_source)
                if not archive_link:
                    archive_link = archive_source
                await bot.send_message(
                    st.chat_id,
                    f"{ver_label}: проект (AEP + ресурсы): {archive_link}",
                )
            else:
                await bot.send_message(
                    st.chat_id,
                    f"{ver_label}: видео готово, но ссылка на архив проекта в ответе рендера не найдена.",
                )

        if self._allow_archive_for_state(st):
            try:
                dbg_text = _build_subtitles_debug_text_for_job(job_id=job_id, ver_label=ver_label)
                if dbg_text:
                    await self._send_long_html_message(bot=bot, chat_id=st.chat_id, text=dbg_text)
            except Exception as e:
                log.warning("subtitles_debug_send_failed chat=%s job=%s err=%s", st.chat_id, job_id, str(e))

        try:
            for p in {video_path, send_video_path}:
                if p.exists():
                    p.unlink()
        except Exception:
            pass

    async def _process_chat_job(self, st: ChatState) -> None:
        # Timeout guard: recover chats that have been stuck in PROCESSING too long.
        if await self._maybe_recover_stuck_processing(st):
            return

        job_ids = self._current_job_ids(st)
        if not job_ids:
            self._reset_processing_state(st)
            await self.store.set(st)
            return
        st.active_job_ids = list(job_ids)
        st.active_job_id = job_ids[0]
        if not st.job_order:
            st.job_order = list(job_ids)
        total_versions = max(1, int(st.batch_total_versions or st.versions_count or len(st.job_order) or len(job_ids)))
        st.batch_total_versions = total_versions

        bot = self._require_bot()
        completed: set[str] = {str(x) for x in (st.completed_job_ids or []) if str(x)}
        rows: List[Dict[str, Any]] = []
        new_finals: List[Tuple[str, Dict[str, Any]]] = []

        st.poll_attempts = max(0, int(st.poll_attempts)) + 1
        for jid in job_ids:
            job = await self.orchestrator.get_job(jid)
            status = str(job.get("status") or "").upper()
            stage = str(job.get("stage") or "").strip()
            error_text = str(job.get("error") or "").strip()
            rows.append(
                {
                    "job_id": jid,
                    "status": status,
                    "stage": stage,
                    "error": error_text,
                    "version": self._version_num_for_job(st, jid),
                }
            )
            if stage:
                st.last_job_stage = stage
            if error_text:
                st.last_job_error = error_text
            if status in {"SUCCEEDED", "FAILED"} and jid not in completed:
                new_finals.append((jid, job))

        status_text = self._jobs_progress_message(
            rows=rows,
            poll_attempts=st.poll_attempts,
            total_versions=total_versions,
            queue_estimate=await self._queue_estimate_for_rows(rows),
        )
        now = time.time()
        should_send = (
            st.poll_attempts == 1
            or status_text != str(st.last_status_text or "")
            or (now - float(st.last_status_msg_at or 0.0)) >= self._progress_interval_s()
        )
        if should_send:
            await self._upsert_status_message(bot=bot, st=st, text=status_text)
            st.last_status_msg_at = now

        for jid, job in new_finals:
            await self._finalize_one_job(bot=bot, st=st, job_id=jid, job=job)
            completed.add(jid)
            if str(job.get("status") or "").upper() == "SUCCEEDED":
                used_now = _load_used_footage_file_names_for_job(jid)
                if used_now:
                    seen_used = set(st.used_footage_file_names or [])
                    added_count = 0
                    for nm in used_now:
                        if nm in seen_used:
                            continue
                        seen_used.add(nm)
                        st.used_footage_file_names.append(nm)
                        added_count += 1
                    log.info(
                        "batch_used_footage_update chat=%s job=%s added=%d total=%d",
                        st.chat_id,
                        jid,
                        added_count,
                        len(st.used_footage_file_names or []),
                    )

                # Persistent cross-session footage history (keyed by artist_id).
                artist_id_for_rotation = str(st.footage_artist_id or "").strip()
                if artist_id_for_rotation and used_now:
                    try:
                        await self.store.add_rotation_history(
                            int(st.chat_id), artist_id_for_rotation, used_now
                        )
                    except Exception as e:
                        log.warning(
                            "rotation_history_persist_failed chat=%s job=%s err=%s",
                            st.chat_id, jid, str(e),
                        )

                # Advance-trigger evaluation: inspect rotation diagnostics and
                # bump cursor + notify user on any bad-run signal.
                if artist_id_for_rotation:
                    diag = _load_rotation_diag_for_job(jid)
                    should_advance, reason = _should_advance_rotation(diag)
                    if should_advance:
                        try:
                            old_cursor, new_cursor = await self.store.advance_rotation_cursor(
                                int(st.chat_id), artist_id_for_rotation
                            )
                            log.info(
                                "rotation_cursor_advance chat=%s artist=%s old=%d new=%d reason=%s",
                                st.chat_id, artist_id_for_rotation,
                                old_cursor, new_cursor, reason,
                            )
                            msg = _describe_rotation_transition(
                                artist_id=artist_id_for_rotation,
                                old_cursor=old_cursor,
                                new_cursor=new_cursor,
                            )
                            if msg:
                                try:
                                    await bot.send_message(st.chat_id, msg)
                                except Exception as send_e:
                                    log.warning(
                                        "rotation_notify_failed chat=%s err=%s",
                                        st.chat_id, str(send_e),
                                    )
                        except Exception as e:
                            log.warning(
                                "rotation_cursor_advance_failed chat=%s artist=%s err=%s",
                                st.chat_id, artist_id_for_rotation, str(e),
                            )

        st.completed_job_ids = [jid for jid in job_ids if jid in completed]
        all_done_enqueued = len(st.completed_job_ids) >= len(job_ids)
        if not all_done_enqueued:
            await self.store.set(st)
            return

        # Compute batch outcome counts
        succeeded_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "SUCCEEDED")
        failed_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "FAILED")

        master_status = ""
        if st.master_job_id:
            for r in rows:
                if str(r.get("job_id") or "") == str(st.master_job_id):
                    master_status = str(r.get("status") or "").upper()
                    break

        next_ver = max(1, int(st.next_version_to_enqueue or 1))
        can_enqueue_more = next_ver <= total_versions
        enqueue_failed = False
        if can_enqueue_more:
            if master_status == "FAILED":
                # master_failed: don't enqueue remaining versions
                await bot.send_message(
                    st.chat_id,
                    f"Версия 1/{total_versions}: завершилась ошибкой (master_failed) — остальные версии не запускаю.",
                )
                st.next_version_to_enqueue = total_versions + 1
            else:
                try:
                    new_job_id = await self._enqueue_batch_version(
                        st=st,
                        audio_s3_url=str(st.batch_audio_s3_url or ""),
                        version_index=next_ver,
                        versions_total=total_versions,
                        batch_id=str(st.batch_id or f"tg-{st.chat_id}"),
                        reuse_text_job_id=str(st.master_job_id or ""),
                        exclude_file_names=list(st.used_footage_file_names or []),
                    )
                    if new_job_id not in st.active_job_ids:
                        st.active_job_ids.append(new_job_id)
                    if new_job_id not in st.job_order:
                        st.job_order.append(new_job_id)
                    st.active_job_id = new_job_id
                    st.next_version_to_enqueue = next_ver + 1
                    await bot.send_message(
                        st.chat_id,
                        f"Версия {next_ver}/{total_versions}: поставил в очередь (exclude={len(st.used_footage_file_names or [])}).",
                    )
                    await self.store.set(st)
                    return
                except Exception as e:
                    enqueue_failed = True
                    await bot.send_message(
                        st.chat_id,
                        f"Не удалось поставить в очередь Версию {next_ver}/{total_versions}: {e}",
                    )
                    st.next_version_to_enqueue = total_versions + 1

        succeeded_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "SUCCEEDED")
        failed_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "FAILED")
        enqueued_count = len(st.job_order or job_ids)
        enqueue_short = enqueued_count < total_versions or enqueue_failed

        if total_versions == 1:
            if succeeded_count == 1:
                batch_outcome = "all_succeeded"
            elif master_status == "FAILED":
                batch_outcome = "master_failed"
            else:
                batch_outcome = "all_failed"
        elif succeeded_count == total_versions:
            batch_outcome = "all_succeeded"
        elif succeeded_count > 0 and not enqueue_short:
            batch_outcome = "partial_failed"
        elif succeeded_count > 0 and enqueue_short:
            batch_outcome = "enqueue_failed"
        elif master_status == "FAILED":
            batch_outcome = "master_failed"
        elif enqueue_short:
            batch_outcome = "enqueue_failed"
        else:
            batch_outcome = "all_failed"

        if batch_outcome == "all_succeeded":
            summary = f"Готово: все {succeeded_count}/{total_versions} версий успешно."
        elif batch_outcome == "partial_failed":
            summary = (
                f"Частично готово: {succeeded_count}/{total_versions} версий успешно, "
                f"{failed_count} завершились ошибкой."
            )
        elif batch_outcome == "enqueue_failed":
            summary = (
                f"Частично: {succeeded_count} из {total_versions} версий запущено "
                f"({succeeded_count} успешно, {failed_count} с ошибкой). "
                f"Не удалось поставить в очередь версии {enqueued_count + 1}–{total_versions}."
            )
        elif batch_outcome == "master_failed":
            summary = "Не удалось: первая версия (master) завершилась ошибкой."
        else:
            summary = f"Все {total_versions} версий завершились ошибкой."

        await bot.send_message(
            st.chat_id,
            summary + "\nСделать следующий?",
            reply_markup=_kb([BTN_NEXT]),
        )

        # Grant referral bonus to inviter if this was the user's first successful generation.
        if succeeded_count > 0:
            await self._maybe_grant_referral_bonus_after_generation(st)

        self._reset_processing_state(st)
        st.prepared_audio_local_path = ""
        st.pending_audio_file_id = ""
        st.pending_audio_filename = ""
        st.lyrics_text = ""
        st.target_fragment = ""
        st.versions_count = 1
        await self.store.set(st)

    async def _download_result_video(self, *, source: str, dest: Path) -> None:
        src = str(source or "").strip()
        if not src:
            raise RuntimeError("empty output source")

        if src.startswith("s3://"):
            await asyncio.to_thread(self.s3.download_s3_url, s3_url=src, dest=dest)
            return

        if src.startswith("http://") or src.startswith("https://"):
            await self._download_http(url=src, dest=dest)
            return

        raise RuntimeError(f"unsupported output source: {src!r}")

    def _tg_video_max_bytes(self) -> int:
        mb = int(getattr(self.settings, "bot_max_video_mb", 49) or 49)
        if mb < 1:
            mb = 1
        return int(mb) * 1024 * 1024

    async def _prepare_result_video_for_tg(self, *, source_path: Path, chat_id: int, job_id: str) -> Path:
        max_bytes = self._tg_video_max_bytes()
        try:
            size_bytes = int(source_path.stat().st_size)
        except Exception as e:
            raise RuntimeError(f"video file is not readable before send: {source_path}") from e

        if size_bytes <= max_bytes:
            return source_path

        compress_enabled = bool(getattr(self.settings, "tg_video_compress_enabled", True))
        if not compress_enabled:
            raise RuntimeError(
                f"video is too large for telegram ({size_bytes} bytes > {max_bytes} bytes) and compression is disabled"
            )

        compressed = source_path.with_name(f"{source_path.stem}.tg.mp4")
        await self._compress_video_to_fit_tg(
            source_path=source_path,
            output_path=compressed,
            max_bytes=max_bytes,
        )
        out_size = int(compressed.stat().st_size) if compressed.exists() else 0
        log.info(
            "video_compressed_for_tg chat=%s job=%s source_mb=%.2f result_mb=%.2f max_mb=%.2f",
            chat_id,
            job_id,
            float(size_bytes) / (1024.0 * 1024.0),
            float(out_size) / (1024.0 * 1024.0),
            float(max_bytes) / (1024.0 * 1024.0),
        )
        return compressed

    async def _compress_video_to_fit_tg(self, *, source_path: Path, output_path: Path, max_bytes: int) -> None:
        if output_path.exists():
            output_path.unlink()

        for crf in _TG_VIDEO_COMPRESS_CRF_STEPS:
            await self._run_ffmpeg_video_compress(source_path=source_path, output_path=output_path, crf=crf)
            size_bytes = int(output_path.stat().st_size) if output_path.exists() else 0
            if size_bytes > 0 and size_bytes <= max_bytes:
                return

        final_size = int(output_path.stat().st_size) if output_path.exists() else 0
        raise RuntimeError(
            f"video compression did not reach telegram limit: size={final_size} max={max_bytes} path={output_path}"
        )

    async def _run_ffmpeg_video_compress(self, *, source_path: Path, output_path: Path, crf: int) -> None:
        cmd = [
            self.settings.ffmpeg_bin,
            "-y",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(int(crf)),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_tail = (stderr.decode("utf-8", errors="replace")[-1200:] if stderr else "").strip()
            raise RuntimeError(
                f"ffmpeg video compress failed rc={proc.returncode} crf={crf} stderr_tail={err_tail}"
            )

    async def _send_result_video_with_retry(
        self,
        *,
        bot: Bot,
        chat_id: int,
        job_id: str,
        video_path: Path,
        caption: str,
    ) -> None:
        retries = max(1, int(getattr(self.settings, "tg_video_send_retries", 2) or 2))
        timeout_s = max(1.0, float(getattr(self.settings, "tg_video_send_timeout_s", 120.0) or 120.0))
        backoff_s = max(0.0, float(getattr(self.settings, "tg_video_send_backoff_base_s", 2.0) or 2.0))
        request_timeout = int(timeout_s)
        last_err: Exception | None = None

        for attempt in range(1, retries + 1):
            try:
                await bot.send_document(
                    chat_id=chat_id,
                    document=FSInputFile(str(video_path)),
                    caption=caption,
                    request_timeout=request_timeout,
                )
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt >= retries:
                    break
                delay_s = backoff_s * float(attempt)
                log.warning(
                    "video_send_retry chat=%s job=%s attempt=%d/%d timeout_s=%.1f delay_s=%.1f err=%r",
                    chat_id,
                    job_id,
                    attempt,
                    retries,
                    timeout_s,
                    delay_s,
                    e,
                )
                if delay_s > 0:
                    await asyncio.sleep(delay_s)

        assert last_err is not None
        raise RuntimeError(
            f"telegram video send failed after {retries} attempts: {type(last_err).__name__}: {last_err!r}"
        ) from last_err

    async def _download_telegram_audio_with_retry(
        self,
        *,
        bot: Bot,
        file_id: str,
        dest: Path,
        chat_id: int,
        original_name: str,
    ) -> None:
        retries = max(1, int(_TG_AUDIO_DOWNLOAD_RETRIES))
        last_err: Exception | None = None
        tg_proxy = str(self.settings.tg_file_proxy_url or "").strip()

        for attempt in range(1, retries + 1):
            try:
                tg_file = await bot.get_file(file_id)
                if tg_proxy:
                    await self._download_telegram_file_via_http(
                        file_path=str(tg_file.file_path or ""),
                        dest=dest,
                        proxy_url=tg_proxy,
                    )
                else:
                    with open(dest, "wb") as f:
                        await bot.download_file(
                            tg_file.file_path,
                            destination=f,
                            timeout=float(_TG_AUDIO_DOWNLOAD_TIMEOUT_S),
                        )
                size = int(dest.stat().st_size) if dest.exists() else 0
                if size <= 0:
                    raise RuntimeError("telegram download produced empty file")
                return
            except TelegramBadRequest:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                try:
                    if dest.exists():
                        dest.unlink()
                except Exception:
                    pass

                if attempt >= retries:
                    break

                delay_s = float(_TG_AUDIO_DOWNLOAD_BACKOFF_BASE_S) * float(attempt)
                log.warning(
                    "audio_download_retry chat=%s file_id=%s name=%s via_proxy=%s attempt=%d/%d delay_s=%.1f err=%r",
                    chat_id,
                    file_id,
                    original_name,
                    bool(tg_proxy),
                    attempt,
                    retries,
                    delay_s,
                    e,
                )
                await asyncio.sleep(delay_s)

        assert last_err is not None
        raise RuntimeError(
            f"telegram download failed after {retries} attempts: {type(last_err).__name__}: {last_err!r}"
        ) from last_err

    async def _download_telegram_file_via_http(
        self,
        *,
        file_path: str,
        dest: Path,
        proxy_url: str,
    ) -> None:
        path = str(file_path or "").strip().lstrip("/")
        if not path:
            raise RuntimeError("telegram file_path is empty")

        url = self.telegram_api.file_url(token=self.settings.tg_bot_token, path=path)

        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(float(_TG_AUDIO_DOWNLOAD_TIMEOUT_S))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=str(proxy_url)) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 300:
                    raise RuntimeError(
                        f"telegram file download failed status={resp.status_code} path={path!r}"
                    )
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

    async def _download_http(self, *, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(600.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 300:
                    raise RuntimeError(f"http download failed status={resp.status_code} url={url}")
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

    async def _build_fallback_link(self, source: str) -> str:
        src = str(source or "").strip()
        if not src:
            return ""
        if src.startswith("http://") or src.startswith("https://"):
            return src
        if src.startswith("s3://"):
            try:
                return await asyncio.to_thread(self.s3.generate_presigned_for_s3_url, s3_url=src, expires_s=None)
            except Exception:
                return src
        return src

    def _require_bot(self) -> Bot:
        if self._bot is None:
            raise RuntimeError("bot instance is not ready")
        return self._bot

    async def run(self) -> None:
        tg_proxy = str(self.settings.tg_file_proxy_url or "").strip()
        if tg_proxy:
            bot = Bot(
                token=self.settings.tg_bot_token,
                session=build_aiogram_session(api_env=self.settings.tg_bot_api_env, proxy_url=tg_proxy),
            )
            log.info("bot_api_proxy_enabled proxy=%s", _mask_proxy_url(tg_proxy))
        else:
            bot = Bot(
                token=self.settings.tg_bot_token,
                session=build_aiogram_session(api_env=self.settings.tg_bot_api_env),
            )
        await self.dp.start_polling(bot)


def main() -> None:
    app = BlastBotApp(SETTINGS)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
