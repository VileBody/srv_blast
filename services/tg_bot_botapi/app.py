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
from core.clip_window import CLIP_WINDOW_RANGE_S_LABEL
from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    normalize_subtitles_mode,
)

from .audio_prepare import AudioPrepareResult, prepare_audio_best_effort
from .config import SETTINGS, Settings
from .orchestrator_client import OrchestratorClient
from .s3_client import S3Client, make_s3_url
from .state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_IDLE,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_FRAGMENT_CHOICE,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
    STAGE_WAIT_NEXT,
    STAGE_WAIT_SUBTITLES_MODE,
    STAGE_WAIT_VERSIONS,
)


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
VERSION_BUTTONS = [BTN_VER_1, BTN_VER_2, BTN_VER_3, BTN_VER_4, BTN_VER_5]
SUBTITLES_MODE_BUTTONS = [
    BTN_SUB_MODE_LEGACY,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
]
_SUBTITLES_MODE_BY_BUTTON = {
    BTN_SUB_MODE_LEGACY: SUBTITLES_MODE_LEGACY_BLOCKS,
    BTN_SUB_MODE_IMPULSE: SUBTITLES_MODE_IMPULSE_2ND,
    BTN_SUB_MODE_SCENES: SUBTITLES_MODE_SCENES_3RD,
}
_CONTROL_BUTTONS = {
    BTN_SEND_TRACK,
    BTN_SEND_LYRICS,
    BTN_SKIP_LYRICS,
    BTN_SEND_FRAGMENT,
    BTN_SKIP_FRAGMENT,
    BTN_SUB_MODE_LEGACY,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_LAUNCH,
    BTN_NEXT,
    *VERSION_BUTTONS,
}


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
_RE_CELERY_RETRIES = re.compile(r"\bretries=(\d+)\b")


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


def _logs_dir_candidates_for_job(job_id: str) -> List[Path]:
    jid = str(job_id or "").strip()
    if not jid:
        return []

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
        out.append(root / jid / "out" / "logs")
    return out


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
    if mode in {SUBTITLES_MODE_IMPULSE_2ND, SUBTITLES_MODE_SCENES_3RD}:
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
    if mode == SUBTITLES_MODE_SCENES_3RD:
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
        self.store = RedisChatStateStore(settings)
        self.s3 = S3Client(settings)
        self.orchestrator = OrchestratorClient(base_url=settings.orchestrator_public_url, timeout_s=60.0)

        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)

        self._processing_task: asyncio.Task[None] | None = None
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

        self._processing_task = asyncio.create_task(self._processing_loop(), name="tg_bot_processing_loop")
        log.info("startup complete: polling loop started")

    async def _on_shutdown(self, bot: Bot) -> None:
        del bot
        if self._processing_task is not None:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        await self.orchestrator.close()
        await self.store.close()
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
            tg_file = await message.bot.get_file(file_id)
            with open(src_path, "wb") as f:
                await message.bot.download_file(tg_file.file_path, destination=f)

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
            await self._ask_subtitles_mode(message, st)
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
        await self._ask_subtitles_mode(message, st)

    async def _handle_wait_subtitles_mode(self, message: Message, st: ChatState) -> None:
        mode = _parse_subtitles_mode_choice(message.text or "")
        if mode is None:
            await message.answer("Выбери режим кнопкой: «Обычные blocks», «Impulse 2nd» или «Scenes 3rd».")
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
        try:
            versions = max(1, min(5, int(st.versions_count or 1)))
            await message.answer(f"Заливаю аудио в S3 и ставлю задачи в очередь… (версий: {versions})")
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
        idem = f"tg-{st.chat_id}-batch-{batch_id}-v{int(version_index)}-{uuid.uuid4().hex[:12]}"
        enqueue = await self.orchestrator.send_audio_s3(
            audio_s3_url=audio_s3_url,
            mode="with_gemini",
            lyrics_text=st.lyrics_text,
            target_fragment=st.target_fragment,
            subtitles_mode=st.subtitles_mode,
            idempotency_key=idem,
            project_id=batch_id or None,
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

    def _jobs_progress_message(
        self,
        *,
        rows: List[Dict[str, Any]],
        poll_attempts: int,
        total_versions: int,
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
        st.subtitles_mode = SUBTITLES_MODE_LEGACY_BLOCKS

    async def _send_long_html_message(self, *, bot: Bot, chat_id: int, text: str) -> None:
        chunks = _split_telegram_chunks(text)
        for part in chunks:
            if not part:
                continue
            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)

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
                    st.next_version_to_enqueue = next_ver + 1
                    await bot.send_message(
                        st.chat_id,
                        f"Версия {next_ver}/{total_versions}: поставил в очередь (exclude={len(st.used_footage_file_names or [])}).",
                    )
                    await self.store.set(st)
                    return
                except Exception as e:
                    await bot.send_message(
                        st.chat_id,
                        f"Не удалось поставить в очередь Версию {next_ver}/{total_versions}: {e}",
                    )
                    st.next_version_to_enqueue = total_versions + 1

        await bot.send_message(
            st.chat_id,
            "Сделать следующий?",
            reply_markup=_kb([BTN_NEXT]),
        )

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
