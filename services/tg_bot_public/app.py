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
from urllib.parse import parse_qsl, quote, unquote_plus

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from core.clip_window import CLIP_WINDOW_RANGE_S_LABEL
from core.filesystem_hygiene import cleanup_jobs_artifacts, cleanup_tmp_chat_dirs
from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    normalize_subtitles_mode,
)
from config.styles.artist_presets_loader import get_artists, get_genres

from .admin_commands import make_admin_router
from .admin_panel import start_admin_panel
from .audio_prepare import AudioPrepareResult, prepare_audio_best_effort
from .config import SETTINGS, Settings
from .credits_db import CreditsDB
from .tbank_client import TBankClient
from .orchestrator_client import OrchestratorClient
from .s3_client import S3Client, make_s3_url
from .state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_IDLE,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_CONFIRM_MODE,
    STAGE_WAIT_CONFIRM_TEXT,
    STAGE_WAIT_FRAGMENT_CHOICE,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_FOOTAGE_ARTIST,
    STAGE_WAIT_FOOTAGE_GENRE,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
    STAGE_WAIT_NEXT,
    STAGE_WAIT_START,
    STAGE_WAIT_SUBSCRIPTION,
    STAGE_WAIT_TIMING_CHOICE,
    STAGE_WAIT_TIMING_INPUT,
    STAGE_WAIT_SUBTITLES_MODE,
    STAGE_WAIT_VERSIONS,
    # Post-generation stages
    STAGE_RATE_VIDEO,
    STAGE_FEEDBACK_LOW,
    STAGE_SALES_PITCH,
    STAGE_PACKAGES_OFFER,
    STAGE_PACKAGE_DETAILS,
    STAGE_ALL_PACKAGES,
    STAGE_PACKAGE_INFO,
    STAGE_WHY_NOT,
    STAGE_NOT_ACTUAL_REASON,
    STAGE_CASES_TECH,
    STAGE_TRY_FULL,
    STAGE_REFERRAL_ASK,
    STAGE_WAIT_REFERRAL_TAG,
    STAGE_WAITING_REFERRAL,
    STAGE_RATE_VIDEO_2,
    STAGE_FEEDBACK_LOW_2,
    STAGE_LAST_STEP_FORM,
    STAGE_POST_SURVEY,
    STAGE_KEEP_IN_TOUCH,
    STAGE_REMIND_RELEASE,
    STAGE_NO_FRIENDS_FORM,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_bot: %(message)s",
)
log = logging.getLogger("tg_bot")


BTN_LETS_GO = "Едем!"
BTN_SUBSCRIBED = "Подписался!"
BTN_SEND_TRACK = "Отправить трек"
BTN_GENERATE_MORE = "Сгенерировать ещё"
BTN_SEND_LYRICS = "Отправить текст"
BTN_SKIP_LYRICS = "Пусть ИИ угадает"
BTN_SEND_FRAGMENT = "Указать строки из текста"
BTN_SKIP_FRAGMENT = "На усмотрение ИИ"
BTN_SET_TIMING = "Указать тайминг"
BTN_SKIP_TIMING = "На усмотрение ИИ"
BTN_CONFIRM_YES = "Да"
BTN_CONFIRM_BACK = "Вернуться назад"
BTN_BACK = "Назад"
BTN_LAUNCH = "Запустить"
BTN_RESTART = "Начать заново"
BTN_NEXT = "Сделать следующий"
BTN_SUB_MODE_IMPULSE = "Impulse"
BTN_SUB_MODE_SCENES = "Jakson"
BTN_SUB_MODE_4TH = "Tape"

# Post-generation flow buttons
BTN_RATE_LOW = "До 5"
BTN_RATE_MID_LOW = "5-6"
BTN_RATE_MID_HIGH = "7-8"
BTN_RATE_HIGH = "9-10"
BTN_RATE_BUTTONS = [BTN_RATE_LOW, BTN_RATE_MID_LOW, BTN_RATE_MID_HIGH, BTN_RATE_HIGH]

BTN_LETS_DO_IT = "Делаем!"
BTN_HOW_SO = "Как же?"

BTN_TELL_MORE = "Рассказывайте!"
BTN_ALL_PACKAGES = "Все пакеты"
BTN_NOT_NOW = "Пока неактуально"

BTN_READY = "Готов!"
BTN_MAYBE_LATER = "Чуть позже"

BTN_PKG_TRIAL = "Триал"
BTN_PKG_BLAST = "Бласт"
BTN_PKG_GLOW = "Глоу"
BTN_PKG_IMPULSE = "Импульс"
BTN_PKG_BUTTONS = [BTN_PKG_TRIAL, BTN_PKG_BLAST, BTN_PKG_GLOW, BTN_PKG_IMPULSE]

BTN_TO_TARIFFS = "К тарифам"
BTN_PURCHASE = "Приобрести"

BTN_NO_RELEASE = "Нет актуального релиза"
BTN_NO_MONEY = "Пока не хватает финансов"
BTN_BAD_QUALITY = "Качество роликов"
BTN_DOUBT_EFFECT = "Сомневаюсь в эффективности"
BTN_WHY_NOT_BUTTONS = [BTN_NO_RELEASE, BTN_NO_MONEY, BTN_BAD_QUALITY, BTN_DOUBT_EFFECT]

BTN_AGREED = "Договорились!"
BTN_ABOUT_CASES = "Про кейсы и технологию"
BTN_GOT_IT = "Принял!"

BTN_SEND_NOW = "Сейчас пришлю"
BTN_NEED_SEARCH = "Надо поискать"
BTN_NO_FRIENDS = "Нет друзей-артистов"

BTN_TO_FORM = "В форму"
BTN_OF_COURSE = "Конечно!"
BTN_PLANNING = "Планирую!"
BTN_NOT_YET = "Пока нет"
BTN_SURVEY_DONE = "Прошёл"

SUBTITLES_MODE_BUTTONS = [
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_SUB_MODE_4TH,
]
_SUBTITLES_MODE_BY_BUTTON = {
    BTN_SUB_MODE_IMPULSE: SUBTITLES_MODE_IMPULSE_2ND,
    BTN_SUB_MODE_SCENES: SUBTITLES_MODE_SCENES_3RD,
    BTN_SUB_MODE_4TH: SUBTITLES_MODE_TEMPLATE_4TH,
}
_BUTTON_BY_SUBTITLES_MODE = {v: k for k, v in _SUBTITLES_MODE_BY_BUTTON.items()}
_SUBTITLES_EXAMPLE_VIDEO = {
    BTN_SUB_MODE_IMPULSE: "BAACAgIAAx0CdKlg9AADQ2mnL7EFHHNkgsKZQHLNHLQhU35jAALTkQAC8jc5SbjhU7JLg4UAAToE",
    BTN_SUB_MODE_SCENES: "BAACAgIAAx0CdKlg9AADQmmnL7EawTRkBbRPX0OaE1oBsy_4AALRkQAC8jc5Sbt4v4pv5zj1OgQ",
    BTN_SUB_MODE_4TH: "BAACAgIAAx0CdKlg9AADRGmnL7G_IkoJjuSqOIe_t9BKgaFRAALbkQAC8jc5Scql7dM2k-q6OgQ",
}
_CONTROL_BUTTONS = {
    BTN_LETS_GO,
    BTN_SUBSCRIBED,
    BTN_SEND_TRACK,
    BTN_SEND_LYRICS,
    BTN_SKIP_LYRICS,
    BTN_SEND_FRAGMENT,
    BTN_SKIP_FRAGMENT,
    BTN_SET_TIMING,
    BTN_SKIP_TIMING,
    BTN_CONFIRM_YES,
    BTN_CONFIRM_BACK,
    BTN_BACK,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_SUB_MODE_4TH,
    BTN_LAUNCH,
    BTN_NEXT,
    BTN_RESTART,
    # Post-generation
    BTN_RATE_LOW, BTN_RATE_MID_LOW, BTN_RATE_MID_HIGH, BTN_RATE_HIGH,
    BTN_LETS_DO_IT, BTN_HOW_SO,
    BTN_TELL_MORE, BTN_ALL_PACKAGES, BTN_NOT_NOW,
    BTN_READY, BTN_MAYBE_LATER,
    BTN_PKG_TRIAL, BTN_PKG_BLAST, BTN_PKG_GLOW, BTN_PKG_IMPULSE,
    BTN_TO_TARIFFS, BTN_PURCHASE,
    BTN_NO_RELEASE, BTN_NO_MONEY, BTN_BAD_QUALITY, BTN_DOUBT_EFFECT,
    BTN_AGREED, BTN_ABOUT_CASES, BTN_GOT_IT,
    BTN_SEND_NOW, BTN_NEED_SEARCH, BTN_NO_FRIENDS,
    BTN_TO_FORM, BTN_OF_COURSE, BTN_PLANNING, BTN_NOT_YET, BTN_SURVEY_DONE,
}


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
_UTM_FIELDS = ("source", "medium", "campaign", "content", "term")
_RE_CELERY_RETRIES = re.compile(r"\bretries=(\d+)\b")
_TG_AUDIO_DOWNLOAD_RETRIES = 3
_TG_AUDIO_DOWNLOAD_TIMEOUT_S = 180.0
_TG_AUDIO_DOWNLOAD_BACKOFF_BASE_S = 2.0
_GENERATION_FAILED_USER_TEXT = (
    "Увидели ошибку, сейчас с тобой свяжется менеджер и запустит генерацию ролика вручную, "
    "а пока тех. отдел все проверит"
)


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


def _parse_subtitles_mode_choice(text: str) -> Optional[str]:
    raw = str(text or "").strip()
    mode = _SUBTITLES_MODE_BY_BUTTON.get(raw)
    if not mode:
        return None
    return normalize_subtitles_mode(mode, default=SUBTITLES_MODE_IMPULSE_2ND)


def _parse_versions_choice(text: str) -> Optional[int]:
    raw = str(text or "").strip()
    if raw in ("1", "2", "3", "4", "5"):
        return int(raw)
    return None


def _extract_start_payload(message: Message) -> str:
    text = str(message.text or "").strip()
    if not text.startswith("/start"):
        return ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return str(parts[1] or "").strip()


def _normalize_utm_value(raw: str, *, max_len: int = 160) -> str:
    compact = " ".join(str(raw or "").split())
    return compact[:max_len]


def _parse_utm_payload(raw_payload: str) -> Dict[str, str]:
    raw = str(raw_payload or "").strip()
    if not raw:
        return {}

    decoded = unquote_plus(raw)
    out: Dict[str, str] = {k: "" for k in _UTM_FIELDS}

    query_like = decoded.replace(";", "&")
    if "=" in query_like:
        for key, value in parse_qsl(query_like, keep_blank_values=True):
            k = str(key or "").strip().lower()
            if k.startswith("utm_"):
                k = k[4:]
            if k in out:
                out[k] = _normalize_utm_value(value)
    else:
        for part in decoded.split("|"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            k = str(key or "").strip().lower()
            if k.startswith("utm_"):
                k = k[4:]
            if k in out and not out[k]:
                out[k] = _normalize_utm_value(value)

    out["payload"] = decoded[:512]
    if any(out[k] for k in _UTM_FIELDS):
        return out
    if out["payload"]:
        return {"payload": out["payload"]}
    return {}


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
        self.store = RedisChatStateStore(settings)
        self.s3 = S3Client(settings)
        self.orchestrator = OrchestratorClient(base_url=settings.orchestrator_public_url, timeout_s=60.0)
        if not settings.credits_db_url:
            raise RuntimeError("CREDITS_DB_URL (or POSTGRES_*) is required for tg_bot_public")
        self.credits_db = CreditsDB(settings.credits_db_url)
        self.tbank = TBankClient(
            terminal_key=settings.tbank_terminal_key,
            password=settings.tbank_password,
            notify_url=settings.tbank_notify_url,
        ) if settings.tbank_terminal_key else None
        self._bot_ref: list = [None]  # mutable ref for admin panel webhook

        self.dp = Dispatcher()
        self.router = Router()
        admin_router = make_admin_router(self.credits_db, settings)
        self.dp.include_router(admin_router)
        self.dp.include_router(self.router)

        self._processing_task: asyncio.Task[None] | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._state_cleanup_task: asyncio.Task[None] | None = None
        self._fs_cleanup_task: asyncio.Task[None] | None = None
        self._bot: Bot | None = None
        self._preview_source_bot_token = str(settings.tg_preview_source_bot_token or "").strip()
        self._preview_source_file_url_cache: Dict[str, str] = {}

        self._register_handlers()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

    def _allow_archive_for_state(self, st: ChatState) -> bool:
        return _is_username_allowed(
            username=st.chat_username,
            allowlist=tuple(self.settings.artifacts_allowlist or tuple()),
        )

    async def _resolve_preview_source_file_url(self, preview_file_id: str) -> str:
        fid = str(preview_file_id or "").strip()
        token = str(self._preview_source_bot_token or "").strip()
        if not fid or not token:
            return ""
        cached = str(self._preview_source_file_url_cache.get(fid) or "").strip()
        if cached:
            return cached

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                api_url = f"https://api.telegram.org/bot{token}/getFile"
                resp = await client.get(api_url, params={"file_id": fid})
                if resp.status_code >= 300:
                    return ""
                payload = resp.json()
                if not bool(payload.get("ok")):
                    return ""
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                file_path = str(result.get("file_path") or "").strip()
                if not file_path:
                    return ""
                file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
                self._preview_source_file_url_cache[fid] = file_url
                return file_url
        except Exception:
            return ""

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
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            start_payload = _extract_start_payload(message)
            if start_payload:
                utm = _parse_utm_payload(start_payload)
                await self.credits_db.record_utm_touch(chat_id, raw_start_arg=start_payload, utm=utm)
                utm_parts = [
                    f"{k}={utm.get(k)}"
                    for k in ("source", "medium", "campaign")
                    if str(utm.get(k) or "").strip()
                ]
                detail = " ".join(utm_parts) if utm_parts else _compact_text(start_payload, limit=180)
                await self.credits_db.log_event(chat_id, "utm_touch", detail)

            # Check referral: notify referrer if this user was referred
            raw_username = (st.chat_username or "").lower().lstrip("@")
            if raw_username:
                ref_tag = f"@{raw_username}"
                referrer_id = await self.store.get_referral(ref_tag)
                if referrer_id:
                    referrer_st = await self.store.get(referrer_id)
                    if referrer_st.stage == STAGE_WAITING_REFERRAL:
                        await self._activate_referral_reward(referrer_st=referrer_st, referral_tag=ref_tag)

            # Ensure user exists in credits DB (credits granted after subscription)
            username = (st.chat_username or "").lstrip("@")
            await self.credits_db.ensure_user(chat_id, username)

            # Extract deep link start parameter for source tracking
            # Format: /start <param> — param is the UTM source identifier
            raw_text = str(message.text or "")
            parts = raw_text.split(maxsplit=1)
            start_param = parts[1].strip() if len(parts) > 1 else ""
            if start_param and not start_param.startswith("@"):
                await self.credits_db.set_user_source(chat_id, start_param)

            await self.credits_db.log_event(chat_id, "start", f"@{username}" if username else "")
            await self._move_to_onboarding(chat_id, message)

        @self.router.message(Command("packets"))
        async def _on_packets(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.\nПакеты можно посмотреть после.")
                return
            await self._show_all_packages(message, st)

        @self.router.message(Command("sendtrack"))
        async def _on_sendtrack(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
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

            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            if st.stage == STAGE_WAIT_START:
                await self._handle_wait_start(message, st)
                return

            if st.stage == STAGE_WAIT_SUBSCRIPTION:
                await self._handle_wait_subscription(message, st)
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

            if st.stage == STAGE_WAIT_TIMING_CHOICE:
                await self._handle_wait_timing_choice(message, st)
                return

            if st.stage == STAGE_WAIT_TIMING_INPUT:
                await self._handle_wait_timing_input(message, st)
                return

            if st.stage == STAGE_WAIT_FOOTAGE_GENRE:
                await self._handle_wait_footage_genre(message, st)
                return

            if st.stage == STAGE_WAIT_FOOTAGE_ARTIST:
                await self._handle_wait_footage_artist(message, st)
                return

            if st.stage == STAGE_WAIT_CONFIRM_TEXT:
                await self._handle_wait_confirm_text(message, st)
                return

            if st.stage == STAGE_WAIT_SUBTITLES_MODE:
                await self._handle_wait_subtitles_mode(message, st)
                return

            if st.stage == STAGE_WAIT_CONFIRM_MODE:
                await self._handle_wait_confirm_mode(message, st)
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

            # --- Post-generation flow stages ---
            _PG_DISPATCH = {
                STAGE_RATE_VIDEO: self._handle_rate_video,
                STAGE_FEEDBACK_LOW: self._handle_feedback_low,
                STAGE_SALES_PITCH: self._handle_sales_pitch,
                STAGE_PACKAGES_OFFER: self._handle_packages_offer,
                STAGE_PACKAGE_DETAILS: self._handle_package_details,
                STAGE_ALL_PACKAGES: self._handle_all_packages,
                STAGE_PACKAGE_INFO: self._handle_package_info,
                STAGE_WHY_NOT: self._handle_why_not,
                STAGE_NOT_ACTUAL_REASON: self._handle_not_actual_reason,
                STAGE_CASES_TECH: self._handle_cases_tech,
                STAGE_TRY_FULL: self._handle_try_full,
                STAGE_REFERRAL_ASK: self._handle_referral_ask,
                STAGE_WAIT_REFERRAL_TAG: self._handle_wait_referral_tag,
                STAGE_WAITING_REFERRAL: self._handle_waiting_referral,
                STAGE_RATE_VIDEO_2: self._handle_rate_video_2,
                STAGE_FEEDBACK_LOW_2: self._handle_feedback_low_2,
                STAGE_LAST_STEP_FORM: self._handle_last_step_form,
                STAGE_POST_SURVEY: self._handle_post_survey,
                STAGE_KEEP_IN_TOUCH: self._handle_keep_in_touch,
                STAGE_REMIND_RELEASE: self._handle_remind_release,
                STAGE_NO_FRIENDS_FORM: self._handle_no_friends_form,
            }
            handler = _PG_DISPATCH.get(st.stage)
            if handler:
                await handler(message, st)
                return

            # Unknown stage -> reset deterministically.
            await self._move_to_wait_audio(chat_id, message)

    async def _on_startup(self, bot: Bot) -> None:
        self._bot = bot
        self._bot_ref[0] = bot
        if not self.settings.tg_bot_token:
            raise RuntimeError("TG_BOT_TOKEN is empty")

        self.s3.validate_core()

        if not self.settings.s3_bucket_raw_audio:
            raise RuntimeError("S3_BUCKET_RAW_AUDIO is empty")

        self.settings.tmp_dir.mkdir(parents=True, exist_ok=True)

        await self.credits_db.init()

        # Start admin web panel as background task
        self._admin_panel_task = asyncio.create_task(
            start_admin_panel(
                self.credits_db, self.store, self.settings,
                tbank_client=self.tbank, bot_ref=self._bot_ref,
            ),
            name="admin_panel",
        )

        self._processing_task = asyncio.create_task(self._processing_loop(), name="tg_bot_processing_loop")
        self._recovery_task = asyncio.create_task(self._recovery_loop(), name="tg_bot_recovery_loop")
        self._reminder_task = asyncio.create_task(self._reminder_loop(), name="tg_bot_reminder_loop")
        self._payment_poll_task = asyncio.create_task(self._payment_poll_loop(), name="tg_bot_payment_poll")
        self._state_cleanup_task = asyncio.create_task(self._state_cleanup_loop(), name="tg_bot_state_cleanup_loop")
        self._fs_cleanup_task = asyncio.create_task(self._fs_cleanup_loop(), name="tg_bot_fs_cleanup_loop")
        log.info("startup complete: polling loop started")

    async def _on_shutdown(self, bot: Bot) -> None:
        del bot
        for task in [
            self._processing_task,
            self._recovery_task,
            self._state_cleanup_task,
            self._fs_cleanup_task,
            getattr(self, "_reminder_task", None),
            getattr(self, "_admin_panel_task", None),
            getattr(self, "_payment_poll_task", None),
        ]:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await self.orchestrator.close()
        await self.credits_db.close()
        await self.store.close()
        self._bot = None
        log.info("shutdown complete")

    async def _move_to_onboarding(self, chat_id: int, message: Message) -> None:
        await self.store.set_stage(chat_id, STAGE_WAIT_START)
        banner = Path(__file__).parent / "assets" / "blast_banner.jpg"
        welcome_text = (
            "Привет! Давай познакомимся. Это бот для нашего технологического решения: "
            "Blast — co-pilot в продвижении музыки.\n\n"
            "Наш AI-агент поможет артисту развивать контент: генерировать идеи и муз. ролики с нуля. "
            "Готов затестить его на своем треке?"
        )
        if banner.exists():
            await message.answer_photo(
                FSInputFile(banner),
                caption=welcome_text,
                reply_markup=_kb([BTN_LETS_GO]),
            )
        else:
            await message.answer(welcome_text, reply_markup=_kb([BTN_LETS_GO]))

    async def _move_to_subscription(self, chat_id: int, message: Message) -> None:
        await self.store.set_stage(chat_id, STAGE_WAIT_SUBSCRIPTION)
        await message.answer(
            "Супер! Тогда не будем медлить, единственное условие — подписка на наш тгк: @impulsemarketing\n\n"
            "Там делимся главными фишками по продукту и продвижения, которые помогают эффективно вести контент артисту.",
            reply_markup=_kb([BTN_SUBSCRIBED]),
        )

    async def _check_subscription(self, user_id: int) -> bool:
        bot = self._require_bot()
        try:
            member = await bot.get_chat_member(
                chat_id=self.settings.subscription_channel,
                user_id=user_id,
            )
            return member.status in {"member", "administrator", "creator"}
        except Exception as e:
            log.warning("subscription check failed for user_id=%s: %s", user_id, e)
            return False

    async def _handle_wait_start(self, message: Message, st: ChatState) -> None:
        if str(message.text or "").strip() == BTN_LETS_GO:
            await self._move_to_subscription(int(message.chat.id), message)
        else:
            await message.answer("Нажми «Едем!», чтобы продолжить.", reply_markup=_kb([BTN_LETS_GO]))

    async def _handle_wait_subscription(self, message: Message, st: ChatState) -> None:
        if str(message.text or "").strip() != BTN_SUBSCRIBED:
            await message.answer("Нажми «Подписался!», когда оформишь подписку.", reply_markup=_kb([BTN_SUBSCRIBED]))
            return
        user_id = int(message.from_user.id) if message.from_user else 0
        subscribed = await self._check_subscription(user_id)
        if not subscribed:
            await message.answer("Думаешь, мы не будем проверять подписку?)")
            await self._move_to_subscription(int(message.chat.id), message)
            return
        chat_id = int(message.chat.id)
        await self.credits_db.log_event(chat_id, "subscription_ok")
        # Grant initial credits after subscription (not on /start) to avoid
        # race conditions with deep-link users who never subscribe.
        if self.settings.initial_credits > 0:
            already_granted = await self.credits_db.has_initial_grant(chat_id)
            if not already_granted:
                await self.credits_db.add_credits(chat_id, self.settings.initial_credits, "initial_grant")
                await self.credits_db.log_event(chat_id, "initial_grant", f"+{self.settings.initial_credits}")
        await self._move_to_wait_audio(chat_id, message)

    async def _move_to_wait_audio(self, chat_id: int, message: Message) -> None:
        await self.store.reset_to_wait_audio(chat_id)
        bal = await self.credits_db.get_balance(chat_id)
        bal_text = f"\n\nДоступно генераций: {bal}" if bal > 0 else ""
        await message.answer(
            f"Привет. Отправь трек аудио-файлом, и я соберу клип.{bal_text}\n\n"
            "/packets — посмотреть тарифы",
            reply_markup=_kb([BTN_SEND_TRACK]),
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

    def _timing_label(self, st: ChatState) -> str:
        start = float(st.user_clip_start_sec or 0.0)
        end = float(st.user_clip_end_sec or 0.0)
        if end > start > 0.0:
            return f"{self._fmt_timing(start)} - {self._fmt_timing(end)}"
        return "весь трек / на усмотрение ИИ"

    def _source_label(self, st: ChatState) -> str:
        artist_id = str(st.footage_artist_id or "").strip()
        if not artist_id:
            return "на усмотрение ИИ"
        genre_key = str(st.footage_genre_key or "").strip()
        if genre_key:
            try:
                for artist in get_artists(genre_key):
                    if str(artist.get("key") or "").strip() == artist_id:
                        label = str(artist.get("label") or "").strip()
                        if label:
                            return label
            except Exception:
                pass
        return artist_id

    async def _ask_timing_choice(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_TIMING_CHOICE
        st.user_clip_start_sec = 0.0
        st.user_clip_end_sec = 0.0
        await self.store.set(st)
        await message.answer(
            "Хочешь указать конкретный тайминг трека для клипа?\n"
            "Например: 1:20-1:50 или 80-110 (в секундах).\n"
            "Максимальный тайминг: 25с.",
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
            await self._ask_footage_genre(message, st)
            return
        await message.answer(
            "Выбери кнопку: «Указать тайминг» или «На усмотрение ИИ».",
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
            f"Тайминг установлен: {self._fmt_timing(start_sec)} - {self._fmt_timing(end_sec)} ({duration:.0f} сек)."
        )
        await self._ask_footage_genre(message, st)

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
            await self._ask_timing_choice(message, st)
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
            preview_fid_public = str(artist.get("preview_file_id_public") or "").strip()
            preview_fid_legacy = str(artist.get("preview_file_id") or "").strip()
            preview_fid = preview_fid_public or preview_fid_legacy
            preview_url = str(artist.get("preview_s3_url") or "").strip()
            description = str(artist.get("description") or "")
            caption = f"{artist['label']}: {description}"
            sent = False
            if preview_fid:
                try:
                    await message.answer_video(video=preview_fid, caption=caption)
                    sent = True
                except Exception as exc:
                    log.warning("failed to send preview for %s (file_id): %s", artist["key"], str(exc))

            if not sent:
                fallback_video = preview_url
                if not fallback_video and preview_fid:
                    fallback_video = await self._resolve_preview_source_file_url(preview_fid)
                if fallback_video:
                    try:
                        await message.answer_video(video=fallback_video, caption=caption)
                        sent = True
                    except Exception:
                        log.warning("failed to send preview for %s (fallback)", artist["key"])

            if not sent:
                # Keep UX explicit instead of silently skipping previews.
                await message.answer(f"{artist['label']}: {description}")

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
            st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND
        await self.store.set(st)
        # Send example videos for each mode
        for btn_name, file_id in _SUBTITLES_EXAMPLE_VIDEO.items():
            await message.answer_video(video=file_id, caption=f"Пример: *{btn_name}*", parse_mode="Markdown")
        await message.answer(
            "Выбери режим субтитров:",
            reply_markup=_kb(
                [BTN_SUB_MODE_IMPULSE],
                [BTN_SUB_MODE_SCENES],
                [BTN_SUB_MODE_4TH],
            ),
        )

    async def _handle_wait_audio(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_ALL_PACKAGES:
            await self._show_all_packages(message, st)
            return
        if text in (BTN_SEND_TRACK, BTN_GENERATE_MORE):
            await message.answer("Жду аудио-файл.", reply_markup=ReplyKeyboardRemove())
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
        await self.credits_db.log_event(st.chat_id, "audio_uploaded", original_name)
        st.lyrics_text = ""
        st.target_fragment = ""
        st.footage_genre_key = ""
        st.footage_artist_key = ""
        st.footage_artist_id = ""
        st.user_clip_start_sec = 0.0
        st.user_clip_end_sec = 0.0
        st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND
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

        await message.answer(
            "Трек готов! Пришли текст песни — это улучшит точность субтитров. "
            "Если не отправишь, ИИ попробует распознать слова сам, но может ошибиться.",
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
            await self._ask_timing_choice(message, st)
            return

        await message.answer("Выбери кнопку: «Отправить текст» или «Пусть ИИ угадает».")

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
            "Текст получил. Хочешь указать конкретные строки, которые должны войти в клип?",
            reply_markup=_kb([BTN_SEND_FRAGMENT, BTN_SKIP_FRAGMENT]),
        )

    async def _handle_wait_fragment_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_FRAGMENT:
            st.stage = STAGE_WAIT_FRAGMENT_TEXT
            await self.store.set(st)
            await message.answer(
                "Скопируй и пришли нужные строки прямо из текста песни — те слова, которые хочешь видеть в клипе. "
                "Например — припев трека.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if text == BTN_SKIP_FRAGMENT:
            st.target_fragment = ""
            await self._ask_timing_choice(message, st)
            return

        await message.answer("Выбери кнопку: «Указать строки из текста» или «На усмотрение ИИ».")

    async def _handle_wait_fragment_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if not text:
            await message.answer("Жду интересующий фрагмент обычным текстовым сообщением.")
            return
        if _is_control_button_text(text):
            await message.answer("Нужны именно строки из текста песни — скопируй их и пришли сообщением.")
            return

        st.target_fragment = text
        st.stage = STAGE_WAIT_CONFIRM_TEXT
        await self.store.set(st)

        lyrics_preview = st.lyrics_text[:200] + ("…" if len(st.lyrics_text) > 200 else "")
        await message.answer(
            f"Подтвердить текст?\n\n"
            f"*Текст песни:*\n{lyrics_preview}\n\n"
            f"*Строки из текста:*\n{st.target_fragment}",
            reply_markup=_kb([BTN_CONFIRM_YES, BTN_CONFIRM_BACK]),
            parse_mode="Markdown",
        )

    async def _handle_wait_confirm_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_CONFIRM_YES:
            await self._ask_timing_choice(message, st)
            return
        if text == BTN_CONFIRM_BACK:
            st.lyrics_text = ""
            st.target_fragment = ""
            st.stage = STAGE_WAIT_LYRICS_CHOICE
            await self.store.set(st)
            await message.answer(
                "Пришли текст песни — это улучшит точность субтитров. "
                "Если не отправишь, ИИ попробует распознать слова сам, но может ошибиться.",
                reply_markup=_kb([BTN_SEND_LYRICS, BTN_SKIP_LYRICS]),
            )
            return
        await message.answer("Выбери: «Да» или «Вернуться назад».", reply_markup=_kb([BTN_CONFIRM_YES, BTN_CONFIRM_BACK]))

    async def _handle_wait_subtitles_mode(self, message: Message, st: ChatState) -> None:
        mode = _parse_subtitles_mode_choice(message.text or "")
        if mode is None:
            await message.answer(
                "Выбери режим кнопкой: «Impulse», «Jakson» или «Tape»."
            )
            return
        st.subtitles_mode = mode

        mode_display = _BUTTON_BY_SUBTITLES_MODE.get(mode, mode)
        st.stage = STAGE_WAIT_CONFIRM_MODE
        await self.store.set(st)
        await message.answer(
            f"Подтвердить режим субтитров?\n*Режим субтитров:* «{mode_display}»",
            parse_mode="Markdown",
            reply_markup=_kb([BTN_CONFIRM_YES, BTN_CONFIRM_BACK]),
        )

    async def _handle_wait_confirm_mode(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_CONFIRM_YES:
            # If user has paid — offer version count selection (1-5)
            paid = await self.credits_db.has_paid(st.chat_id)
            if paid:
                st.stage = STAGE_WAIT_VERSIONS
                await self.store.set(st)
                await message.answer(
                    "Сколько версий сгенерировать?",
                    reply_markup=_kb(["1", "2", "3", "4", "5"]),
                )
                return
            # Free users — always 1 version
            st.versions_count = 1
            st.stage = STAGE_WAIT_CONFIRM
            await self.store.set(st)
            mode_display = _BUTTON_BY_SUBTITLES_MODE.get(st.subtitles_mode, st.subtitles_mode)
            fragment_display = st.target_fragment or "на усмотрение ИИ"
            timing_display = self._timing_label(st)
            source_display = self._source_label(st)
            await message.answer(
                f"*Режим субтитров:* «{mode_display}»\n"
                f"*Фрагмент:* «{fragment_display}»\n\n"
                f"*Тайминг:* «{timing_display}»\n"
                f"*Исходники:* «{source_display}»\n\n"
                f"Запустить генерацию?",
                parse_mode="Markdown",
                reply_markup=_kb([BTN_LAUNCH, BTN_RESTART]),
            )
            return
        if text == BTN_CONFIRM_BACK:
            await self._ask_subtitles_mode(message, st)
            return
        await message.answer("Выбери: «Да» или «Вернуться назад».", reply_markup=_kb([BTN_CONFIRM_YES, BTN_CONFIRM_BACK]))

    async def _handle_wait_versions(self, message: Message, st: ChatState) -> None:
        n = _parse_versions_choice(message.text or "")
        if n is None:
            await message.answer("Выбери количество версий: 1, 2, 3, 4 или 5.")
            return
        st.versions_count = int(n)
        bal = await self.credits_db.get_balance(st.chat_id)
        if int(n) > bal:
            await message.answer(
                f"Недостаточно генераций. У тебя {bal}, а выбрано {n}. Выбери меньше.",
                reply_markup=_kb(["1", "2", "3", "4", "5"]),
            )
            return
        st.stage = STAGE_WAIT_CONFIRM
        await self.store.set(st)
        mode_display = _BUTTON_BY_SUBTITLES_MODE.get(st.subtitles_mode, st.subtitles_mode)
        fragment_display = st.target_fragment or "на усмотрение ИИ"
        timing_display = self._timing_label(st)
        source_display = self._source_label(st)
        await message.answer(
            f"*Режим субтитров:* «{mode_display}»\n"
            f"*Фрагмент:* «{fragment_display}»\n"
            f"*Тайминг:* «{timing_display}»\n"
            f"*Исходники:* «{source_display}»\n"
            f"*Версий:* {n}\n\n"
            f"Запустить генерацию?",
            parse_mode="Markdown",
            reply_markup=_kb([BTN_LAUNCH, BTN_RESTART]),
        )

    async def _handle_wait_confirm(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_RESTART:
            if message.chat is not None:
                await self._move_to_wait_audio(int(message.chat.id), message)
            return
        if text != BTN_LAUNCH:
            await message.answer("Нажми «Запустить» или «Начать заново».", reply_markup=_kb([BTN_LAUNCH, BTN_RESTART]))
            return

        if message.chat is None:
            return

        chat_id = int(message.chat.id)
        user_id = message.from_user.id if message.from_user else chat_id

        # Check and reserve credits before generation (1 credit per version)
        versions = st.versions_count or 1
        balance = await self.credits_db.get_balance(user_id)
        if balance < versions:
            await self.credits_db.log_event(chat_id, "no_credits")
            await message.answer(
                "Твои кредиты закончились. Хочешь посмотреть тарифы?",
                reply_markup=_kb([BTN_ALL_PACKAGES]),
            )
            st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            return

        # Reserve credits upfront to prevent double-spend
        for _ in range(versions):
            await self.credits_db.deduct_credit(chat_id)
        await self.credits_db.log_event(chat_id, "credits_reserved", f"versions={versions}")

        prepared_path = Path(st.prepared_audio_local_path).expanduser().resolve()
        if not prepared_path.exists():
            await message.answer("Подготовленный mp3 не найден. Пришли трек заново.")
            await self._move_to_wait_audio(chat_id, message)
            return

        key = self._build_raw_audio_key(chat_id=chat_id, file_name=prepared_path.name)
        try:
            versions = st.versions_count or 1
            await message.answer("Запускаю генерацию…")
            audio_s3_url = await asyncio.to_thread(
                self.s3.upload_file,
                path=prepared_path,
                bucket=self.settings.s3_bucket_raw_audio,
                key=key,
                content_type="audio/mpeg",
            )

            batch_id = f"tg-{chat_id}-{uuid.uuid4().hex[:12]}"
            master_job_id = await self._enqueue_batch_version(
                st=st,
                audio_s3_url=audio_s3_url,
                version_index=1,
                versions_total=versions,
                batch_id=batch_id,
                reuse_text_job_id="",
                exclude_file_names=[],
            )

            await self.credits_db.log_event(chat_id, "generation_started", f"batch={batch_id}")
            st.stage = STAGE_PROCESSING
            st.batch_id = batch_id
            st.batch_audio_s3_url = audio_s3_url
            st.batch_total_versions = int(versions)
            st.next_version_to_enqueue = 2
            st.master_job_id = master_job_id
            st.job_order = [master_job_id]
            st.used_footage_file_names = []
            st.active_job_id = master_job_id
            st.active_job_ids = [master_job_id]
            st.completed_job_ids = []
            st.active_job_started_at = time.time()
            st.last_status_msg_at = 0.0
            st.status_message_id = 0
            st.last_status_text = ""
            st.poll_attempts = 0
            st.last_job_stage = ""
            st.last_job_error = ""
            st.last_result_url = ""

            initial_rows = [
                {"job_id": master_job_id, "status": "QUEUED", "stage": "build", "error": "", "version": 1}
            ]
            initial_text = self._jobs_progress_message(
                rows=initial_rows,
                poll_attempts=0,
                total_versions=versions,
            )
            sent = await message.answer(initial_text)
            st.status_message_id = int(getattr(sent, "message_id", 0) or 0)
            st.last_status_text = initial_text
            st.last_status_msg_at = time.time()
            await self.store.set(st)
        except Exception as e:
            err_text = str(e)
            if versions > 0:
                try:
                    await self.credits_db.add_credits(
                        chat_id,
                        int(versions),
                        "generation_failed_refund",
                        admin_note="batch=enqueue_start_failed",
                    )
                except Exception as add_e:
                    log.warning("enqueue_start_refund_failed chat=%s err=%s", chat_id, str(add_e))
            await self.credits_db.log_event(
                chat_id,
                "generation_failed",
                f"job=enqueue_start stage=enqueue_start error={_compact_text(err_text, limit=140)}",
            )
            await self._notify_manager_generation_failure(
                st=st,
                job_id="enqueue_start",
                stage="enqueue_start",
                error_text=err_text,
                succeeded_versions=0,
                total_versions=int(versions),
                refunded_versions=int(versions),
            )
            await message.answer(_GENERATION_FAILED_USER_TEXT, reply_markup=_kb([BTN_SEND_TRACK]))
            self._reset_processing_state(st, next_stage=STAGE_WAIT_AUDIO)
            await self.store.set(st)

    async def _handle_wait_next(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text != BTN_NEXT:
            await message.answer(
                "Если хочешь новый ролик, нажми «Сделать следующий».\n\n"
                "/sendtrack — отправить трек\n"
                "/packets — посмотреть тарифы",
                reply_markup=_kb([BTN_NEXT]),
            )
            return

        if message.chat is None:
            return

        await self._move_to_wait_audio(int(message.chat.id), message)

    # ── Package descriptions ──────────────────────────────────────────
    _PKG_PHOTOS = {
        BTN_PKG_TRIAL: "tariffs/1011",
        BTN_PKG_BLAST: "tariffs/1008",
        BTN_PKG_GLOW: "tariffs/1009",
        BTN_PKG_IMPULSE: "tariffs/1010",
    }

    _PKG_TEXTS = {
        BTN_PKG_TRIAL: (
            "Бласт Trial — 990₽\n\n"
            "Пробный запуск для одного трека.\n\n"
            "Отправляешь mp3 и текст — ИИ генерирует контент-план, трендовые идеи "
            "для роликов и 5 готовых видео под твой звук.\n\n"
            "Быстрый способ проверить потенциал трека перед тем, как вкладываться "
            "масштабно.\n\n"
            "Нажимай «Приобрести» — и через несколько минут 5 роликов под твой "
            "трек будут готовы."
        ),
        BTN_PKG_BLAST: (
            "Бласт — 1 990₽/мес\n\n"
            "Ежемесячное продвижение на автопилоте.\n\n"
            "Отправляешь трек и текст — ИИ строит контент-план, придумывает "
            "трендовые форматы и создаёт 15 видео под твой звук.\n\n"
            "Держишь соцсети живыми, органически растишь нужную аудиторию "
            "и повышаешь шансы попасть в тренд.\n\n"
            "Нажимай «Приобрести» — и соцсети работают на тебя каждый месяц."
        ),
        BTN_PKG_GLOW: (
            "Глоу — 7 990₽\n\n"
            "Глубокое тестирование трека с подключением блогеров.\n\n"
            "ИИ генерирует контент-план и 30 видео под твой звук — мы выявляем "
            "лучшие форматы, затем контент-менеджер находит двух подходящих "
            "блогеров и закупает по ролику для поддержки трека. Максимум данных, "
            "реальный охват и шанс задать тренд.\n\n"
            "Нажимай «Приобрести» — запусти трек расти."
        ),
        BTN_PKG_IMPULSE: (
            "Импульс — 29 990₽\n\n"
            "Полноценная кампания от идеи до масштабирования.\n\n"
            "Маркетинг и стратегия. Разрабатываем 5 контентных форматов для "
            "продвижения трека, составляем детальный контент-план с референсами "
            "и планом съёмок, выстраиваем общую стратегию для измерения потенциала "
            "тренда в цифрах.\n\n"
            "Контент.\n"
            "Генерируем 25 роликов, тестируем, определяем лучшие форматы — "
            "и масштабируем ещё 25 роликами.\n\n"
            "Посевы.\n"
            "Закупаем 10–12 роликов у каждого блогера под трек.\n\n"
            "В итоге ты получаешь проверенный материал с подтверждёнными цифрами, "
            "большой пул контента и реальный шанс создать тренд.\n\n"
            "Нажимай «Приобрести» — это уже не просто трек, это целая рекламная "
            "кампания."
        ),
    }

    # ── Post-generation flow handlers ────────────────────────────────

    async def _send_rating_prompt(self, bot, chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text, reply_markup=_kb(BTN_RATE_BUTTONS))

    async def _notify_manager(self, username: str, package: str) -> None:
        mgr = self.settings.manager_chat_id
        if not mgr:
            log.warning("MANAGER_CHAT_ID not set, cannot notify")
            return
        bot = self._require_bot()
        try:
            await bot.send_message(
                mgr,
                f"🔔 Новая заявка!\n\nПользователь: @{username}\nПакет: {package}",
            )
        except Exception as e:
            log.warning("manager_notify_failed err=%s", str(e))

    async def _notify_manager_generation_error(self, *, username: str, chat_id: int, job_id: str, stage: str, error_text: str) -> None:
        mgr = self.settings.manager_chat_id
        if not mgr:
            return
        bot = self._require_bot()
        user_tag = f"@{username}" if username else str(chat_id)
        lines = [
            "⚠️ Ошибка генерации!",
            "",
            f"Артист: {user_tag}",
            f"Job: {job_id}",
            f"Стадия: {stage or '-'}",
        ]
        if error_text:
            lines.append(f"Ошибка: {_compact_text(error_text, limit=500)}")
        try:
            await bot.send_message(mgr, "\n".join(lines))
        except Exception as e:
            log.warning("manager_generation_error_notify_failed err=%s", str(e))

    async def _notify_manager_payment(self, username: str, package: str, amount: int, status: str) -> None:
        mgr = self.settings.manager_chat_id
        if not mgr:
            return
        bot = self._require_bot()
        emojis = {"Создан": "📋", "Оплачено": "✅", "Отклонено": "❌", "Возврат": "🔄"}
        emoji = emojis.get(status, "📋")
        price_str = f"{amount:,}".replace(",", ".")
        try:
            await bot.send_message(
                mgr,
                f"{emoji} Статус оплаты: {status}\n\n"
                f"Пользователь: @{username}\n"
                f"Пакет: {package}\n"
                f"Сумма: {price_str}₽",
            )
        except Exception as e:
            log.warning("manager_payment_notify_failed err=%s", str(e))

    async def _notify_finance_bot_income(self, amount: int, username: str, package: str) -> None:
        """Отправить доход в finance-bot для учёта в конвертах."""
        url = self.settings.finance_bot_url.rstrip("/") + "/webhook/income"
        payload = {"amount": amount, "source": "blast", "client": f"{username} — {package}"}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    log.info("finance_bot income ok amount=%s client=%s", amount, username)
                else:
                    log.warning("finance_bot income err status=%s body=%s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("finance_bot income request failed: %s", e)

    async def _notify_manager_generation_failure(
        self,
        *,
        st: ChatState,
        job_id: str,
        stage: str,
        error_text: str,
        succeeded_versions: int,
        total_versions: int,
        refunded_versions: int,
    ) -> None:
        mgr = self.settings.manager_chat_id
        if not mgr:
            return
        bot = self._require_bot()
        username = str(st.chat_username or "").strip()
        uname = f"@{username}" if username else "(нет username)"
        err_short = _compact_text(error_text or "без деталей", limit=700)
        try:
            await bot.send_message(
                mgr,
                "⚠️ Generation error (public bot)\n\n"
                f"Пользователь: {uname}\n"
                f"chat_id: {st.chat_id}\n"
                f"job_id: {job_id}\n"
                f"stage: {stage or '-'}\n"
                f"Успешно версий: {succeeded_versions}/{total_versions}\n"
                f"Возврат кредитов: {refunded_versions}\n\n"
                f"Ошибка: {err_short}",
            )
        except Exception as e:
            log.warning("manager_generation_failure_notify_failed chat=%s err=%s", st.chat_id, str(e))

    async def _send_survey_link(self, message: Message) -> None:
        chat_id = int(message.chat.id) if message.chat else 0
        if chat_id:
            await self.credits_db.log_event(chat_id, "survey_opened")
        await message.answer(
            f"Вот ссылка на опросник:\n{self.settings.survey_url}\n\n"
            "В конце оставь свой реальный тег телеграмма, иначе не сможем "
            "связаться и прислать ролики.",
            reply_markup=_kb([BTN_SURVEY_DONE]),
        )

    async def _show_all_packages(self, message: Message, st: ChatState) -> None:
        await self.credits_db.log_event(st.chat_id, "view_packages")
        st.stage = STAGE_ALL_PACKAGES
        await self.store.set(st)
        await message.answer(
            "Вот пул пакетов:\n"
            "— Бласт Trial за 990₽ (5 роликов)\n"
            "— Бласт за 1 990₽/мес (15 роликов)\n"
            "— Глоу за 7 990₽ (30 роликов + 2 блогера)\n"
            "— Импульс за 29 990₽ (50 роликов + посевы)\n\n"
            "О каком рассказать подробнее?",
            reply_markup=_kb([BTN_PKG_TRIAL], [BTN_PKG_BLAST], [BTN_PKG_GLOW], [BTN_PKG_IMPULSE]),
        )

    async def _show_why_not(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WHY_NOT
        await self.store.set(st)
        await message.answer(
            "Почему не актуально прямо сейчас?",
            reply_markup=_kb([BTN_NO_RELEASE], [BTN_NO_MONEY], [BTN_BAD_QUALITY], [BTN_DOUBT_EFFECT]),
        )

    async def _show_referral_ask(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_REFERRAL_ASK
        await self.store.set(st)
        await message.answer(
            "Так, чтобы прямо сейчас получить 2-й ролик — нужно позвать друга в бота. "
            "Уверен, ты найдешь среди своих контактов хотя бы одного артиста, которому "
            "можешь посоветовать наш сервис.\n\n"
            "Как только он активирует бота — мы сразу отправим новый ролик. "
            "В ответном сообщении тебе нужно будет прислать его тег, начинающийся с @, "
            "например: @impulsemanage",
            reply_markup=_kb([BTN_SEND_NOW], [BTN_NEED_SEARCH], [BTN_NO_FRIENDS]),
        )

    _PKG_PRICES = {
        "Триал": 990,
        "Бласт": 1990,
        "Глоу": 7990,
        "Импульс": 29990,
    }

    _PKG_CREDITS = {
        "Триал": 5,
        "Бласт": 15,
        "Глоу": 30,
        "Импульс": 50,
    }

    async def _show_purchase_stub(self, message: Message, st: ChatState) -> None:
        username = (st.chat_username or "").lstrip("@") or str(st.chat_id)
        pkg = st.selected_package or "не указан"
        await self.credits_db.log_event(st.chat_id, "purchase_intent", pkg)

        price = self._PKG_PRICES.get(pkg, 0)

        # Try to create T-Bank payment link
        if self.tbank and price > 0:
            order_id = f"{st.chat_id}-{pkg.replace(' ', '_')}-{uuid.uuid4().hex[:8]}"
            try:
                last_utm = await self.credits_db.get_last_utm(st.chat_id)
                await self.credits_db.create_payment(order_id, st.chat_id, price, pkg, utm=last_utm)
                pay_url = await self.tbank.create_payment(
                    amount_rub=price,
                    order_id=order_id,
                    description=f"Пакет «{pkg}»",
                )
                if pay_url:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    buttons = [
                        [InlineKeyboardButton(text=f"Оплатить {price:,}₽".replace(",", "."), url=pay_url)],
                    ]
                    if self.settings.offer_url:
                        buttons.append([InlineKeyboardButton(text="Договор оферты", url=self.settings.offer_url)])
                    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
                    price_str = f"{price:,}".replace(",", ".")
                    await message.answer(
                        f"Отлично! Пакет «{pkg}» — {price_str}₽.\n\n"
                        "Нажми кнопку ниже для оплаты. После успешной оплаты кредиты "
                        "начислятся автоматически.\n\n"
                        "У нас все официально: прозрачный эквайринг и, конечно, чек об оплате.",
                        reply_markup=ReplyKeyboardRemove(),
                    )
                    await message.answer(
                        "Ссылка на оплату:",
                        reply_markup=kb,
                    )
                    await self._notify_manager_payment(username, pkg, price, "Создан")
                    return
            except Exception as e:
                log.warning("tbank payment creation failed: %s", e)

        # Fallback: manager contact
        await message.answer(
            "Рады, что ты решился попробовать. С тобой свяжется наш менеджер и уточнит "
            "все интересующие моменты по продукту. Отпишем с этого аккаунта: @impulsemanage\n\n"
            "У нас все официально: прозрачный эквайринг и, конечно, чек об оплате.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await self._notify_manager(username, pkg)

    # --- Rating first video ---
    async def _handle_rate_video(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_RATE_LOW:
            await self.credits_db.log_event(st.chat_id, "rate_video", "low")
            st.last_rating = "low"
            st.stage = STAGE_FEEDBACK_LOW
            await self.store.set(st)
            await message.answer(
                "Приняли, готовы услышать обратную связь и вместе с ней исправить ролики "
                "— бесплатно.\n\n"
                "Давай, ты пройдешь 5 вопросов в форме (там меньше минуты), за счет них "
                "мы поймем, как улучшить ролики.\n\n"
                "Учтем твою обратку и пришлем 2 ролика с исправлениями, так, чтобы тебе "
                "вкатило. Сделаем?",
                reply_markup=_kb([BTN_LETS_DO_IT]),
            )
        elif text == BTN_RATE_MID_LOW:
            await self.credits_db.log_event(st.chat_id, "rate_video", "mid_low")
            st.last_rating = "mid"
            st.stage = STAGE_SALES_PITCH
            await self.store.set(st)
            await message.answer(
                "Супер, значит мы близко!\n\n"
                "Обычно артисты застревают на одном ролике и хаотичных попытках. "
                "Blast решает это — технология помогает вести контент регулярно и эффективно.\n\n"
                "Прямо сейчас ты можешь выстроить систему из контента: без страха съемок, "
                "ужасов монтажа, проблем выкладки и инфантильных менеджеров.",
                reply_markup=_kb([BTN_HOW_SO]),
            )
        elif text in {BTN_RATE_MID_HIGH, BTN_RATE_HIGH}:
            await self.credits_db.log_event(st.chat_id, "rate_video", "high")
            st.last_rating = "high"
            st.stage = STAGE_SALES_PITCH
            await self.store.set(st)
            await message.answer(
                "Отлично, значит мы попали!\n\n"
                "Обычно артисты застревают на одном ролике и хаотичных попытках что-то "
                "смонтировать и выложить. Blast решает это — технология помогает вести "
                "контент регулярно и эффективно.\n\n"
                "Прямо сейчас ты можешь выстроить систему из контента: без страха съемок, "
                "ужасов монтажа, проблем выкладки и инфантильных менеджеров.",
                reply_markup=_kb([BTN_HOW_SO]),
            )
        else:
            await message.answer(
                "Выбери оценку из кнопок ниже.",
                reply_markup=_kb(BTN_RATE_BUTTONS),
            )

    # --- Feedback low (first video "До 5") ---
    async def _handle_feedback_low(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_LETS_DO_IT:
            await self._send_survey_link(message)
            st.stage = STAGE_IDLE
            await self.store.set(st)
        else:
            await message.answer("Нажми «Делаем!»", reply_markup=_kb([BTN_LETS_DO_IT]))

    # --- Sales pitch ("Как же?") ---
    async def _handle_sales_pitch(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_HOW_SO:
            await self.credits_db.log_event(st.chat_id, "sales_pitch")
            st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            await message.answer(
                "Мы только запускаем технологию — и делаем вкусное предложение первым юзерам.\n\n"
                "Подписка на Бласт: 15 роликов в месяц за 1.990₽ вместо 4.000₽. -50% off. "
                "Это наш ходовой пакет:\n"
                "— видео под твой стиль, жанр и настроение\n"
                "— регулярный контент, выкладка и аналитика\n"
                "— без съёмок, продюсеров и мишуры\n\n"
                "Отмена в любой момент, удвоение роликов, блогеры и дистрибьюция со "
                "следующих месяцев. Рассказать больше про все плюшки?",
                reply_markup=_kb([BTN_TELL_MORE], [BTN_ALL_PACKAGES], [BTN_NOT_NOW]),
            )
        else:
            await message.answer("Нажми «Как же?»", reply_markup=_kb([BTN_HOW_SO]))

    # --- Packages offer (Рассказывайте / Все пакеты / Пока неактуально) ---
    async def _handle_packages_offer(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_TELL_MORE:
            st.stage = STAGE_PACKAGE_DETAILS
            await self.store.set(st)
            await message.answer(
                "Отлично! В первый же месяц ты получишь:\n"
                "— 15 индивидуальных роликов, которые автоматически появятся на твоем "
                "аккаунте в соц. сетях, их развернутую аналитику и рекомендации.\n\n"
                "Во второй месяц — удвоение роликов, то есть 30 шт, в третий — бонусного "
                "блогера, в четвертый — безлимитную дистрибьюции через наших партнеров.\n\n"
                "Готов попробовать все преимущества Бласта за 2.000₽ в месяц?",
                reply_markup=_kb([BTN_READY], [BTN_ALL_PACKAGES], [BTN_MAYBE_LATER]),
            )
        elif text == BTN_ALL_PACKAGES:
            await self._show_all_packages(message, st)
        elif text == BTN_NOT_NOW:
            await self._show_why_not(message, st)
        else:
            await message.answer(
                "Выбери из кнопок ниже.",
                reply_markup=_kb([BTN_TELL_MORE], [BTN_ALL_PACKAGES], [BTN_NOT_NOW]),
            )

    # --- Package details (Готов / Все пакеты / Чуть позже) ---
    async def _handle_package_details(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_READY:
            st.selected_package = "Бласт"
            await self._show_purchase_stub(message, st)
            st.stage = STAGE_IDLE
            await self.store.set(st)
        elif text == BTN_ALL_PACKAGES:
            await self._show_all_packages(message, st)
        elif text == BTN_MAYBE_LATER:
            await self._show_referral_ask(message, st)
        else:
            await message.answer(
                "Выбери из кнопок ниже.",
                reply_markup=_kb([BTN_READY], [BTN_ALL_PACKAGES], [BTN_MAYBE_LATER]),
            )

    # --- All packages list ---
    async def _handle_all_packages(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text in self._PKG_TEXTS:
            await self.credits_db.log_event(st.chat_id, "select_package", text)
            st.selected_package = text
            st.stage = STAGE_PACKAGE_INFO
            await self.store.set(st)
            s3_key = self._PKG_PHOTOS.get(text)
            if s3_key and self.settings.s3_bucket_asset_storage:
                bot = self._require_bot()
                try:
                    tmp = Path(self.settings.bot_tmp_dir) / f"pkg_{s3_key.replace('/', '_')}"
                    self.s3.download_file(
                        bucket=self.settings.s3_bucket_asset_storage,
                        key=s3_key,
                        dest=tmp,
                    )
                    await bot.send_photo(st.chat_id, photo=FSInputFile(tmp))
                    tmp.unlink(missing_ok=True)
                except Exception as e:
                    log.warning("pkg_photo_send_failed pkg=%s err=%s", text, str(e))
            await message.answer(
                self._PKG_TEXTS[text],
                reply_markup=_kb([BTN_TO_TARIFFS], [BTN_NOT_NOW], [BTN_PURCHASE]),
            )
        else:
            await self._show_all_packages(message, st)

    # --- Package info (К тарифам / Пока неактуально / Приобрести) ---
    async def _handle_package_info(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_TO_TARIFFS:
            await self._show_all_packages(message, st)
        elif text == BTN_NOT_NOW:
            await self._show_why_not(message, st)
        elif text == BTN_PURCHASE:
            await self._show_purchase_stub(message, st)
            st.stage = STAGE_IDLE
            await self.store.set(st)
        else:
            await message.answer(
                "Выбери из кнопок ниже.",
                reply_markup=_kb([BTN_TO_TARIFFS], [BTN_NOT_NOW], [BTN_PURCHASE]),
            )

    # --- Why not actual ---
    async def _handle_why_not(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text in {BTN_NO_RELEASE, BTN_NO_MONEY}:
            st.stage = STAGE_NOT_ACTUAL_REASON
            await self.store.set(st)
            await message.answer(
                "Давай мы напомним о себе чуть позже, а ты пока получишь следующие "
                "ролики под текущий трек!",
                reply_markup=_kb([BTN_AGREED]),
            )
        elif text in {BTN_BAD_QUALITY, BTN_DOUBT_EFFECT}:
            st.stage = STAGE_CASES_TECH
            await self.store.set(st)
            await message.answer(
                "Если сомневаешься в качестве или эффективности роликов, то можем детальнее "
                "рассказать про нашу экспертизу, на основе которой построена технология и "
                "подсветить кейсы.\n\n"
                "А еще, тебе никто не мешает выложить ролики в соц. сети и на практике "
                "убедиться в том, насколько контент — эффективный инструмент.",
                reply_markup=_kb([BTN_ABOUT_CASES]),
            )
        else:
            await self._show_why_not(message, st)

    # --- Not actual reason -> Договорились! ---
    async def _handle_not_actual_reason(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_AGREED:
            await self._show_referral_ask(message, st)
        else:
            await message.answer("Нажми «Договорились!»", reply_markup=_kb([BTN_AGREED]))

    # --- Cases & tech ---
    async def _handle_cases_tech(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_ABOUT_CASES:
            st.stage = STAGE_TRY_FULL
            await self.store.set(st)
            await message.answer(
                "Отлично! Наши ключевые кейсы — это Тик-Ток хиты 24 и 25 года и надеемся, "
                "что 2026-й с появлением технологии порадует нас еще большим количеством трендов.\n\n"
                "Все о наших артистах мы уложили в посте @impulsemarketing — мы продюсировали "
                "контент с нуля, закупали блогеров и глубоко анализировали эти тренды.\n\n"
                "На основе этого опыта мы строим нашу технологию: переносяь все ходовые идеи, "
                "форматы и подход, чтобы добиться максимального результата для артиста в любом жанре.",
                reply_markup=_kb([BTN_GOT_IT]),
            )
        else:
            await message.answer("Нажми «Про кейсы и технологию»", reply_markup=_kb([BTN_ABOUT_CASES]))

    # --- Try full (Готов! / Может, позже) ---
    async def _handle_try_full(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_GOT_IT:
            await message.answer(
                "Что ж, как на счет того, чтобы попробовать Бласт на полную?",
                reply_markup=_kb([BTN_READY, BTN_MAYBE_LATER]),
            )
            st.stage = STAGE_TRY_FULL
            await self.store.set(st)
            return
        if text == BTN_READY:
            await self._show_all_packages(message, st)
        elif text == BTN_MAYBE_LATER:
            await self._show_referral_ask(message, st)
        else:
            await message.answer(
                "Выбери из кнопок ниже.",
                reply_markup=_kb([BTN_READY, BTN_MAYBE_LATER]),
            )

    # --- Referral ask ---
    async def _handle_referral_ask(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text in {BTN_SEND_NOW, BTN_NEED_SEARCH}:
            st.stage = STAGE_WAIT_REFERRAL_TAG
            await self.store.set(st)
            await message.answer(
                "Супер, ожидаем тег, как только друг подпишется на тг-бота, "
                "мы сразу пришлем тебе следующее сообщение."
            )
        elif text == BTN_NO_FRIENDS:
            st.stage = STAGE_NO_FRIENDS_FORM
            await self.store.set(st)
            await message.answer(
                "Окей! Тогда остался последний шаг, чтобы получить все ролики.\n\n"
                "Тебе нужно всего лишь пройти форму обратной связи: ответить на 5 вопросов "
                "о контенте и менеджер пришлет тебе еще два ролика.\n\n"
                "Проще простого, не правда ли?",
                reply_markup=_kb([BTN_TO_FORM]),
            )
        else:
            await self._show_referral_ask(message, st)

    # --- No friends form ---
    async def _handle_no_friends_form(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_TO_FORM:
            await self._send_survey_link(message)
            st.stage = STAGE_IDLE
            await self.store.set(st)
        else:
            await message.answer("Нажми «В форму»", reply_markup=_kb([BTN_TO_FORM]))

    # --- Wait referral tag ---
    async def _find_chat_by_username(self, username: str, *, exclude_chat_id: int | None = None) -> bool:
        found = await self.store.find_chat_id_by_username(username)
        if found is None:
            return False
        if exclude_chat_id is not None and int(found) == int(exclude_chat_id):
            return False
        return True

    async def _activate_referral_reward(self, *, referrer_st: ChatState, referral_tag: str) -> None:
        referrer_id = int(referrer_st.chat_id)
        await self.store.delete_referral(referral_tag)
        referrer_st.video_round = 2
        referrer_st.referral_wait_started_at = 0.0
        bot = self._require_bot()
        try:
            await bot.send_message(
                referrer_id,
                "Друг подписался! Ставим второе видео в работу.",
            )
            if referrer_st.batch_audio_s3_url:
                referrer_st.stage = STAGE_PROCESSING
                batch_id = self._build_referral_batch_id(referrer_id)
                job_id = await self._enqueue_batch_version(
                    st=referrer_st,
                    audio_s3_url=referrer_st.batch_audio_s3_url,
                    version_index=1,
                    versions_total=1,
                    batch_id=batch_id,
                )
                referrer_st.active_job_ids = [job_id]
                referrer_st.job_order = [job_id]
                referrer_st.active_job_id = job_id
                referrer_st.batch_id = batch_id
                referrer_st.batch_total_versions = 1
                referrer_st.next_version_to_enqueue = 2
                referrer_st.master_job_id = job_id
                referrer_st.completed_job_ids = []
                referrer_st.active_job_started_at = time.time()
                referrer_st.last_status_msg_at = 0.0
                referrer_st.status_message_id = 0
                referrer_st.last_status_text = ""
                referrer_st.poll_attempts = 0
                referrer_st.last_job_stage = ""
                referrer_st.last_job_error = ""
            else:
                referrer_st.stage = STAGE_RATE_VIDEO_2
                await bot.send_message(
                    referrer_id,
                    "Как тебе ролик по 10-балльной шкале?",
                    reply_markup=_kb(BTN_RATE_BUTTONS),
                )
        except Exception as e:
            log.warning("referral_gen_failed referrer=%s err=%s", referrer_id, str(e))
            referrer_st.stage = STAGE_RATE_VIDEO_2
        await self.store.set(referrer_st)

    async def _handle_wait_referral_tag(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text.startswith("@") and len(text) > 1:
            tag = text.lower()

            own_username = (st.chat_username or "").strip().lower().lstrip("@")
            entered_username = tag.lstrip("@")
            if own_username and entered_username == own_username:
                await message.answer(
                    "Это твой собственный тег 😅 Укажи тег друга, которого хочешь пригласить."
                )
                return

            already_in_bot = await self._find_chat_by_username(entered_username)
            if already_in_bot:
                await message.answer(
                    "Этот пользователь уже есть в боте. Укажи тег друга, которого ещё нет."
                )
                return

            st.referral_tag = tag
            st.stage = STAGE_WAITING_REFERRAL
            st.referral_wait_started_at = time.time()
            await self.store.set(st)
            await self.store.set_referral(tag, st.chat_id)

            await self.credits_db.log_event(st.chat_id, "referral_sent", tag)
            await message.answer(
                f"Принял тег {text}. Как только он активирует бота — сразу пришлём тебе ролик!"
            )
        else:
            await message.answer(
                "Пришли тег друга, начинающийся с @, например: @impulsemanage"
            )

    # --- Waiting for referral friend to activate ---
    async def _handle_waiting_referral(self, message: Message, st: ChatState) -> None:
        await message.answer(
            f"Ждём, пока твой друг ({st.referral_tag}) активирует бота. "
            "Как только это произойдёт — сразу пришлём ролик!"
        )

    # --- Rating second video ---
    async def _handle_rate_video_2(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_RATE_LOW:
            st.stage = STAGE_FEEDBACK_LOW_2
            await self.store.set(st)
            await message.answer(
                "Приняли, готовы услышать обратную связь и вместе с ней исправить ролики "
                "— бесплатно.\n\n"
                "Давай, ты пройдешь 5 вопросов в форме (там меньше минуты), за счет них "
                "мы поймем, как улучшить ролики.\n\n"
                "Учтем твою обратку и пришлем последний ролик с исправлениями, так, чтобы "
                "тебе вкатило. Сделаем?",
                reply_markup=_kb([BTN_LETS_DO_IT]),
            )
        elif text in {BTN_RATE_MID_LOW, BTN_RATE_MID_HIGH, BTN_RATE_HIGH}:
            st.stage = STAGE_LAST_STEP_FORM
            await self.store.set(st)
            await message.answer(
                "Окей! Тогда остался последний шаг, чтобы получить все ролики.\n\n"
                "Тебе нужно всего лишь пройти форму обратной связи: ответить на 5 вопросов "
                "о контенте и менеджер пришлет тебе еще один ролик.\n\n"
                "Проще простого, не правда ли?",
                reply_markup=_kb([BTN_TO_FORM]),
            )
        else:
            await message.answer(
                "Выбери оценку из кнопок ниже.",
                reply_markup=_kb(BTN_RATE_BUTTONS),
            )

    # --- Feedback low second round ---
    async def _handle_feedback_low_2(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_LETS_DO_IT:
            await self._send_survey_link(message)
            st.stage = STAGE_IDLE
            await self.store.set(st)
        else:
            await message.answer("Нажми «Делаем!»", reply_markup=_kb([BTN_LETS_DO_IT]))

    # --- Last step form (5-6/7-8/9-10 second round) ---
    async def _handle_last_step_form(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_TO_FORM:
            await self._send_survey_link(message)
            st.stage = STAGE_POST_SURVEY
            await self.store.set(st)
        else:
            await message.answer("Нажми «В форму»", reply_markup=_kb([BTN_TO_FORM]))

    # --- Post survey ---
    async def _handle_post_survey(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SURVEY_DONE:
            await message.answer(
                "Спасибо за прохождение опроса! Лимит по роликам — кончился, надеемся, "
                "ты их обязательно выложишь и получишь первые результаты от контента.\n\n"
                "Чтобы не останавливаться, давай мы будем на связи и на будущий релиз "
                "сделаем еще один ролик, чтобы ты смог оценить его потенциал?",
                reply_markup=_kb([BTN_OF_COURSE]),
            )
        elif text == BTN_OF_COURSE:
            await self.credits_db.log_event(st.chat_id, "survey_done")
            await message.answer("Супер! Будем на связи.", reply_markup=ReplyKeyboardRemove())
            await self.credits_db.log_event(st.chat_id, "keep_in_touch")
            st.stage = STAGE_KEEP_IN_TOUCH
            st.reminder_at = time.time() + 2592000  # +30 days
            await self.store.set(st)
        else:
            await message.answer("Нажми «Прошёл»", reply_markup=_kb([BTN_SURVEY_DONE]))

    # --- Keep in touch (passive, handled by reminder loop) ---
    async def _handle_keep_in_touch(self, message: Message, st: ChatState) -> None:
        await message.answer("Мы на связи! Напишем тебе, когда придёт время.")

    # --- Remind release ---
    async def _handle_remind_release(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_PLANNING:
            st.video_round = 3
            st.stage = STAGE_WAIT_AUDIO
            await self.store.set(st)
            await self._move_to_wait_audio(st.chat_id, message)
        elif text == BTN_NOT_YET:
            await message.answer("Приняли!", reply_markup=ReplyKeyboardRemove())
            st.stage = STAGE_KEEP_IN_TOUCH
            st.reminder_at = time.time() + 2592000  # +30 days
            await self.store.set(st)
        else:
            await message.answer(
                "Привет! Не планируешь релиз?",
                reply_markup=_kb([BTN_PLANNING, BTN_NOT_YET]),
            )

    def _build_raw_audio_key(self, *, chat_id: int, file_name: str) -> str:
        safe = _safe_name(file_name)
        return f"{self.settings.s3_raw_audio_prefix.strip('/')}/{chat_id}/{_now_tag()}_{uuid.uuid4().hex[:10]}_{safe}"

    def _build_referral_batch_id(self, chat_id: int) -> str:
        return f"tg-{int(chat_id)}-referral-round-2"

    @staticmethod
    def _build_batch_idempotency_key(*, chat_id: int, batch_id: str, version_index: int) -> str:
        return f"tg-{int(chat_id)}-batch-{str(batch_id or '').strip()}-v{int(version_index)}"

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
        normalized_batch_id = str(batch_id or f"tg-{st.chat_id}").strip()
        idem = self._build_batch_idempotency_key(
            chat_id=int(st.chat_id),
            batch_id=normalized_batch_id,
            version_index=int(version_index),
        )
        user_clip_start_sec: float | None = None
        user_clip_end_sec: float | None = None
        start = float(st.user_clip_start_sec or 0.0)
        end = float(st.user_clip_end_sec or 0.0)
        if end > start >= 0.0:
            user_clip_start_sec = start
            user_clip_end_sec = end
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
            project_id=normalized_batch_id or None,
            reuse_text_job_id=str(reuse_text_job_id or "") or None,
            exclude_file_names=list(exclude_file_names or []),
            variant_index=int(version_index),
            variants_total=int(versions_total),
        )
        job_id = str(enqueue.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError(f"enqueue response has no job_id: {enqueue}")
        return job_id

    def _progress_interval_s(self) -> float:
        return max(1.0, float(self.settings.bot_status_update_interval_s))

    def _job_progress_fraction(self, *, status: str, stage: str) -> float:
        st = str(status or "").upper().strip()
        sg = str(stage or "").lower().strip()

        if st == "SUCCEEDED":
            return 1.0
        if st == "FAILED":
            return 1.0
        if st == "QUEUED":
            return 0.03

        # RUNNING stages (coarse finite pipeline weighting)
        if sg.startswith("build"):
            return 0.10
        if sg.startswith("llm_stage1a"):
            return 0.26
        if sg.startswith("llm_stage1b"):
            return 0.34
        if sg.startswith("llm_stage1"):
            return 0.22
        if "fragment_select" in sg:
            return 0.40
        if sg.startswith("llm_stage2_parallel"):
            return 0.55
        if sg.startswith("llm_stage2_subtitles"):
            return 0.62
        if sg.startswith("llm_stage2_style"):
            return 0.68
        if sg.startswith("llm_stage2_timing") or "switch" in sg:
            return 0.74
        if sg.startswith("stage3"):
            return 0.80
        if sg.startswith("dispatch"):
            return 0.88
        if sg.startswith("poll"):
            return 0.95
        if sg.startswith("render"):
            return 0.99
        return 0.15

    @staticmethod
    def _progress_bar(percent: int, width: int = 16) -> str:
        p = max(0, min(100, int(percent)))
        filled = int(round((p / 100.0) * width))
        return "[" + ("█" * filled) + ("░" * max(0, width - filled)) + "]"

    def _jobs_progress_message(
        self,
        *,
        rows: List[Dict[str, Any]],
        poll_attempts: int,
        total_versions: int,
    ) -> str:
        total = max(1, int(total_versions))
        sum_frac = 0.0
        for r in rows:
            sum_frac += self._job_progress_fraction(
                status=str(r.get("status") or ""),
                stage=str(r.get("stage") or ""),
            )

        frac = sum_frac / float(total)
        if rows and all(str(r.get("status") or "").upper() == "SUCCEEDED" for r in rows):
            frac = 1.0
        frac = max(0.0, min(1.0, frac))
        percent = int(round(frac * 100))

        return "\n".join([
            "Прогресс:",
            f"{self._progress_bar(percent)} {percent}%",
        ])

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

    def _reset_processing_state(self, st: ChatState, *, next_stage: str = STAGE_RATE_VIDEO) -> None:
        st.stage = str(next_stage)
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
        st.user_clip_start_sec = 0.0
        st.user_clip_end_sec = 0.0
        st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND

    async def _send_long_html_message(self, *, bot: Bot, chat_id: int, text: str) -> None:
        chunks = _split_telegram_chunks(text)
        for part in chunks:
            if not part:
                continue
            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)

    def _processing_timeout_s(self) -> float:
        return max(300.0, float(self.settings.bot_job_timeout_h) * 3600.0)

    def _referral_timeout_s(self) -> float:
        return max(300.0, float(self.settings.bot_referral_timeout_h) * 3600.0)

    def _recovery_interval_s(self) -> float:
        return max(15.0, float(self.settings.bot_recovery_poll_interval_s))

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

    def _is_processing_stuck(self, *, st: ChatState, now_ts: float) -> bool:
        if st.stage != STAGE_PROCESSING:
            return False
        has_jobs = bool(st.active_job_ids) or bool(st.active_job_id)
        if not has_jobs:
            return True
        started_at = float(st.active_job_started_at or 0.0)
        if started_at <= 0:
            started_at = float(st.last_status_msg_at or 0.0)
        if started_at <= 0:
            return False
        return (now_ts - started_at) >= self._processing_timeout_s()

    def _is_waiting_referral_stuck(self, *, st: ChatState, now_ts: float) -> bool:
        if st.stage != STAGE_WAITING_REFERRAL:
            return False
        started_at = float(st.referral_wait_started_at or 0.0)
        if started_at <= 0:
            return False
        return (now_ts - started_at) >= self._referral_timeout_s()

    async def _recover_processing_timeout(self, st: ChatState) -> None:
        await self.store.reset_to_wait_audio(st.chat_id)
        await self.credits_db.log_event(st.chat_id, "processing_timeout_recovered")
        try:
            bot = self._require_bot()
            await bot.send_message(
                st.chat_id,
                "Не дождались результата генерации в ожидаемое время. "
                "Вернул тебя в стартовое состояние, отправь трек заново.",
                reply_markup=_kb([BTN_SEND_TRACK]),
            )
        except Exception as e:
            log.warning("processing_timeout_notify_failed chat=%s err=%s", st.chat_id, str(e))

    async def _recover_referral_timeout(self, st: ChatState) -> None:
        if st.referral_tag:
            await self.store.delete_referral(st.referral_tag)
        st.stage = STAGE_REFERRAL_ASK
        st.referral_wait_started_at = 0.0
        await self.store.set(st)
        await self.credits_db.log_event(st.chat_id, "referral_timeout_recovered")
        try:
            bot = self._require_bot()
            await bot.send_message(
                st.chat_id,
                "Не дождались активации по рефералу. Можно отправить другой тег друга "
                "или продолжить без этого шага.",
                reply_markup=_kb([BTN_SEND_NOW], [BTN_NEED_SEARCH], [BTN_NO_FRIENDS]),
            )
        except Exception as e:
            log.warning("referral_timeout_notify_failed chat=%s err=%s", st.chat_id, str(e))

    async def _recovery_loop(self) -> None:
        while True:
            try:
                now = time.time()
                waiting_states = await self.store.list_waiting_referral()
                for st in waiting_states:
                    try:
                        if self._is_waiting_referral_stuck(st=st, now_ts=now):
                            await self._recover_referral_timeout(st)
                    except Exception as e:
                        log.warning("recovery_loop_waiting_referral chat=%s err=%r", st.chat_id, e)

                processing_states = await self.store.list_processing_candidates()
                for st in processing_states:
                    try:
                        if self._is_processing_stuck(st=st, now_ts=now):
                            await self._recover_processing_timeout(st)
                    except Exception as e:
                        log.warning("recovery_loop_processing chat=%s err=%r", st.chat_id, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("recovery_loop_iteration_error err=%r", e)
            await asyncio.sleep(self._recovery_interval_s())

    async def _state_cleanup_loop(self) -> None:
        while True:
            try:
                now = time.time()
                cutoff = now - self._state_ttl_s()
                batch_size = max(1, int(self.settings.tg_state_cleanup_batch_size))
                stale_ids = await self.store.list_stale_chat_ids(cutoff, limit=batch_size)
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
        while True:
            try:
                states = await self.store.list_processing()
                for st in states:
                    try:
                        await self._process_chat_job(st)
                    except Exception as e:
                        log.warning("processing loop chat=%s err=%r", st.chat_id, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("processing loop iteration error=%r", e)

            await asyncio.sleep(max(1.0, float(self.settings.bot_poll_interval_s)))

    async def _reminder_loop(self) -> None:
        """Check every hour for users whose 30-day reminder is due."""
        while True:
            try:
                now = time.time()
                pending = await self.store.list_pending_reminders(now)
                bot = self._require_bot()
                for st in pending:
                    try:
                        st.stage = STAGE_REMIND_RELEASE
                        st.reminder_at = 0.0
                        await self.store.set(st)
                        await bot.send_message(
                            st.chat_id,
                            "Привет! Не планируешь релиз?",
                            reply_markup=_kb([BTN_PLANNING, BTN_NOT_YET]),
                        )
                        await self.credits_db.log_event(st.chat_id, "reminder_sent")
                    except Exception as e:
                        log.warning("reminder send chat=%s err=%r", st.chat_id, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("reminder loop error=%r", e)
            await asyncio.sleep(3600)  # check every hour

    async def _payment_poll_loop(self) -> None:
        """Poll T-Bank every 30s for pending payments, credit on CONFIRMED."""
        while True:
            try:
                if self.tbank:
                    pending = await self.credits_db.get_pending_payments()
                    bot = self._require_bot()
                    for pay in pending:
                        try:
                            order_id = pay["order_id"]
                            state_resp = await self._tbank_check_order(order_id)
                            if not state_resp:
                                continue
                            status = state_resp.get("Status", "")
                            payment_id = str(state_resp.get("PaymentId", ""))
                            if status == "CONFIRMED":
                                pkg = pay["package"]
                                tg_id = pay["tg_id"]
                                credits_to_add = self._PKG_CREDITS.get(pkg, 5)
                                await self.credits_db.update_payment_status(order_id, "confirmed", payment_id)
                                await self.credits_db.add_credits(tg_id, credits_to_add, "payment", f"Пакет «{pkg}»")
                                await self.credits_db.log_event(tg_id, "payment_confirmed", f"{pkg} +{credits_to_add} кредитов")
                                username = ""
                                try:
                                    user_data = await self.credits_db.get_user(tg_id)
                                    username = user_data.get("username", "") if user_data else ""
                                except Exception:
                                    pass
                                bal = await self.credits_db.get_balance(tg_id)
                                try:
                                    await bot.send_message(
                                        tg_id,
                                        f"Оплата прошла успешно! Пакет «{pkg}» активирован.\n\n"
                                        f"Начислено кредитов: {credits_to_add}\n"
                                        f"Баланс: {bal}\n\n"
                                        "Отправь трек, чтобы начать генерацию.",
                                        reply_markup=_kb(["Отправить трек"]),
                                    )
                                except Exception as e:
                                    log.warning("payment notify user=%s err=%s", tg_id, e)
                                uname = f"@{username}" if username else str(tg_id)
                                await self._notify_manager_payment(uname, pkg, pay["amount_rub"], "Оплачен")
                                await self._notify_finance_bot_income(pay["amount_rub"], uname, pkg)
                                try:
                                    st = await self.store.get(tg_id)
                                    if st:
                                        st.stage = STAGE_WAIT_AUDIO
                                        await self.store.set(st)
                                except Exception as e:
                                    log.warning("payment state update err=%s", e)
                                log.info("payment confirmed order=%s tg_id=%s pkg=%s credits=%s", order_id, tg_id, pkg, credits_to_add)
                            elif status in ("REJECTED", "DEADLINE_EXPIRED", "CANCELED", "REVERSED"):
                                await self.credits_db.update_payment_status(order_id, status.lower(), payment_id)
                                username = ""
                                try:
                                    user_data = await self.credits_db.get_user(pay["tg_id"])
                                    username = user_data.get("username", "") if user_data else ""
                                except Exception:
                                    pass
                                uname = f"@{username}" if username else str(pay["tg_id"])
                                status_label = {"REJECTED": "Отклонён", "DEADLINE_EXPIRED": "Истёк", "CANCELED": "Отменён", "REVERSED": "Возврат"}.get(status, status)
                                await self._notify_manager_payment(uname, pay["package"], pay["amount_rub"], status_label)
                                log.info("payment %s order=%s", status, order_id)
                        except Exception as e:
                            log.warning("payment poll order=%s err=%r", pay.get("order_id"), e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("payment poll loop error=%r", e)
            await asyncio.sleep(30)

    async def _tbank_check_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Check order status via T-Bank CheckOrder API."""
        if not self.tbank:
            return None
        params: Dict[str, Any] = {
            "TerminalKey": self.tbank._terminal_key,
            "OrderId": order_id,
        }
        params["Token"] = self.tbank._make_token(params)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post("https://securepay.tinkoff.ru/v2/CheckOrder", json=params)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                if not data.get("Success"):
                    return None
                payments = data.get("Payments", [])
                if not payments:
                    return None
                return payments[-1]
        except Exception as e:
            log.warning("tbank check_order err=%r", e)
            return None

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
            log.warning(
                "finalize_one_job_failed chat=%s job=%s stage=%s err=%s",
                st.chat_id,
                job_id,
                stage,
                _compact_text(error_text, limit=220),
            )
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
            await self._notify_manager_generation_error(
                username=st.chat_username.lstrip("@") if st.chat_username else "",
                chat_id=st.chat_id,
                job_id=job_id,
                stage=stage,
                error_text=error_text,
            )
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

        file_sent = False
        send_file_error = ""
        try:
            await self._download_result_video(source=source, dest=video_path)
            await bot.send_document(
                chat_id=st.chat_id,
                document=FSInputFile(str(video_path)),
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
            if video_path.exists():
                video_path.unlink()
        except Exception:
            pass

    async def _process_chat_job(self, st: ChatState) -> None:
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

        st.completed_job_ids = [jid for jid in job_ids if jid in completed]
        failed_rows = [r for r in rows if str(r.get("status") or "").upper() == "FAILED"]
        if failed_rows:
            failed = failed_rows[0]
            failed_job_id = str(failed.get("job_id") or "")
            failed_stage = str(failed.get("stage") or "")
            failed_error = str(failed.get("error") or "")
            succeeded_versions = sum(1 for r in rows if str(r.get("status") or "").upper() == "SUCCEEDED")
            refund_versions = max(0, int(total_versions) - int(succeeded_versions))
            if refund_versions > 0:
                try:
                    await self.credits_db.add_credits(
                        st.chat_id,
                        int(refund_versions),
                        "generation_failed_refund",
                        admin_note=f"batch={st.batch_id or '-'} job={failed_job_id or '-'}",
                    )
                except Exception as e:
                    log.warning("generation_failed_refund_add_credits_failed chat=%s err=%s", st.chat_id, str(e))
            await self.credits_db.log_event(
                st.chat_id,
                "generation_failed",
                f"job={failed_job_id or '-'} stage={failed_stage or '-'}",
            )
            await self._notify_manager_generation_failure(
                st=st,
                job_id=failed_job_id,
                stage=failed_stage,
                error_text=failed_error,
                succeeded_versions=int(succeeded_versions),
                total_versions=int(total_versions),
                refunded_versions=int(refund_versions),
            )
            await bot.send_message(
                st.chat_id,
                _GENERATION_FAILED_USER_TEXT,
                reply_markup=_kb([BTN_SEND_TRACK]),
            )
            self._reset_processing_state(st, next_stage=STAGE_WAIT_AUDIO)
            await self.store.set(st)
            return

        all_done_enqueued = len(st.completed_job_ids) >= len(job_ids)
        if not all_done_enqueued:
            await self.store.set(st)
            return

        master_status = ""
        if st.master_job_id:
            for r in rows:
                if str(r.get("job_id") or "") == str(st.master_job_id):
                    master_status = str(r.get("status") or "").upper()
                    break

        next_ver = max(1, int(st.next_version_to_enqueue or 1))
        can_enqueue_more = next_ver <= total_versions
        if can_enqueue_more:
            if master_status == "FAILED":
                await bot.send_message(
                    st.chat_id,
                    f"Версия 1/{total_versions} завершилась ошибкой, остальные версии не запускаю.",
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
                    st.active_job_started_at = time.time()
                    st.next_version_to_enqueue = next_ver + 1
                    await bot.send_message(
                        st.chat_id,
                        f"Версия {next_ver}/{total_versions}: поставил в очередь (exclude={len(st.used_footage_file_names or [])}).",
                    )
                    await self.store.set(st)
                    return
                except Exception as e:
                    err_text = str(e)
                    succeeded_versions = sum(1 for r in rows if str(r.get("status") or "").upper() == "SUCCEEDED")
                    refund_versions = max(0, int(total_versions) - int(succeeded_versions))
                    if refund_versions > 0:
                        try:
                            await self.credits_db.add_credits(
                                st.chat_id,
                                int(refund_versions),
                                "generation_failed_refund",
                                admin_note=f"batch={st.batch_id or '-'} job=enqueue_next_version_failed",
                            )
                        except Exception as add_e:
                            log.warning("enqueue_next_refund_failed chat=%s err=%s", st.chat_id, str(add_e))
                    await self.credits_db.log_event(
                        st.chat_id,
                        "generation_failed",
                        f"job=enqueue_next stage=enqueue_next_version error={_compact_text(err_text, limit=140)}",
                    )
                    await self._notify_manager_generation_failure(
                        st=st,
                        job_id="enqueue_next_version",
                        stage="enqueue_next_version",
                        error_text=err_text,
                        succeeded_versions=int(succeeded_versions),
                        total_versions=int(total_versions),
                        refunded_versions=int(refund_versions),
                    )
                    await bot.send_message(
                        st.chat_id,
                        _GENERATION_FAILED_USER_TEXT,
                        reply_markup=_kb([BTN_SEND_TRACK]),
                    )
                    self._reset_processing_state(st, next_stage=STAGE_WAIT_AUDIO)
                    await self.store.set(st)
                    return

        self._reset_processing_state(st)  # sets stage = RATE_VIDEO

        await self.credits_db.log_event(st.chat_id, "generation_done")

        # Credits already deducted at launch time
        bal = await self.credits_db.get_balance(st.chat_id)
        log.info("generation_complete chat=%s remaining=%s", st.chat_id, bal)

        bal = await self.credits_db.get_balance(st.chat_id)
        paid = await self.credits_db.has_paid(st.chat_id)

        # Paid users: no rating/funnel, just loop back to generation
        if paid:
            if bal > 0:
                await bot.send_message(
                    st.chat_id,
                    f"Готово — лови контент! Давай сделаем ещё:\n\n"
                    f"Остаток генераций: {bal}\n"
                    f"/packets — посмотреть тарифы",
                    reply_markup=_kb([BTN_GENERATE_MORE]),
                )
                st.stage = STAGE_WAIT_AUDIO
            else:
                await bot.send_message(
                    st.chat_id,
                    "Готово — лови контент!\n\n"
                    "Твои кредиты закончились. Хочешь посмотреть тарифы?",
                    reply_markup=_kb([BTN_ALL_PACKAGES]),
                )
                st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            return

        # Free users: rating + post-generation funnel
        bal_suffix = f"\n\nОстаток генераций: {bal}" if bal is not None else ""

        if st.video_round == 2:
            rating_text = f"Видим подписку друга! Лови второй ролик, как тебе по 10-балльной шкале?{bal_suffix}"
            st.stage = STAGE_RATE_VIDEO_2
        elif st.video_round >= 3:
            rating_text = f"Готово — лови ролик! Скажи, пожалуйста, как оцениваешь по 10-балльной шкале?{bal_suffix}"
            st.stage = STAGE_RATE_VIDEO_2
        else:
            rating_text = f"Готово — лови первый ролик! Скажи, пожалуйста, как оцениваешь по 10-балльной шкале?{bal_suffix}"
            st.stage = STAGE_RATE_VIDEO

        await bot.send_message(
            st.chat_id,
            rating_text,
            reply_markup=_kb(BTN_RATE_BUTTONS),
        )
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

        encoded_path = quote(path, safe="/")
        url = f"https://api.telegram.org/file/bot{self.settings.tg_bot_token}/{encoded_path}"

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
        bot = Bot(token=self.settings.tg_bot_token)
        await self.dp.start_polling(bot)


def main() -> None:
    app = BlastBotApp(SETTINGS)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
