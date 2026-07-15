from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus

import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, ChatMemberUpdated, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from core.telegram_api import build_aiogram_session, make_telegram_api
from core.clip_window import CLIP_WINDOW_RANGE_S_LABEL
from core.filesystem_hygiene import cleanup_jobs_artifacts, cleanup_tmp_chat_dirs
from core.queue_estimate import format_queue_estimate_lines, pick_queue_estimate_job_id
from core.hook_intros import HOOK_CATEGORY_ORDER, hook_intro
from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    SUBTITLES_MODE_TRENDY_5TH,
    SUBTITLES_MODE_BRAT_5TH,
    normalize_subtitles_mode,
)
from config.styles.artist_presets_loader import get_artists, get_genres
from config.styles.theme_groups import (
    get_artist_rotation_slots,
    get_rotation_slot,
    get_theme_groups,
)

from .admin_commands import make_admin_router
from .admin_panel import start_admin_panel
from .broadcast_sender import start_broadcast_workers
from .audio_prepare import AudioPrepareResult, prepare_audio_best_effort
from .config import SETTINGS, Settings
from .credits_db import CreditsDB
from .tbank_client import TBankClient
from .warmup_chain import CALLBACK_PREFIX as WARMUP_CALLBACK_PREFIX, CAMPAIGN as WARMUP_CAMPAIGN, keyboard_for_next as warmup_keyboard, message_for_stage as warmup_message
from .orchestrator_client import OrchestratorClient
from .s3_client import S3Client, make_s3_url
from services.generation_runtime import GenerationRuntimeStore
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
    STAGE_WAIT_BG_COLOR,
    STAGE_WAIT_STROBE_CUT,
    STAGE_WAIT_BG_MODE,
    STAGE_WAIT_BG_INFO,
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
    STAGE_PURCHASE_CHOICE,
    STAGE_SUBSCRIPTION_CONFIRM,
    STAGE_WAIT_PAYMENT,
    STAGE_IMPROVEMENT_FEEDBACK,
    STAGE_IMPROVEMENT_OTHER_TEXT,
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
    # Season flow (Hooks S1) — gated by SEASON_FLOW_ENABLED below.
    SEASON_STAGES,
    STAGE_SEASON_INTRO_1,
    STAGE_SEASON_INTRO_2,
    STAGE_SEASON_CONSENT,
    STAGE_SEASON_MENU,
    # Hook flow (Phase A-UX) — gated by HOOK_FLOW_ENABLED below.
    STAGE_WAIT_HOOK_CHOICE,
    STAGE_WAIT_HOOK_DROP,
    STAGE_WAIT_HOOK_DROP_MANUAL,
    STAGE_WAIT_HOOK_TYPE,
    STAGE_WAIT_HOOK_DEVICE,
    STAGE_WAIT_EFFECT_HOOK,
    STAGE_WAIT_EFFECT_TRANSITION,
    STAGE_WAIT_EFFECT_EXTRA,
    STAGE_WAIT_EFFECT_EXTRA_FULL,
    STAGE_WAIT_EFFECT_EXTEND,
    STAGE_WAIT_VISUAL_TRANSITION,
    STAGE_WAIT_VISUAL_STYLE,
    STAGE_WAIT_F2_SHAPE,
    STAGE_WAIT_F1_SOUND,
    STAGE_WAIT_F1_TEXT,
    STAGE_WAIT_PHOTO_STYLE,
    STAGE_WAIT_PHOTO_TRANSITION,
    STAGE_WAIT_VIBE,
    STAGE_WAIT_SUBTITLE_COLOR,
    STAGE_WAIT_ACCENT_COLOR,
)


# Season flow kill-switch. Shared with tg_bot_botapi via the same env var so
# both bots flip together: when SEASON_FLOW_ENABLED is off (default), neither
# bot enters the season onboarding — generation works end-to-end as before.
SEASON_FLOW_ENABLED = (os.environ.get("SEASON_FLOW_ENABLED", "0").strip().lower()
                       in {"1", "true", "yes", "on", "enabled"})

# Hook flow toggle. Same pattern as SEASON_FLOW_ENABLED — the public bot
# carries the mirrored state machine and the orchestrator-client wiring
# (hook_enabled / user_drop_t kwargs land in the payload regardless), but the
# 4-stage hook picker is NOT exposed in the user flow until tg_bot_botapi
# validates the UX on real test users. Flip to "1" in env to wire it on.
HOOK_FLOW_ENABLED = (os.environ.get("HOOK_FLOW_ENABLED", "0").strip().lower()
                     in {"1", "true", "yes", "on", "enabled"})

# Footage precision flow (Phase 2b) toggle. The ranked-shortlist vibe multi-select
# UX is now ported 1:1 from tg_bot_botapi (genre/artist → vibe reroute, paged
# inline picker, enqueue bucket distribution, auto-cursor removal). Default ON in
# both bots; overridable via FOOTAGE_VIBE_FLOW_ENABLED to fall back to the legacy
# genre/artist picker.
FOOTAGE_VIBE_FLOW_ENABLED = (os.environ.get("FOOTAGE_VIBE_FLOW_ENABLED", "1").strip().lower()
                             in {"1", "true", "yes", "on", "enabled"})

# Photo flow (4:3) toggle. The "Картинки" background routes to bg_mode="photo":
# the vibe shortlist is reused (PHOTO pool), then two F3-style picker steps
# (photo_style → photo_transition) run before the version count, and the render
# uses the 1920×1440 photo template. UX ported 1:1 from tg_bot_botapi but gated
# OFF here until the team bot validates it; state/client/stages mirror regardless
# for CI parity. Overridable via PHOTO_FLOW_ENABLED.
PHOTO_FLOW_ENABLED = (os.environ.get("PHOTO_FLOW_ENABLED", "0").strip().lower()
                      in {"1", "true", "yes", "on", "enabled"})

# /bigtest is a team-bot-only command. Constant is False here so the handler
# (registered below for parity) immediately rejects the request in production.
# Parity note: the LLM-reuse roll-forward logic (promoting bigtest_master_job_id
# to the last completed case after every step) lives entirely in tg_bot_botapi.
# Parity note 2: reuse_stage2_footage, stage2_selection_seed_override and
# bigtest_footage_seed fields exist in state_store/schemas for schema parity;
# they are never set to non-default values here (BIGTEST_ENABLED=False).
# Parity note 3: last_subtitles_mode (ChatState) is pinned at /bigtest start in
# tg_bot_botapi so every case reuses the source job's subtitles_mode (otherwise
# the LLM cache is invalidated). It exists here for state parity only and is
# never read in the public flow. The /bigtest F2 «Объект» cases live in
# tg_bot_botapi's _BIGTEST_CASES / _apply_bigtest_config exclusively.
# Parity note 4: the /bigtest reuse safety-breaker (Layer 1 precondition
# _bigtest_precheck_reuse_source + Layer 2 runtime abort _bigtest_emergency_stop
# on reuse_stage1_miss) lives entirely in tg_bot_botapi. The shared
# OrchestratorClient.kill_job helper is mirrored below for parity but unused here.
# Parity note 5: /bigtest validates the reuse-source (incl. case-0) via
# _bigtest_precheck_reuse_source, refuses to start on an invalid/FAILED source,
# pins subtitles_mode to the source's real resume_state.stage2_subtitles_mode,
# and reuses footage style for every case (incl. case-0) so the stage2 LLM runs
# at most once. All team-bot-only; not used here.
# Parity note 6: `/bigtest resume` continues an interrupted run from the saved
# resume point (bigtest_resume_index / bigtest_resume_source_job, recorded after
# each successful case; the failed job is never promoted as source). team-only.
# Parity note 7: the bigtest enqueue loop never dies silently — halt/skip
# messages are plain text (no HTML to break on error reprs), the source precheck
# retries get_job on transient hiccups, and an outer guard surfaces any uncaught
# error with a resume hint. team-only.
# Parity note 8: bigtest skip/guard messages call _compact_text(s, limit=N)
# (keyword — limit is keyword-only); `/bigtest resume` restores the clip window
# (user_clip_*) + drop from the source job's request so reframe/F4/F5/F2 cases
# don't enqueue with user_clip_end_sec=0 (orchestrator 422). team-only.
BIGTEST_ENABLED: bool = False
# Hook battery is team-bot only; mirrored here as False (button never shown).
# Its case-building (incl. per-format drop selection — F4 needs drop >= lead,
# so it walks the next drop candidates) lives only in tg_bot_botapi.
BATTERY_ENABLED: bool = False

# F5 «Мысль» clip-reframe lead (seconds): clip_start := drop − F5_LEAD_SEC so the
# TTS voice runs up INTO the drop. Mirrors tg_bot_botapi for parity.
F5_LEAD_SEC: float = 4.0

# Minimum clip length a hook reframe must leave (mirrors tg_bot_botapi; the team
# battery uses it to bound drop auto-selection). Keep ≥ STAGE2_FAST_START_SECONDS.
MIN_REFRAME_CLIP_SEC: float = 7.0

# F4 minimum visible intro length (mirrors tg_bot_botapi). Battery auto-picks an
# F4 drop only with intro ≥ this; else asks for a manual F4 drop.
F4_MIN_INTRO_SEC: float = 3.0

HOOK_STAGES = frozenset({
    STAGE_WAIT_HOOK_CHOICE,
    STAGE_WAIT_HOOK_DROP,
    STAGE_WAIT_HOOK_DROP_MANUAL,
    STAGE_WAIT_HOOK_TYPE,
    STAGE_WAIT_HOOK_DEVICE,
    STAGE_WAIT_EFFECT_HOOK,
    STAGE_WAIT_EFFECT_TRANSITION,
    STAGE_WAIT_EFFECT_EXTRA,
    STAGE_WAIT_EFFECT_EXTRA_FULL,
    STAGE_WAIT_EFFECT_EXTEND,
    STAGE_WAIT_VISUAL_TRANSITION,
    STAGE_WAIT_VISUAL_STYLE,
    STAGE_WAIT_F2_SHAPE,
    STAGE_WAIT_F1_SOUND,
    STAGE_WAIT_F1_TEXT,
})

# Footage precision flow (Phase 2b): stage(s) carrying the vibe multi-select.
# Mirror of tg_bot_botapi; routing is gated behind FOOTAGE_VIBE_FLOW_ENABLED.
VIBE_STAGES = frozenset({
    STAGE_WAIT_VIBE,
})

# Photo flow (4:3): stage(s) carrying the two F3-style photo picker steps.
# Mirror of tg_bot_botapi; routing is gated behind PHOTO_FLOW_ENABLED.
PHOTO_STAGES = frozenset({
    STAGE_WAIT_PHOTO_STYLE,
    STAGE_WAIT_PHOTO_TRANSITION,
})

# F3 «Эффект» visual-FX ids (mirror of mlcore/hooks/f3_effect + tg_bot_botapi).
# The 3-step picker UX lives in tg_bot_botapi; the public bot mirrors the id sets
# + RU-label maps + the orchestrator-client effect_* kwargs (which land in the
# payload regardless of HOOK_FLOW_ENABLED). At least one of hook/transition/extra
# is required downstream; effect_hook_extend applies only to flash_slow_shutter.
F3_HOOK_IDS = frozenset({"hook_light", "shutter_effect", "flash_slow_shutter", "negative_zoom"})
F3_TRANSITION_IDS = frozenset({
    "snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts", "layer_shake",
})
F3_EXTRA_IDS = frozenset({
    "xerox", "analog_glitch", "neon_extract", "old_camera",
    "blackwhite", "crystal_glow", "night_vision", "wave",
})
F3_HOOK_LABELS_RU = {"Молния": "hook_light", "Затвор": "shutter_effect", "Слоу-шаттер": "flash_slow_shutter", "Негатив-зум": "negative_zoom"}
F3_TRANSITION_LABELS_RU = {
    "Снап-вайп": "snap_wipe", "Минимакс": "minimax", "Инверт": "invert_flash",
    "Экстракт": "extract_flash", "Вспышки": "flash_on_cuts", "Тряска": "layer_shake",
}
F3_EXTRA_LABELS_RU = {
    "Ксерокс": "xerox", "Аналог-глитч": "analog_glitch", "Неон": "neon_extract",
    "Старая камера": "old_camera",
    "Ч/Б": "blackwhite", "Crystal Glow": "crystal_glow",
    "Night Vision": "night_vision", "Wave": "wave",
}
F3_EXTEND_LABELS_RU = {"Стандарт": "", "До конца ролика": "to_end", "3 футажа после": "after_drop:3"}

# F4 «Движение» motion-hook devices. The picker UX lives in tg_bot_botapi; the
# public bot mirrors the id set + RU-label map + the orchestrator-client
# `f4_device` kwarg (which lands in the payload regardless of HOOK_FLOW_ENABLED,
# so a chat state pre-populated with a motion device propagates cleanly). All
# five devices are wired downstream (mlcore/hooks/f4_motion).
# NOTE (mirrors tg_bot_botapi): focus-clip hook analysis is delegated to the
# orchestrator via OrchestratorClient.analyze_hook — librosa lives in the
# runtime image, NOT in the slim bot image. The bots must never import librosa.
#
# NOTE (mirrors tg_bot_botapi): the hook-choice step offers a "🔄 Обновить
# тайминг" button while the background drop analysis is pending/failed, so the
# user can re-check instead of assuming it failed. That UX is team-bot only
# (the public hook flow is gated behind HOOK_FLOW_ENABLED).
F4_MOTION_DEVICE_IDS = frozenset({"swipe", "tap", "pinch", "holdfinger", "head"})
F4_MOTION_DEVICE_LABELS_RU = {
    "Свайп": "swipe",
    "Тап": "tap",
    "Зум": "pinch",
    "Задержи палец": "holdfinger",
    "Качай головой": "head",
}
# F2 «Объект» packaged-combo shape ids (mirror of mlcore/hooks/f2_object +
# tg_bot_botapi). The single-step picker UX lives in tg_bot_botapi; the public
# bot mirrors the id set + RU-label map + the orchestrator-client `f2_shape`
# kwarg (which lands in the payload regardless of HOOK_FLOW_ENABLED, so a chat
# state pre-populated with a shape propagates cleanly). All five shapes are
# wired downstream (mlcore/hooks/f2_object/shapes/).
F2_SHAPE_IDS = frozenset({"rhomb", "square", "star1", "star2", "elipse"})
F2_SHAPE_LABELS_RU = {
    "Ромб": "rhomb",
    "Квадрат": "square",
    "Звезда-10": "star1",
    "Звезда-5": "star2",
    "Эллипс": "elipse",
}
# Reference BPM the F4 device keyframes were authored under. Mirrored for parity
# (the public picker UX is gated behind HOOK_FLOW_ENABLED).
#
# Overlay-to-drop alignment (mirrors tg_bot_botapi): for a motion hook the bot
# reframes the clip window to clip_start = drop - lead_eff
# (lead_eff = LEAD[device] * F4_REF_BPM / bpm), so the overlay's cover-layer end
# lands exactly on the drop. For a motion hook the rolled clip IS [drop-lead, end]
# and Stage1 subtitles align to that same window.
F4_REF_BPM = 128.0


def _should_route_to_season(st: ChatState) -> bool:
    """Return True iff this chat should land in the season flow on /start.

    Gated by SEASON_FLOW_ENABLED (kill-switch). When off — always False so
    the standard onboarding runs and generation stays reachable. When on,
    an existing season stage or completed intro keeps the user in season.
    """
    if not SEASON_FLOW_ENABLED:
        return False
    if st.stage in SEASON_STAGES:
        return True
    return bool(getattr(st, "season_intro_completed", False))


def _should_route_to_hook_flow(st: ChatState) -> bool:
    """Return True iff this chat should enter the hook picker step.

    Gated by HOOK_FLOW_ENABLED. When the flag is off, public bot behavior is
    unchanged regardless of mirrored hook_* state on the chat. When on, an
    existing hook stage on the chat state is enough to route — useful for
    resuming a session that was rolled into the hook picker mid-flight.
    """
    if not HOOK_FLOW_ENABLED:
        return False
    return st.stage in HOOK_STAGES


def _should_route_to_vibe_flow(st: ChatState) -> bool:
    """Return True iff this chat should enter the footage vibe picker step.

    Gated by FOOTAGE_VIBE_FLOW_ENABLED. When the flag is off, behavior falls back
    to the legacy genre/artist picker regardless of mirrored vibe_* state on the
    chat.
    """
    if not FOOTAGE_VIBE_FLOW_ENABLED:
        return False
    return st.stage in VIBE_STAGES


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_bot: %(message)s",
)
log = logging.getLogger("tg_bot")


# --- Footage bucket previews mirror (precision flow, phase 4) -----------------
# Mirror of tg_bot_botapi: bucket_id -> preview. The public bot sends the
# `file_id_public` variant (captured via the public bot). Infra is mirrored for
# parity; the public vibe shortlist UI is wired separately behind its flag.
_BUCKET_PREVIEWS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_BUCKET_PREVIEW_FILE_ID_FIELD = "file_id_public"


def _bucket_previews_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "footage_bucket_previews.json"


def _load_bucket_previews() -> Dict[str, Dict[str, Any]]:
    global _BUCKET_PREVIEWS_CACHE
    if _BUCKET_PREVIEWS_CACHE is None:
        try:
            obj = json.loads(_bucket_previews_path().read_text(encoding="utf-8"))
            prev = obj.get("previews") if isinstance(obj, dict) else None
            _BUCKET_PREVIEWS_CACHE = prev if isinstance(prev, dict) else {}
        except Exception:
            _BUCKET_PREVIEWS_CACHE = {}
    return _BUCKET_PREVIEWS_CACHE


def _bucket_preview_file_id(bucket_id: str) -> str:
    e = _load_bucket_previews().get(str(bucket_id or "").strip())
    if not isinstance(e, dict):
        return ""
    return str(e.get(_BUCKET_PREVIEW_FILE_ID_FIELD) or "").strip()


def _vibe_display_label(label: str) -> str:
    """Tidy a vibe label so it matches the on-video caption: '/' -> ','."""
    import re as _re
    return _re.sub(r"\s*/\s*", ", ", str(label or "")).strip()


# Hook/shape/effect/subtitle example previews mirror (file_id_public variant).
_HOOK_PREVIEWS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _hook_previews_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "hook_previews.json"


def _load_hook_previews() -> Dict[str, Dict[str, Any]]:
    global _HOOK_PREVIEWS_CACHE
    if _HOOK_PREVIEWS_CACHE is None:
        try:
            obj = json.loads(_hook_previews_path().read_text(encoding="utf-8"))
            prev = obj.get("previews") if isinstance(obj, dict) else None
            _HOOK_PREVIEWS_CACHE = prev if isinstance(prev, dict) else {}
        except Exception:
            _HOOK_PREVIEWS_CACHE = {}
    return _HOOK_PREVIEWS_CACHE


def _hook_preview_file_id(key: str) -> str:
    e = _load_hook_previews().get(str(key or "").strip())
    if not isinstance(e, dict):
        return ""
    return str(e.get(_BUCKET_PREVIEW_FILE_ID_FIELD) or "").strip()


# Onboarding welcome reel (Telegram file_id). Replaces the static banner photo.
ONBOARDING_VIDEO_FILE_ID = "BAACAgIAAxkBAAEB-glqSiZ4jI6G4bLdR66LPl_X_6uXhAACvpoAAnC7UErgia72fPHcIDwE"
BTN_LETS_GO = "Едем!"
BTN_SUBSCRIBED = "Подписался!"
BTN_SEND_TRACK = "Отправить трек"
BTN_REUSE_INPUT = "Сделать под тот же трек"
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
BTN_BG_FOOTAGE = "Футажи"
BTN_BG_SOLID = "Цветной фон"
BTN_BG_STROBE = "Строб Ч/Б"
BTN_BG_INFO_NEXT = "Продолжить"
# Footage precision flow (Phase 2b): a "pictures (soon)" background stub shown
# alongside footage/solid when the vibe flow is on. Mirror of tg_bot_botapi —
# not implemented yet → replies "скоро".
BTN_BG_PICTURES = "Картинки (скоро)"
# Photo flow (4:3) ready button — shown instead of the stub when PHOTO_FLOW_ENABLED.
# Mirror of tg_bot_botapi; selecting it sets bg_mode="photo".
BTN_BG_PICTURES_PHOTO = "🖼 Картинки"
# Vibe shortlist (inline) control buttons + callback-data prefix. Mirror of team.
VIBE_CB_PREFIX = "vibe:"          # vibe:tog:<idx> | vibe:more | vibe:done | vibe:auto
BTN_VIBE_REFRESH = "Ещё варианты ›"
BTN_VIBE_DONE = "▶️ Готово"
BTN_VIBE_BACK = "‹ Назад"
BTN_VIBE_AUTO = "✨ По треку (авто)"
# How many buckets to show per shortlist page.
VIBE_PAGE_SIZE = 3
BTN_BG_WHITE = "Белый"
BTN_BG_BLACK = "Чёрный"
BTN_BG_GREEN = "Зелёный (хромакей)"
BTN_LAUNCH = "Запустить"
BTN_RESTART = "Начать заново"
BTN_NEXT = "Сделать следующий"
BTN_SUB_MODE_IMPULSE = "Impulse"
BTN_SUB_MODE_SCENES = "Jakson"
BTN_SUB_MODE_4TH = "Tape"
# 5th-template JSX modes — data mirrored from tg_bot_botapi for parity; not yet
# surfaced in the public picker (validated in the test bot first).
BTN_SUB_MODE_TRENDY = "Trendy"
BTN_SUB_MODE_BRAT = "Brat"

# Customization color palette (mirror of tg_bot_botapi). Data layer mirrored for
# parity; the picker UX lands in public when the hooks/customization flow does.
BTN_COLOR_DEFAULT = "По умолчанию"
_COLOR_PALETTE: dict[str, str] = {
    "Белый": "#FFFFFF",
    # Чёрный убран из палитры текста — сливается с тёмными фонами. Чёрный фон
    # остаётся отдельным выбором фона (BTN_BG_BLACK).
    "Красный": "#FF2D55",
    "Оранжевый": "#FF9500",
    "Жёлтый": "#FFD60A",
    "Зелёный": "#34C759",
    "Голубой": "#32ADE6",
    "Фиолетовый": "#AF52DE",
    "Розовый": "#FF2D92",
}
COLOR_PALETTE_BUTTONS = list(_COLOR_PALETTE.keys())


def _parse_color_choice(text: str) -> str | None:
    raw = str(text or "").strip()
    if raw == BTN_COLOR_DEFAULT:
        return ""
    return _COLOR_PALETTE.get(raw)


# ── Hook flow buttons (ported 1:1 from tg_bot_botapi; public UX behind
#    HOOK_FLOW_ENABLED). All 5 categories: Звук=F1, Объект=F2, Эффект=F3,
#    Движение=F4, Мысль=F5.
BTN_HOOK_YES = "Сделать хук"
BTN_HOOK_NO = "Без хука"
BTN_HOOK_DROP_NONE = "В отрывке нет дропа"
BTN_HOOK_DROP_MANUAL = "Ввести вручную"
BTN_HOOK_CAT_SOUND = "Звук"
BTN_HOOK_CAT_OBJECT = "Объект"
BTN_HOOK_CAT_EFFECT = "Эффект"
BTN_HOOK_CAT_MOTION = "Движение"
BTN_HOOK_CAT_THOUGHT = "Мысль"
HOOK_CATEGORY_BUTTONS = [
    BTN_HOOK_CAT_SOUND, BTN_HOOK_CAT_OBJECT, BTN_HOOK_CAT_EFFECT,
    BTN_HOOK_CAT_MOTION, BTN_HOOK_CAT_THOUGHT,
]
_HOOK_CATEGORY_BY_BUTTON = {
    BTN_HOOK_CAT_SOUND: "sound",
    BTN_HOOK_CAT_OBJECT: "object",
    BTN_HOOK_CAT_EFFECT: "effect",
    BTN_HOOK_CAT_MOTION: "motion",
    BTN_HOOK_CAT_THOUGHT: "thought",
}
_HOOK_CATEGORY_NOT_READY: set[str] = set()
# F4 «Движение» device picker
BTN_HOOK_DEV_SWIPE = "Свайп"
BTN_HOOK_DEV_TAP = "Тап"
BTN_HOOK_DEV_PINCH = "Зум"
BTN_HOOK_DEV_HOLD = "Задержи палец"
BTN_HOOK_DEV_HEAD = "Качай головой"
HOOK_MOTION_DEVICE_BUTTONS = [
    BTN_HOOK_DEV_SWIPE, BTN_HOOK_DEV_TAP, BTN_HOOK_DEV_PINCH,
    BTN_HOOK_DEV_HOLD, BTN_HOOK_DEV_HEAD,
]
_HOOK_MOTION_DEVICE_BY_BUTTON = {
    BTN_HOOK_DEV_SWIPE: "swipe",
    BTN_HOOK_DEV_TAP: "tap",
    BTN_HOOK_DEV_PINCH: "pinch",
    BTN_HOOK_DEV_HOLD: "holdfinger",
    BTN_HOOK_DEV_HEAD: "head",
}
# F5 «Мысль» device picker
BTN_HOOK_DEV_PUNCHLINE = "Панчлайн"
BTN_HOOK_DEV_MISSING_WORD = "Пропущенное слово"
BTN_HOOK_DEV_LYRIC_ECHO = "Эхо"
BTN_HOOK_DEV_QUESTION = "Вопрос к треку"
BTN_HOOK_DEV_INVERSE = "Инверсия"
HOOK_DEVICE_BUTTONS = [
    BTN_HOOK_DEV_PUNCHLINE, BTN_HOOK_DEV_MISSING_WORD, BTN_HOOK_DEV_LYRIC_ECHO,
    BTN_HOOK_DEV_QUESTION, BTN_HOOK_DEV_INVERSE,
]
_HOOK_DEVICE_BY_BUTTON = {
    BTN_HOOK_DEV_PUNCHLINE: "punchline",
    BTN_HOOK_DEV_MISSING_WORD: "missing_word",
    BTN_HOOK_DEV_LYRIC_ECHO: "lyric_echo",
    BTN_HOOK_DEV_QUESTION: "question_to_track",
    BTN_HOOK_DEV_INVERSE: "inverse_lyric",
}
# F3 «Эффект» 3-step picker
BTN_FX_SKIP = "Пропустить"
BTN_FX_HOOK_LIGHT = "Молния"
BTN_FX_HOOK_SHUTTER = "Затвор"
BTN_FX_HOOK_SLOW = "Слоу-шаттер"
BTN_FX_HOOK_NEGZOOM = "Негатив-зум"
_FX_HOOK_BY_BUTTON = {
    BTN_FX_HOOK_LIGHT: "hook_light",
    BTN_FX_HOOK_SHUTTER: "shutter_effect",
    BTN_FX_HOOK_SLOW: "flash_slow_shutter",
    BTN_FX_HOOK_NEGZOOM: "negative_zoom",
}
BTN_FX_TR_SNAP = "Снап-вайп"
BTN_FX_TR_MINIMAX = "Минимакс"
BTN_FX_TR_INVERT = "Инверт"
BTN_FX_TR_EXTRACT = "Экстракт"
BTN_FX_TR_FLASH = "Вспышки"
BTN_FX_TR_SHAKE = "Тряска"
_FX_TRANSITION_BY_BUTTON = {
    BTN_FX_TR_SNAP: "snap_wipe",
    BTN_FX_TR_MINIMAX: "minimax",
    BTN_FX_TR_INVERT: "invert_flash",
    BTN_FX_TR_EXTRACT: "extract_flash",
    BTN_FX_TR_FLASH: "flash_on_cuts",
    BTN_FX_TR_SHAKE: "layer_shake",
}
BTN_FX_EX_XEROX = "Ксерокс"
BTN_FX_EX_ANALOG = "Аналог-глитч"
BTN_FX_EX_NEON = "Неон"
BTN_FX_EX_OLDCAM = "Старая камера"
BTN_FX_EX_BLACKWHITE = "Ч/Б"
BTN_FX_EX_CRYSTAL = "Crystal Glow"
BTN_FX_EX_NIGHT = "Night Vision"
BTN_FX_EX_WAVE = "Wave"
_FX_EXTRA_BY_BUTTON = {
    BTN_FX_EX_XEROX: "xerox",
    BTN_FX_EX_ANALOG: "analog_glitch",
    BTN_FX_EX_NEON: "neon_extract",
    BTN_FX_EX_OLDCAM: "old_camera",
    BTN_FX_EX_BLACKWHITE: "blackwhite",
    BTN_FX_EX_CRYSTAL: "crystal_glow",
    BTN_FX_EX_NIGHT: "night_vision",
    BTN_FX_EX_WAVE: "wave",
}
BTN_FX_EXT_STD = "Стандарт"
BTN_FX_EXT_END = "До конца ролика"
BTN_FX_EXT_3 = "3 футажа после"
_FX_EXTEND_BY_BUTTON = {
    BTN_FX_EXT_STD: "",
    BTN_FX_EXT_END: "to_end",
    BTN_FX_EXT_3: "after_drop:3",
}
BTN_FX_EXTRA_FULL_ALL = "Стилизация на весь ролик"
BTN_FX_EXTRA_FULL_PREDROP = "Только до дропа"
# F2 «Объект» shape picker
BTN_F2_SHAPE_RHOMB = "Ромб"
BTN_F2_SHAPE_SQUARE = "Квадрат"
BTN_F2_SHAPE_STAR1 = "Звезда-10"
BTN_F2_SHAPE_STAR2 = "Звезда-5"
BTN_F2_SHAPE_ELIPSE = "Эллипс"
_F2_SHAPE_BY_BUTTON = {
    BTN_F2_SHAPE_RHOMB: "rhomb",
    BTN_F2_SHAPE_SQUARE: "square",
    BTN_F2_SHAPE_STAR1: "star1",
    BTN_F2_SHAPE_STAR2: "star2",
    BTN_F2_SHAPE_ELIPSE: "elipse",
}

# Reverse id -> RU-label maps. The chat state stores pipeline ids (e.g.
# "snap_wipe"/"rhomb"/"swipe"/"punchline"); the confirm summary echoes the picks
# in Russian for the user instead of the raw English ids.
_F3_HOOK_RU_BY_ID = {v: k for k, v in F3_HOOK_LABELS_RU.items()}
_F3_TRANSITION_RU_BY_ID = {v: k for k, v in F3_TRANSITION_LABELS_RU.items()}
_F3_EXTRA_RU_BY_ID = {v: k for k, v in F3_EXTRA_LABELS_RU.items()}
_F2_SHAPE_RU_BY_ID = {v: k for k, v in F2_SHAPE_LABELS_RU.items()}
_F4_DEVICE_RU_BY_ID = {v: k for k, v in F4_MOTION_DEVICE_LABELS_RU.items()}
_F5_DEVICE_RU_BY_ID = {v: k for k, v in _HOOK_DEVICE_BY_BUTTON.items()}

# Photo flow (4:3) — two F3-style picker steps. Mirror of tg_bot_botapi; UX is
# gated behind PHOTO_FLOW_ENABLED but the data layer (buttons/maps/id sets) is
# mirrored for CI parity. Id sets must stay in sync with the schema Literals.
BTN_PHOTO_STYLE_NONE = "Без стилизации"
BTN_PHOTO_STYLE_WARM = "Тёплый"
BTN_PHOTO_STYLE_COLD = "Холодный"
BTN_PHOTO_STYLE_VINTAGE = "Винтаж"
BTN_PHOTO_STYLE_BW = "Ч/Б"
BTN_PHOTO_STYLE_VHS = "VHS"
_PHOTO_STYLE_BY_BUTTON = {
    BTN_PHOTO_STYLE_NONE: "none",
    BTN_PHOTO_STYLE_WARM: "warm",
    BTN_PHOTO_STYLE_COLD: "cold",
    BTN_PHOTO_STYLE_VINTAGE: "vintage",
    BTN_PHOTO_STYLE_BW: "bw",
    BTN_PHOTO_STYLE_VHS: "vhs",
}
BTN_PHOTO_TR_FLASH = "Вспышка"
BTN_PHOTO_TR_NONE = "Без перехода"
BTN_PHOTO_TR_SLIDE = "Слайд"
BTN_PHOTO_TR_ZOOM = "Зум"
BTN_PHOTO_TR_WHIP = "Вжух"
_PHOTO_TRANSITION_BY_BUTTON = {
    BTN_PHOTO_TR_FLASH: "flash",
    BTN_PHOTO_TR_NONE: "none",
    BTN_PHOTO_TR_SLIDE: "slide",
    BTN_PHOTO_TR_ZOOM: "zoom",
    BTN_PHOTO_TR_WHIP: "whip",
}
PHOTO_STYLE_IDS = set(_PHOTO_STYLE_BY_BUTTON.values())
PHOTO_STYLE_LABELS_RU = dict(_PHOTO_STYLE_BY_BUTTON)
PHOTO_TRANSITION_IDS = set(_PHOTO_TRANSITION_BY_BUTTON.values())
PHOTO_TRANSITION_LABELS_RU = dict(_PHOTO_TRANSITION_BY_BUTTON)

# Post-generation flow buttons
BTN_RATE_LOW = "До 5"
BTN_RATE_MID_LOW = "5-6"
BTN_RATE_MID_HIGH = "7-8"
BTN_RATE_HIGH = "9-10"
BTN_RATE_BUTTONS = [BTN_RATE_LOW, BTN_RATE_MID_LOW, BTN_RATE_MID_HIGH, BTN_RATE_HIGH]

BTN_LETS_DO_IT = "Делаем!"
BTN_HOW_SO = "Как же?"
BTN_WANT_THIS = "Хочу так!"

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
BTN_BUY_ONCE = "Купить разово"
BTN_BUY_SUBSCRIPTION = "Купить по подписке"
BTN_CONFIRM = "Подтвердить"

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
    BTN_SUB_MODE_TRENDY,
    BTN_SUB_MODE_BRAT,
]
_SUBTITLES_MODE_BY_BUTTON = {
    BTN_SUB_MODE_IMPULSE: SUBTITLES_MODE_IMPULSE_2ND,
    BTN_SUB_MODE_SCENES: SUBTITLES_MODE_SCENES_3RD,
    BTN_SUB_MODE_4TH: SUBTITLES_MODE_TEMPLATE_4TH,
    # Mirrored for parity; not in SUBTITLES_MODE_BUTTONS (curated public picker).
    BTN_SUB_MODE_TRENDY: SUBTITLES_MODE_TRENDY_5TH,
    BTN_SUB_MODE_BRAT: SUBTITLES_MODE_BRAT_5TH,
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
    BTN_REUSE_INPUT,
    BTN_SEND_LYRICS,
    BTN_SKIP_LYRICS,
    BTN_SEND_FRAGMENT,
    BTN_SKIP_FRAGMENT,
    BTN_SET_TIMING,
    BTN_SKIP_TIMING,
    BTN_CONFIRM_YES,
    BTN_CONFIRM_BACK,
    BTN_BACK,
    BTN_BG_FOOTAGE,
    BTN_BG_SOLID,
    BTN_BG_WHITE,
    BTN_BG_BLACK,
    BTN_BG_GREEN,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_SUB_MODE_4TH,
    BTN_SUB_MODE_TRENDY,
    BTN_SUB_MODE_BRAT,
    BTN_COLOR_DEFAULT,
    *COLOR_PALETTE_BUTTONS,
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
_TG_VIDEO_COMPRESS_CRF_STEPS = (30, 32, 34, 36)
_GENERATION_FAILED_USER_TEXT = (
    "Увидели ошибку, сейчас с тобой свяжется менеджер и запустит генерацию ролика вручную, "
    "а пока тех. отдел все проверит"
)
_AUDIO_PREPARE_FAILED_USER_TEXT = (
    "Не получилось подготовить трек к генерации. "
    "Попробуй отправить его ещё раз чуть позже."
)
_AUDIO_PREPARE_TG_FAILED_USER_TEXT = (
    "Не получилось получить файл из Telegram. "
    "Попробуй отправить трек ещё раз, лучше в mp3 или m4a."
)
_PACKAGE_COMMAND_ALIASES = {
    "/packets",
    "/package",
    "/packages",
    "/зackages",
    "/пакеты",
    "/тарифы",
}
_RESULT_SOURCE_MISSING_USER_TEXT = (
    "Видео собрано, но ссылка на файл не вернулась. "
    "Мы уже увидели проблему и проверяем её."
)
_VIDEO_DELIVERY_FAILED_USER_TEXT = (
    "Не получилось отправить видео прямо в Telegram, "
    "но сам файл сохранился."
)
_TG_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


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


def _message_chat_id(message: Message) -> int:
    try:
        return int(message.chat.id) if message.chat is not None else 0
    except Exception:
        return 0


def _message_username(message: Message) -> str:
    try:
        if message.from_user is None:
            return ""
        return _normalize_username(getattr(message.from_user, "username", "") or "")
    except Exception:
        return ""


def _message_update_id(message: Message) -> str:
    try:
        event_update = getattr(message, "event_update", None)
        uid = getattr(event_update, "update_id", "")
        return str(uid or "")
    except Exception:
        return ""


def _message_text_kind(message: Message) -> str:
    text = str(getattr(message, "text", "") or "").strip()
    if text:
        if text.startswith("/"):
            return "command"
        if _is_control_button_text(text):
            return "button"
        return "text"
    if getattr(message, "audio", None) is not None:
        return "audio"
    if getattr(message, "document", None) is not None:
        return "document"
    if getattr(message, "video", None) is not None:
        return "video"
    if getattr(message, "photo", None) is not None:
        return "photo"
    return "other"


def _normalized_command_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return ""
    first = raw.split(maxsplit=1)[0].strip().lower()
    if "@" in first:
        first = first.split("@", 1)[0]
    return first


def _is_packages_command_text(text: str) -> bool:
    return _normalized_command_text(text) in _PACKAGE_COMMAND_ALIASES


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


# Rotation cursor advances by 1 after every SUCCEEDED job (no quality gate).
# Diag signals are still computed for observability but no longer gate advance.
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
    """Always advance after a SUCCEEDED job (see same fn in tg_bot_botapi)."""
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
    """Build a short Russian user-facing message about the rotation move."""
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
        self.telegram_api = make_telegram_api(settings.tg_bot_api_env)
        self.store = RedisChatStateStore(settings)
        self.s3 = S3Client(settings)
        self.orchestrator = OrchestratorClient(base_url=settings.orchestrator_public_url, timeout_s=60.0)
        if not settings.credits_db_url:
            raise RuntimeError("CREDITS_DB_URL (or POSTGRES_*) is required for tg_bot_public")
        self.credits_db = CreditsDB(settings.credits_db_url)
        self.runtime_store: GenerationRuntimeStore | None = None
        self.tbank = TBankClient(
            terminal_key=settings.tbank_terminal_key,
            password=settings.tbank_password,
            notify_url=settings.tbank_notify_url,
        ) if settings.tbank_terminal_key else None
        if self.tbank:
            notify_url = str(settings.tbank_notify_url or "").strip()
            if not notify_url:
                log.error(
                    "TBANK_NOTIFY_URL is empty — T-Bank webhooks (incl. RebillId) "
                    "will not be delivered, subscriptions will not bootstrap. "
                    "Set TBANK_NOTIFY_URL=https://<your-host>/api/tbank/notify."
                )
            else:
                log.info("tbank notify URL configured: %s", notify_url)
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
        self._startup_maintenance_task: asyncio.Task[None] | None = None
        self._outbox_task: asyncio.Task[None] | None = None
        self._bot: Bot | None = None
        self._preview_source_bot_token = str(settings.tg_preview_source_bot_token or "").strip()
        self._preview_source_file_url_cache: Dict[str, str] = {}
        node_id = str(settings.tg_processing_node_id or "").strip() or "unknown-node"
        self._processing_owner_id = f"{node_id}:{os.getpid()}"
        self._processing_lock_ttl_s = max(30, int(settings.tg_processing_lock_ttl_s or 240))

        self._register_handlers()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

    @staticmethod
    def _runtime_surface() -> str:
        return "public"

    def _runtime_owner_id(self) -> str:
        node_id = str(self.settings.tg_processing_node_id or "unknown-node").strip() or "unknown-node"
        return f"{self._runtime_surface()}:{node_id}:{os.getpid()}"

    def _runtime_outbox_owner_id(self) -> str:
        return f"{self._runtime_owner_id()}:outbox"

    def _build_generation_run_id(self, *, chat_id: int) -> str:
        return f"{self._runtime_surface()}-{int(chat_id)}-{uuid.uuid4().hex}"

    def _runtime_snapshot_payload(
        self,
        *,
        st: ChatState,
        audio_s3_url: str,
        versions_total: int,
    ) -> Dict[str, Any]:
        return {
            "chat_id": int(st.chat_id),
            "chat_username": str(st.chat_username or ""),
            "audio_s3_url": str(audio_s3_url or ""),
            "lyrics_text": str(st.lyrics_text or ""),
            "target_fragment": str(st.target_fragment or ""),
            "footage_artist_id": str(st.footage_artist_id or ""),
            "bg_mode": str(st.bg_mode or "footage"),
            "bg_solid_color": str(st.bg_solid_color or ""),
            "subtitles_mode": str(st.subtitles_mode or ""),
            "user_clip_start_sec": float(st.user_clip_start_sec or 0.0),
            "user_clip_end_sec": float(st.user_clip_end_sec or 0.0),
            "versions_total": max(1, int(versions_total)),
        }

    async def _runtime_start_run(
        self,
        *,
        st: ChatState,
        batch_id: str,
        audio_s3_url: str,
        versions_total: int,
    ) -> str:
        store = getattr(self, "runtime_store", None)
        if store is None:
            return ""
        run_id = str(st.generation_run_id or "").strip() or self._build_generation_run_id(chat_id=st.chat_id)
        await store.upsert_run(
            run_id=run_id,
            surface=self._runtime_surface(),
            chat_id=int(st.chat_id),
            batch_id=str(batch_id or ""),
            status="running",
            versions_total=max(1, int(versions_total)),
            next_version_to_enqueue=1,
            current_stage="enqueue_start",
        )
        await store.record_event(
            run_id=run_id,
            surface=self._runtime_surface(),
            event_type="run_started",
            payload=self._runtime_snapshot_payload(
                st=st,
                audio_s3_url=audio_s3_url,
                versions_total=versions_total,
            ),
        )
        return run_id

    async def _runtime_update_run(
        self,
        *,
        st: ChatState,
        status: Optional[str] = None,
        current_stage: Optional[str] = None,
        next_version_to_enqueue: Optional[int] = None,
        last_error_code: Optional[str] = None,
        last_error_text: Optional[str] = None,
    ) -> None:
        store = getattr(self, "runtime_store", None)
        run_id = str(st.generation_run_id or "").strip()
        if store is None or not run_id:
            return
        await store.update_run(
            run_id,
            status=status,
            current_stage=current_stage,
            next_version_to_enqueue=next_version_to_enqueue,
            last_error_code=last_error_code,
            last_error_text=last_error_text,
        )

    async def _runtime_attach_version(
        self,
        *,
        st: ChatState,
        version_index: int,
        job_id: str,
        reuse_text_job_id: str = "",
    ) -> None:
        store = getattr(self, "runtime_store", None)
        run_id = str(st.generation_run_id or "").strip()
        if store is None or not run_id:
            return
        await store.upsert_version(
            run_id=run_id,
            version_index=max(1, int(version_index)),
            job_id=str(job_id or ""),
            job_status="QUEUED",
            job_stage="build",
            resume_source_job_id=str(reuse_text_job_id or ""),
        )
        await store.record_event(
            run_id=run_id,
            surface=self._runtime_surface(),
            job_id=str(job_id or ""),
            event_type="version_enqueued",
            payload={
                "version_index": max(1, int(version_index)),
                "job_id": str(job_id or ""),
                "resume_source_job_id": str(reuse_text_job_id or ""),
            },
        )

    async def _runtime_sync_version_from_job(
        self,
        *,
        st: ChatState,
        job_id: str,
        job: Dict[str, Any],
    ) -> None:
        store = getattr(self, "runtime_store", None)
        run_id = str(st.generation_run_id or "").strip()
        if store is None or not run_id:
            return
        version_index = max(1, int(self._version_num_for_job(st, job_id) or 1))
        req = job.get("request") if isinstance(job.get("request"), dict) else {}
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        resume_state = result.get("resume_state") if isinstance(result.get("resume_state"), dict) else None
        await store.upsert_version(
            run_id=run_id,
            version_index=version_index,
            job_id=str(job_id or ""),
            job_status=str(job.get("status") or "NEW"),
            job_stage=str(job.get("stage") or ""),
            worker_type=str(req.get("llm_worker_type") or ""),
            origin_node=str(req.get("origin_node") or ""),
            build_queue=str(req.get("build_queue") or ""),
            render_queue=str(req.get("render_queue") or ""),
            result_url=_resolve_job_video_source(job, self.settings),
            archive_url=_resolve_job_project_archive_source(job),
            resume_source_job_id=str(req.get("reuse_text_job_id") or ""),
            resume_state=resume_state,
            resume_state_source=str(result.get("resume_state_source") or ""),
            resume_state_checksum_value=str(result.get("resume_state_checksum") or ""),
            last_error_text=str(job.get("error") or ""),
        )
        await self._runtime_update_run(
            st=st,
            current_stage=str(job.get("stage") or ""),
            last_error_text=str(job.get("error") or ""),
        )

    async def _runtime_record_version_succeeded(
        self,
        *,
        st: ChatState,
        job_id: str,
        used_file_names: List[str],
    ) -> None:
        store = getattr(self, "runtime_store", None)
        run_id = str(st.generation_run_id or "").strip()
        if store is None or not run_id:
            return
        await store.record_event(
            run_id=run_id,
            surface=self._runtime_surface(),
            job_id=str(job_id or ""),
            event_type="version_succeeded",
            payload={
                "job_id": str(job_id or ""),
                "version_index": max(1, int(self._version_num_for_job(st, job_id) or 1)),
                "used_file_names": [str(x) for x in list(used_file_names or []) if str(x)],
            },
        )

    @staticmethod
    def _runtime_outbox_key(*, run_id: str, kind: str, job_id: str = "", suffix: str = "") -> str:
        parts = ["public", str(run_id or ""), str(kind or "")]
        if job_id:
            parts.append(str(job_id))
        if suffix:
            parts.append(str(suffix))
        return ":".join(parts)

    async def _runtime_claim_outbox(
        self,
        *,
        st: ChatState,
        kind: str,
        payload: Dict[str, Any],
        job_id: str = "",
        suffix: str = "",
        lease_s: int = 21600,
    ) -> tuple[bool, str]:
        store = getattr(self, "runtime_store", None)
        run_id = str(st.generation_run_id or "").strip()
        if store is None or not run_id:
            return True, ""
        dedupe_key = self._runtime_outbox_key(run_id=run_id, kind=kind, job_id=job_id, suffix=suffix)
        await store.ensure_outbox_item(
            run_id=run_id,
            surface=self._runtime_surface(),
            kind=kind,
            dedupe_key=dedupe_key,
            payload=payload,
            job_id=job_id,
        )
        claimed = await store.claim_outbox_item(
            dedupe_key=dedupe_key,
            owner_id=self._runtime_owner_id(),
            lease_s=max(300, int(lease_s)),
            allow_stale_lease=False,
        )
        if claimed:
            return True, dedupe_key
        return False, dedupe_key

    async def _runtime_mark_outbox_sent(
        self,
        *,
        dedupe_key: str,
        payload_patch: Optional[Dict[str, Any]] = None,
    ) -> None:
        store = getattr(self, "runtime_store", None)
        if store is None or not dedupe_key:
            return
        await store.mark_outbox_sent(dedupe_key=dedupe_key, payload_patch=payload_patch)

    async def _runtime_mark_outbox_failed(
        self,
        *,
        dedupe_key: str,
        error_text: str,
        retry_delay_s: int = 0,
        keep_leased: bool = False,
    ) -> None:
        store = getattr(self, "runtime_store", None)
        if store is None or not dedupe_key:
            return
        await store.mark_outbox_failed(
            dedupe_key=dedupe_key,
            error_text=error_text,
            retry_delay_s=retry_delay_s,
            keep_leased=keep_leased,
        )

    def _runtime_outbox_retry_delay_s(self, item: Dict[str, Any]) -> int:
        attempt = max(1, int(item.get("attempt_count") or 1))
        base = max(1, int(getattr(self.settings, "tg_outbox_retry_base_s", 30) or 30))
        max_delay = max(base, int(getattr(self.settings, "tg_outbox_retry_max_s", 900) or 900))
        return min(max_delay, base * (2 ** min(6, attempt - 1)))

    @staticmethod
    def _runtime_outbox_terminal_error(exc: Exception) -> bool:
        text = repr(exc).lower()
        return (
            exc.__class__.__name__ == "TelegramForbiddenError"
            or "bot was blocked" in text
            or "chat not found" in text
            or "user is deactivated" in text
        )

    @staticmethod
    def _runtime_outbox_payload(item: Dict[str, Any]) -> Dict[str, Any]:
        payload = item.get("payload")
        return dict(payload) if isinstance(payload, dict) else {}

    async def _runtime_run_snapshot(self, run_id: str) -> Dict[str, Any]:
        store = getattr(self, "runtime_store", None)
        if store is None or not str(run_id or "").strip():
            return {}
        events = await store.list_events(str(run_id), event_type="run_started", limit=1)
        if events and isinstance(events[0].get("payload"), dict):
            return dict(events[0].get("payload") or {})
        return {}

    async def _runtime_outbox_context(self, item: Dict[str, Any]) -> tuple[Dict[str, Any], ChatState, Dict[str, Any]]:
        store = getattr(self, "runtime_store", None)
        if store is None:
            raise RuntimeError("runtime_store is not ready")
        payload = self._runtime_outbox_payload(item)
        run_id = str(item.get("run_id") or "").strip()
        if not run_id:
            raise RuntimeError("outbox item has empty run_id")
        run = await store.get_run(run_id)
        if not run:
            raise RuntimeError(f"generation run not found for outbox run_id={run_id}")
        chat_id = int(run.get("chat_id") or payload.get("chat_id") or 0)
        if chat_id <= 0:
            raise RuntimeError(f"outbox item has invalid chat_id run_id={run_id}")
        st = await self.store.get(chat_id)
        st.generation_run_id = str(st.generation_run_id or run_id)
        if not str(st.chat_username or "").strip():
            snapshot = await self._runtime_run_snapshot(run_id)
            st.chat_username = str(snapshot.get("chat_username") or st.chat_username or "")
        return run, st, payload

    def _runtime_outbox_ver_label(
        self,
        *,
        st: ChatState,
        run: Dict[str, Any],
        payload: Dict[str, Any],
        job_id: str,
    ) -> str:
        explicit = str(payload.get("ver_label") or "").strip()
        if explicit:
            return explicit
        total = max(1, int(run.get("versions_total") or st.batch_total_versions or len(st.job_order or []) or 1))
        ver = self._version_num_for_job(st, job_id)
        return f"Версия {ver}/{total}" if ver > 0 else f"job_id={job_id}"

    async def _runtime_outbox_version(self, job_id: str) -> Dict[str, Any]:
        store = getattr(self, "runtime_store", None)
        jid = str(job_id or "").strip()
        if store is None or not jid:
            return {}
        try:
            return await store.get_version_by_job(jid)
        except Exception:
            return {}

    async def _runtime_record_outbox_event(
        self,
        item: Dict[str, Any],
        *,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        store = getattr(self, "runtime_store", None)
        run_id = str(item.get("run_id") or "").strip()
        if store is None or not run_id:
            return
        try:
            await store.record_event(
                run_id=run_id,
                surface=self._runtime_surface(),
                job_id=str(item.get("job_id") or ""),
                event_type=event_type,
                payload=payload,
            )
        except Exception as exc:
            log.warning("runtime_outbox_event_record_failed key=%s err=%r", item.get("dedupe_key"), exc)

    async def _runtime_dispatch_ready_outbox_once(self) -> int:
        store = getattr(self, "runtime_store", None)
        if store is None:
            return 0
        items = await store.claim_ready_outbox_items(
            surface=self._runtime_surface(),
            owner_id=self._runtime_outbox_owner_id(),
            limit=max(1, int(getattr(self.settings, "tg_outbox_dispatch_batch_size", 25) or 25)),
            stale_lease_s=max(60, int(getattr(self.settings, "tg_outbox_stale_lease_s", 1800) or 1800)),
        )
        for item in items:
            dedupe_key = str(item.get("dedupe_key") or "").strip()
            if not dedupe_key:
                continue
            try:
                payload_patch = await self._runtime_dispatch_outbox_item(item)
                await self._runtime_mark_outbox_sent(
                    dedupe_key=dedupe_key,
                    payload_patch=payload_patch,
                )
                await self._runtime_record_outbox_event(
                    item,
                    event_type="outbox_sent",
                    payload={
                        "dedupe_key": dedupe_key,
                        "kind": str(item.get("kind") or ""),
                        "attempt_count": int(item.get("attempt_count") or 0),
                        **dict(payload_patch or {}),
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._runtime_outbox_terminal_error(exc):
                    terminal_error = _compact_text(repr(exc), limit=500)
                    log.warning(
                        "runtime_outbox_terminal_delivery key=%s kind=%s err=%s",
                        dedupe_key,
                        item.get("kind"),
                        terminal_error,
                    )
                    await self._runtime_mark_outbox_sent(
                        dedupe_key=dedupe_key,
                        payload_patch={
                            "sent_mode": "telegram_undeliverable",
                            "terminal_error": terminal_error,
                        },
                    )
                    await self._runtime_record_outbox_event(
                        item,
                        event_type="outbox_undeliverable",
                        payload={
                            "dedupe_key": dedupe_key,
                            "kind": str(item.get("kind") or ""),
                            "error": terminal_error,
                        },
                    )
                    continue
                delay_s = self._runtime_outbox_retry_delay_s(item)
                log.warning(
                    "runtime_outbox_dispatch_failed key=%s kind=%s retry_delay_s=%s err=%r",
                    dedupe_key,
                    item.get("kind"),
                    delay_s,
                    exc,
                )
                await self._runtime_mark_outbox_failed(
                    dedupe_key=dedupe_key,
                    error_text=repr(exc),
                    retry_delay_s=delay_s,
                    keep_leased=False,
                )
                await self._runtime_record_outbox_event(
                    item,
                    event_type="outbox_failed",
                    payload={
                        "dedupe_key": dedupe_key,
                        "kind": str(item.get("kind") or ""),
                        "retry_delay_s": int(delay_s),
                        "error": repr(exc),
                    },
                )
        return len(items)

    async def _runtime_outbox_loop(self) -> None:
        interval_s = max(1.0, float(getattr(self.settings, "tg_outbox_dispatch_interval_s", 10.0) or 10.0))
        while True:
            try:
                claimed = await self._runtime_dispatch_ready_outbox_once()
                if claimed <= 0:
                    await asyncio.sleep(interval_s)
                else:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("runtime_outbox_loop_iteration_failed err=%r", exc)
                await asyncio.sleep(interval_s)

    async def _runtime_dispatch_outbox_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(item.get("kind") or "").strip()
        if kind == "result_source_missing_notice":
            return await self._runtime_dispatch_result_source_missing_notice(item)
        if kind == "telegram_video_delivery":
            return await self._runtime_dispatch_telegram_video_delivery(item)
        if kind == "telegram_project_archive_notice":
            return await self._runtime_dispatch_project_archive_notice(item)
        if kind in {"generation_failed_refund", "enqueue_next_failed_refund"}:
            return await self._runtime_dispatch_refund(item)
        if kind in {"generation_failed_manager_alert", "enqueue_next_failed_manager_alert"}:
            return await self._runtime_dispatch_manager_alert(item)
        if kind in {"generation_failed_user_notice", "enqueue_next_failed_user_notice"}:
            return await self._runtime_dispatch_user_notice(item)
        raise RuntimeError(f"unsupported public outbox kind={kind!r}")

    async def _runtime_dispatch_result_source_missing_notice(self, item: Dict[str, Any]) -> Dict[str, Any]:
        run, st, payload = await self._runtime_outbox_context(item)
        bot = self._require_bot()
        job_id = str(payload.get("job_id") or item.get("job_id") or "").strip()
        stage = str(payload.get("stage") or "render").strip()
        ver_label = self._runtime_outbox_ver_label(st=st, run=run, payload=payload, job_id=job_id)
        await self._notify_ops_alert(
            title="Render result without output source",
            chat_id=st.chat_id,
            username=st.chat_username,
            job_id=job_id,
            stage=stage,
            error_text=json.dumps(payload, ensure_ascii=False)[:1200],
        )
        await bot.send_message(st.chat_id, f"{ver_label}: {_RESULT_SOURCE_MISSING_USER_TEXT}")
        return {"sent_mode": "user_notice"}

    async def _runtime_dispatch_telegram_video_delivery(self, item: Dict[str, Any]) -> Dict[str, Any]:
        run, st, payload = await self._runtime_outbox_context(item)
        bot = self._require_bot()
        job_id = str(payload.get("job_id") or item.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("telegram_video_delivery outbox item has empty job_id")
        version = await self._runtime_outbox_version(job_id)
        source = str(payload.get("source") or version.get("result_url") or st.last_result_url or "").strip()
        if not source:
            raise RuntimeError(f"telegram_video_delivery has empty source job_id={job_id}")
        ver_label = self._runtime_outbox_ver_label(st=st, run=run, payload=payload, job_id=job_id)
        stage = str(payload.get("stage") or "render_delivery").strip()

        video_path = self.settings.tmp_dir / str(st.chat_id) / "result" / f"{job_id}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        send_video_path = video_path
        send_file_error = ""
        try:
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
                return {"sent_mode": "telegram_video", "source": source}
            except Exception as exc:
                send_file_error = str(exc)
                log.warning("outbox_send_file_failed chat=%s job=%s err=%s", st.chat_id, job_id, send_file_error)

            fallback_link = await self._build_fallback_link(source)
            await self._notify_ops_alert(
                title="Telegram video delivery failed",
                chat_id=st.chat_id,
                username=st.chat_username,
                job_id=job_id,
                stage=stage,
                error_text=send_file_error,
                extra_lines=[f"has_fallback_link: {bool(fallback_link)}"],
            )
            msg = f"{ver_label}: {_VIDEO_DELIVERY_FAILED_USER_TEXT}"
            if fallback_link:
                msg += f"\nСсылка: {fallback_link}"
            await bot.send_message(st.chat_id, msg)
            return {
                "sent_mode": "fallback_link",
                "fallback_link": fallback_link,
                "source": source,
                "send_file_error": _compact_text(send_file_error, limit=500),
            }
        finally:
            try:
                for path in {video_path, send_video_path}:
                    if path.exists():
                        path.unlink()
            except Exception:
                pass

    async def _runtime_dispatch_project_archive_notice(self, item: Dict[str, Any]) -> Dict[str, Any]:
        run, st, payload = await self._runtime_outbox_context(item)
        bot = self._require_bot()
        job_id = str(payload.get("job_id") or item.get("job_id") or "").strip()
        ver_label = self._runtime_outbox_ver_label(st=st, run=run, payload=payload, job_id=job_id)
        version = await self._runtime_outbox_version(job_id)
        archive_source = str(payload.get("archive_source") or version.get("archive_url") or "").strip()
        if not archive_source and job_id:
            job = await self.orchestrator.get_job(job_id)
            archive_source = _resolve_job_project_archive_source(job)
        if archive_source:
            archive_link = await self._build_fallback_link(archive_source)
            if not archive_link:
                archive_link = archive_source
            await bot.send_message(st.chat_id, f"{ver_label}: проект (AEP + ресурсы): {archive_link}")
            return {"archive_link": archive_link}
        await bot.send_message(
            st.chat_id,
            f"{ver_label}: видео готово, но ссылка на архив проекта в ответе рендера не найдена.",
        )
        return {"archive_link": ""}

    async def _runtime_dispatch_refund(self, item: Dict[str, Any]) -> Dict[str, Any]:
        run, st, payload = await self._runtime_outbox_context(item)
        refund_versions = int(payload.get("refund_versions") or payload.get("refunded_versions") or 0)
        if refund_versions <= 0:
            return {"sent_mode": "refund_skipped", "refund_versions": 0}
        failed_job_id = str(payload.get("failed_job_id") or item.get("job_id") or "batch").strip() or "batch"
        await self.credits_db.add_credits(
            st.chat_id,
            int(refund_versions),
            "generation_failed_refund",
            admin_note=f"run={run.get('run_id') or '-'} batch={run.get('batch_id') or '-'} job={failed_job_id}",
            actor="outbox_dispatcher",
            order_id=str(item.get("dedupe_key") or ""),
        )
        return {"sent_mode": "refund", "refund_versions": int(refund_versions)}

    async def _runtime_dispatch_manager_alert(self, item: Dict[str, Any]) -> Dict[str, Any]:
        run, st, payload = await self._runtime_outbox_context(item)
        kind = str(item.get("kind") or "")
        refund_versions = int(payload.get("refunded_versions") or payload.get("refund_versions") or 0)
        total_versions = max(1, int(payload.get("total_versions") or run.get("versions_total") or st.batch_total_versions or 1))
        succeeded_versions = int(payload.get("succeeded_versions") or max(0, total_versions - refund_versions))
        job_id = str(payload.get("failed_job_id") or item.get("job_id") or "").strip()
        if not job_id and kind.startswith("enqueue_next"):
            job_id = "enqueue_next_version"
        stage = str(payload.get("failed_stage") or run.get("current_stage") or "").strip()
        if not stage and kind.startswith("enqueue_next"):
            stage = "enqueue_next_version"
        error_text = str(payload.get("error_text") or run.get("last_error_text") or st.last_job_error or "").strip()
        await self._notify_manager_generation_failure(
            st=st,
            job_id=job_id,
            stage=stage,
            error_text=error_text,
            succeeded_versions=int(succeeded_versions),
            total_versions=int(total_versions),
            refunded_versions=int(refund_versions),
            strict=True,
        )
        return {"sent_mode": "manager_alert"}

    async def _runtime_dispatch_user_notice(self, item: Dict[str, Any]) -> Dict[str, Any]:
        _, st, _ = await self._runtime_outbox_context(item)
        bot = self._require_bot()
        await bot.send_message(
            st.chat_id,
            _GENERATION_FAILED_USER_TEXT,
            reply_markup=self._wait_audio_reuse_kb(),
        )
        return {"sent_mode": "user_notice"}

    async def _restore_runtime_processing_states(self) -> None:
        store = getattr(self, "runtime_store", None)
        if store is None:
            return
        try:
            runs = await store.list_incomplete_runs(surface=self._runtime_surface(), limit=200)
        except Exception as exc:
            log.warning("runtime_restore_runs_failed err=%r", exc)
            return

        for run in runs:
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            try:
                versions = await store.get_versions(run_id)
                if not versions:
                    continue
                start_events = await store.list_events(run_id, event_type="run_started", limit=1)
                snapshot = {}
                if start_events and isinstance(start_events[0].get("payload"), dict):
                    snapshot = dict(start_events[0].get("payload") or {})
                success_events = await store.list_events(run_id, event_type="version_succeeded", limit=500)

                ordered_job_ids = [
                    str(v.get("job_id") or "")
                    for v in versions
                    if str(v.get("job_id") or "").strip()
                ]
                processed_success_job_ids = {
                    str(
                        ev.get("job_id")
                        or (ev.get("payload") if isinstance(ev.get("payload"), dict) else {}).get("job_id")
                        or ""
                    ).strip()
                    for ev in success_events
                }
                processed_success_job_ids.discard("")
                active_job_ids = [jid for jid in ordered_job_ids if jid not in processed_success_job_ids]
                if not active_job_ids:
                    continue
                completed_job_ids = [jid for jid in ordered_job_ids if jid in processed_success_job_ids]

                used_file_names: List[str] = []
                seen_names: set[str] = set()
                for ev in success_events:
                    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                    for name in list(payload.get("used_file_names") or []):
                        clean = str(name or "").strip()
                        if not clean or clean in seen_names:
                            continue
                        seen_names.add(clean)
                        used_file_names.append(clean)

                chat_id = int(run.get("chat_id") or 0)
                if chat_id <= 0:
                    continue
                st = await self.store.get(chat_id)
                if st.stage == STAGE_PROCESSING and str(st.generation_run_id or "") == run_id:
                    continue
                st.stage = STAGE_PROCESSING
                st.chat_username = str(snapshot.get("chat_username") or st.chat_username or "")
                st.lyrics_text = str(snapshot.get("lyrics_text") or "")
                st.target_fragment = str(snapshot.get("target_fragment") or "")
                st.footage_artist_id = str(snapshot.get("footage_artist_id") or "")
                st.bg_mode = str(snapshot.get("bg_mode") or "footage")
                st.bg_solid_color = str(snapshot.get("bg_solid_color") or "")
                st.subtitles_mode = str(snapshot.get("subtitles_mode") or st.subtitles_mode or "")
                st.user_clip_start_sec = float(snapshot.get("user_clip_start_sec") or 0.0)
                st.user_clip_end_sec = float(snapshot.get("user_clip_end_sec") or 0.0)
                st.generation_run_id = run_id
                st.batch_id = str(run.get("batch_id") or "")
                st.batch_audio_s3_url = str(snapshot.get("audio_s3_url") or "")
                st.batch_total_versions = max(1, int(run.get("versions_total") or len(versions) or 1))
                st.next_version_to_enqueue = max(1, int(run.get("next_version_to_enqueue") or 1))
                st.master_job_id = ordered_job_ids[0] if ordered_job_ids else ""
                st.job_order = ordered_job_ids
                st.used_footage_file_names = used_file_names
                st.active_job_ids = active_job_ids
                st.active_job_id = active_job_ids[0]
                st.completed_job_ids = completed_job_ids
                st.active_job_started_at = time.time()
                st.last_status_msg_at = 0.0
                st.status_message_id = 0
                st.last_status_text = ""
                st.last_backpressure_notice = ""
                st.poll_attempts = 0
                await self.store.set(st)
                log.info(
                    "runtime_processing_state_restored surface=%s chat=%s run_id=%s jobs=%s",
                    self._runtime_surface(),
                    st.chat_id,
                    run_id,
                    active_job_ids,
                )
            except Exception as exc:
                log.warning("runtime_restore_run_failed run_id=%s err=%r", run_id, exc)

    def _allow_archive_for_state(self, st: ChatState) -> bool:
        return _is_username_allowed(
            username=st.chat_username,
            allowlist=tuple(self.settings.artifacts_allowlist or tuple()),
        )

    async def _maybe_grant_referral_bonus_after_generation(self, st: ChatState) -> None:
        """
        Team bot handles referral bonuses in PostgreSQL.
        Public bot keeps a no-op hook for Team/Public parity and future reuse.
        """
        _ = st
        return

    def _allow_maintenance_bypass_username(self, username: str) -> bool:
        return _is_username_allowed(
            username=username,
            allowlist=tuple(self.settings.system_maintenance_bypass_usernames or tuple()),
        )

    def _allow_maintenance_bypass_for_state(self, st: ChatState) -> bool:
        return self._allow_maintenance_bypass_username(str(st.chat_username or ""))

    def _allow_maintenance_bypass_for_message(self, message: Message) -> bool:
        if message.from_user is None:
            return False
        username = str(getattr(message.from_user, "username", "") or "")
        return self._allow_maintenance_bypass_username(username)

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
                api_url = self.telegram_api.method_url(token=token, method="getFile")
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
                file_url = self.telegram_api.file_url(token=token, path=file_path)
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

    def _log_incoming_message(self, message: Message, *, handler: str, stage: str = "") -> None:
        text = str(getattr(message, "text", "") or "")
        log.info(
            "tg_in handler=%s chat=%s username=%s update_id=%s stage=%s kind=%s text=%r",
            handler,
            _message_chat_id(message),
            _message_username(message),
            _message_update_id(message),
            str(stage or ""),
            _message_text_kind(message),
            _compact_text(text, limit=120),
        )

    async def _timed_answer(self, message: Message, text: str, *, op: str, **kwargs):
        chat_id = _message_chat_id(message)
        t0 = time.monotonic()
        try:
            result = await message.answer(text, **kwargs)
        except Exception as exc:
            log.warning(
                "tg_out_failed op=%s method=answer chat=%s dur_ms=%d err=%r",
                op,
                chat_id,
                int((time.monotonic() - t0) * 1000.0),
                exc,
            )
            raise
        log.info(
            "tg_out_ok op=%s method=answer chat=%s dur_ms=%d",
            op,
            chat_id,
            int((time.monotonic() - t0) * 1000.0),
        )
        return result

    async def _timed_send_photo(self, *, bot: Bot, chat_id: int, photo: Any, op: str, **kwargs):
        t0 = time.monotonic()
        try:
            result = await bot.send_photo(chat_id, photo=photo, **kwargs)
        except Exception as exc:
            log.warning(
                "tg_out_failed op=%s method=send_photo chat=%s dur_ms=%d err=%r",
                op,
                int(chat_id),
                int((time.monotonic() - t0) * 1000.0),
                exc,
            )
            raise
        log.info(
            "tg_out_ok op=%s method=send_photo chat=%s dur_ms=%d",
            op,
            int(chat_id),
            int((time.monotonic() - t0) * 1000.0),
        )
        return result

    async def _timed_send_video(self, *, bot: Bot, chat_id: int, video: Any, op: str, **kwargs):
        t0 = time.monotonic()
        try:
            result = await bot.send_video(chat_id, video=video, **kwargs)
        except Exception as exc:
            log.warning(
                "tg_out_failed op=%s method=send_video chat=%s dur_ms=%d err=%r",
                op,
                int(chat_id),
                int((time.monotonic() - t0) * 1000.0),
                exc,
            )
            raise
        log.info(
            "tg_out_ok op=%s method=send_video chat=%s dur_ms=%d",
            op,
            int(chat_id),
            int((time.monotonic() - t0) * 1000.0),
        )
        return result

    async def _maintenance_enabled(self) -> bool:
        if bool(self.settings.tg_maintenance_mode):
            return True
        key = str(self.settings.tg_maintenance_state_key or "").strip()
        if key and await self.store.get_runtime_bool(key, default=False):
            return True
        startup_key = self._startup_maintenance_state_key()
        if not startup_key:
            return False
        return await self.store.get_runtime_bool(startup_key, default=False)

    def _maintenance_message_text(self) -> str:
        txt = str(self.settings.tg_maintenance_message or "").strip()
        if txt:
            return txt
        return "Мы на техработах. Скоро вернемся."

    def _startup_maintenance_state_key(self) -> str:
        base = str(getattr(self.settings, "tg_startup_maintenance_state_key", "") or "").strip()
        if not base:
            return ""
        node_id = str(getattr(self.settings, "tg_processing_node_id", "") or "").strip() or "unknown-node"
        return f"{base}:{node_id}"

    async def _set_startup_maintenance_enabled(self, enabled: bool, *, ttl_s: int = 0) -> None:
        startup_key = self._startup_maintenance_state_key()
        if not startup_key:
            return
        await self.store.set_runtime_bool(startup_key, bool(enabled), ttl_s=max(0, int(ttl_s or 0)))

    async def _startup_dependencies_status(self) -> tuple[bool, List[str]]:
        issues: List[str] = []

        try:
            health = await self.orchestrator.get_health()
            if not bool(health.get("ok")):
                issues.append("orchestrator_health_not_ok")
            checks = health.get("checks") if isinstance(health.get("checks"), dict) else {}
            if isinstance(checks, dict):
                if checks.get("bundle_ready") is False:
                    issues.append("bundle_ready=false")
                if checks.get("llm_admission_ready") is False:
                    issues.append("llm_admission_ready=false")
                if checks.get("payment_db_ready") is False:
                    issues.append("payment_db_ready=false")
        except Exception as exc:
            issues.append(f"health_error={type(exc).__name__}")

        try:
            llm_workers = await self.orchestrator.get_llm_workers()
            workers = llm_workers.get("workers") if isinstance(llm_workers.get("workers"), dict) else {}
            useful_capacity = False
            for row in workers.values():
                if not isinstance(row, dict):
                    continue
                if bool(row.get("enabled")) and int(row.get("weight", 0) or 0) > 0 and int(row.get("max_inflight", 0) or 0) > 0:
                    useful_capacity = True
                    break
            if not useful_capacity:
                issues.append("llm_workers_not_ready")
        except Exception as exc:
            issues.append(f"llm_workers_error={type(exc).__name__}")

        try:
            windows_nodes = await self.orchestrator.get_windows_nodes()
            effective_urls = windows_nodes.get("effective_urls")
            if not isinstance(effective_urls, list) or not [str(x).strip() for x in effective_urls if str(x).strip()]:
                issues.append("windows_nodes_empty")
        except Exception as exc:
            issues.append(f"windows_nodes_error={type(exc).__name__}")

        return (len(issues) == 0), issues

    async def _startup_auto_maintenance_loop(self) -> None:
        poll_s = max(1.0, float(getattr(self.settings, "tg_startup_maintenance_poll_s", 5.0) or 5.0))
        timeout_s = max(30.0, float(getattr(self.settings, "tg_startup_maintenance_timeout_s", 600.0) or 600.0))
        ttl_s = max(60, int(timeout_s + poll_s * 3.0))
        deadline = time.monotonic() + timeout_s
        timeout_alert_sent = False
        try:
            while True:
                await self._set_startup_maintenance_enabled(True, ttl_s=ttl_s)
                ready, issues = await self._startup_dependencies_status()
                if ready:
                    await self._set_startup_maintenance_enabled(False)
                    await self._notify_ops_alert(
                        title="Public bot startup maintenance cleared",
                        extra_lines=[
                            f"node: {str(self.settings.tg_processing_node_id or '').strip() or 'unknown-node'}",
                            f"delivery_mode: {str(self.settings.tg_delivery_mode or '').strip() or 'polling'}",
                        ],
                    )
                    return

                if (not timeout_alert_sent) and time.monotonic() >= deadline:
                    timeout_alert_sent = True
                    await self._notify_ops_alert(
                        title="Public bot startup maintenance still active",
                        extra_lines=[f"issues: {', '.join(issues[:5])}" if issues else "issues: unknown"],
                    )

                await asyncio.sleep(poll_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("startup_auto_maintenance_loop_failed err=%r", exc)
            await self._notify_ops_alert(
                title="Public bot startup maintenance loop failed",
                extra_lines=[f"node: {str(self.settings.tg_processing_node_id or '').strip() or 'unknown-node'}"],
                error_text=repr(exc),
            )

    async def _allow_maintenance_bypass_paid_client(self, message: Message) -> bool:
        """Платящие клиенты не попадают под техработы — если это не полный стоп."""
        if not bool(getattr(self.settings, "tg_maintenance_allow_paid_clients", True)):
            return False
        chat_id = _message_chat_id(message)
        if chat_id <= 0:
            return False
        try:
            return await self.credits_db.has_paid(chat_id)
        except Exception as e:
            log.warning("maintenance_paid_client_check_failed chat=%s err=%r", chat_id, e)
            return False

    async def _maybe_reply_maintenance_stub(self, message: Message) -> bool:
        try:
            enabled = await self._maintenance_enabled()
        except Exception as e:
            log.warning("maintenance_gate_check_failed err=%r", e)
            enabled = bool(self.settings.tg_maintenance_mode)
        if not enabled:
            return False
        if self._allow_maintenance_bypass_for_message(message):
            return False
        if await self._allow_maintenance_bypass_paid_client(message):
            return False
        await message.answer(self._maintenance_message_text())
        return True

    def _register_handlers(self) -> None:
        @self.router.my_chat_member()
        async def _on_my_chat_member(event: ChatMemberUpdated) -> None:
            new_status = event.new_chat_member.status
            if new_status in ("kicked", "left"):
                chat_id = int(event.chat.id)
                await self.credits_db.log_event(chat_id, "bot_blocked", new_status)

        @self.router.message(CommandStart())
        async def _on_start(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            self._log_incoming_message(message, handler="start")
            if await self._maybe_reply_maintenance_stub(message):
                return
            st = await self.store.get(chat_id)
            self._log_incoming_message(message, handler="start_state", stage=st.stage)
            user_changed = self._sync_state_user_from_message(st, message)
            if user_changed:
                await self.store.set(st)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            # Season flow gate — parity-mirrored from tg_bot_botapi. When
            # SEASON_FLOW_ENABLED is off (default) this is a no-op and the
            # standard onboarding path below runs unchanged.
            if _should_route_to_season(st):
                log.info(
                    "season_flow_routed chat=%s stage=%s — falling back to "
                    "standard onboarding (handlers not yet wired in public).",
                    chat_id, st.stage,
                )

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

        @self.router.message(Command("packets", "packages", "package"))
        async def _on_packets(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            self._log_incoming_message(message, handler="packages_command")
            if await self._maybe_reply_maintenance_stub(message):
                return
            st = await self.store.get(chat_id)
            self._log_incoming_message(message, handler="packages_command_state", stage=st.stage)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.\nПакеты можно посмотреть после.")
                return
            await self._show_all_packages(message, st)

        @self.router.message(Command("rustgen", "ae"))
        async def _on_render_engine(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            command = str(message.text or "").split(maxsplit=1)[0].split("@", 1)[0].lower()
            st = await self.store.get(chat_id)
            if command == "/rustgen":
                allowed = set(self.settings.rust_gen_allowed_chat_ids or frozenset())
                if chat_id not in allowed:
                    return
                st.render_engine = "rust-gen"
                await self.store.set(st)
                await message.answer("Native renderer selected for this chat.")
                return
            st.render_engine = "ae"
            await self.store.set(st)
            await message.answer("AE renderer selected for this chat.")

        @self.router.message(Command("freeflow1"))
        async def _on_freeflow1(message: Message) -> None:
            # Hidden admin-only helper: jump straight into the free post-gen
            # funnel entry (round-1 rating) so it can be reviewed without a real
            # generation or the has_paid gate. Not in the menu; silently ignored
            # for non-admins.
            if message.chat is None or message.from_user is None:
                return
            uid = int(message.from_user.id)
            is_owner = uid == self.settings.manager_chat_id
            if not (is_owner or await self.credits_db.is_admin(uid)):
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе — дождись завершения, потом зови /freeflow1.")
                return
            st.video_round = 1
            st.last_rating = ""
            st.stage = STAGE_RATE_VIDEO
            await self.store.set(st)
            await message.answer(
                "🧪 Тест free-флоу: ты в точке входа фаннела (как будто ролик "
                "только что выдан). Оцени по 10-балльной шкале — дальше пойдёт "
                "обычный бесплатный сценарий.",
                reply_markup=_kb(BTN_RATE_BUTTONS),
            )

        async def _warmup_admin(message: Message) -> bool:
            if message.from_user is None:
                return False
            uid = int(message.from_user.id)
            return uid == self.settings.manager_chat_id or await self.credits_db.is_admin(uid)

        async def _send_warmup_stage(chat_id: int, stage: int, *, is_test: bool) -> None:
            bot = self._require_bot()
            await bot.send_message(
                chat_id,
                warmup_message(stage),
                reply_markup=warmup_keyboard(stage, is_test=is_test),
            )
            await self.credits_db.advance_warmup_stage(
                WARMUP_CAMPAIGN, chat_id, stage, is_test=is_test,
            )

        @self.router.message(Command("warmup_test"))
        async def _on_warmup_test(message: Message) -> None:
            """Admin-only, sends the full chain entry only to the caller."""
            if message.chat is None or not await _warmup_admin(message):
                return
            await _send_warmup_stage(int(message.chat.id), 1, is_test=True)

        @self.router.message(Command("warmup_send"))
        async def _on_warmup_send(message: Message) -> None:
            """Explicit production launch. Never runs without the literal CONFIRM."""
            if message.chat is None or not await _warmup_admin(message):
                return
            command_parts = (message.text or "").strip().split(maxsplit=1)
            confirmed = len(command_parts) == 2 and command_parts[1].strip().upper() == "CONFIRM"
            if not confirmed:
                await message.answer("Боевой запуск: /warmup_send CONFIRM\nСначала проверь /warmup_test.")
                return
            audience = await self.credits_db.resolve_audience({"mode": "all", "exclude_admins": True})

            async def _run() -> None:
                for tg_id in audience:
                    try:
                        await _send_warmup_stage(tg_id, 1, is_test=False)
                    except Exception as exc:
                        log.warning("warmup_stage_1_failed chat=%s err=%r", tg_id, exc)
                    await asyncio.sleep(0.05)

            asyncio.create_task(_run(), name="warmup_broadcast_stage_1")
            await message.answer(f"Запущена рассылка сообщения 1: аудитория {len(audience)}. /warmup_stats — конверсия.")

        @self.router.message(Command("warmup_stats"))
        async def _on_warmup_stats(message: Message) -> None:
            if message.chat is None or not await _warmup_admin(message):
                return
            prod = await self.credits_db.warmup_stats(WARMUP_CAMPAIGN, is_test=False)
            test = await self.credits_db.warmup_stats(WARMUP_CAMPAIGN, is_test=True)
            def _line(label: str, value: Dict[int, int]) -> str:
                base = value[1]
                pct2 = (100 * value[2] / base) if base else 0
                pct3 = (100 * value[3] / base) if base else 0
                return f"{label}: 1={base}, 2={value[2]} ({pct2:.1f}%), 3={value[3]} ({pct3:.1f}%)"
            await message.answer("Прогревочная цепочка\n" + _line("Прод", prod) + "\n" + _line("Тест", test))

        @self.router.callback_query(lambda c: c.data and c.data.startswith(WARMUP_CALLBACK_PREFIX))
        async def _on_warmup_callback(callback: CallbackQuery) -> None:
            if callback.message is None or callback.message.chat is None:
                return
            try:
                _, mode, raw_stage = str(callback.data).split(":", 2)
                is_test = mode == "test"
                stage = int(raw_stage)
            except (TypeError, ValueError):
                await callback.answer("Некорректная кнопка.", show_alert=True)
                return
            if mode not in {"test", "prod"} or stage not in {2, 3}:
                await callback.answer("Некорректная кнопка.", show_alert=True)
                return
            chat_id = int(callback.message.chat.id)
            current = await self.credits_db.warmup_stage(WARMUP_CAMPAIGN, chat_id, is_test=is_test)
            if current != stage - 1:
                await callback.answer("Этот шаг уже пройден или ещё не доступен.", show_alert=True)
                return
            await _send_warmup_stage(chat_id, stage, is_test=is_test)
            await callback.answer()

        @self.router.message(Command("freeflow2"))
        async def _on_freeflow2(message: Message) -> None:
            # Hidden admin-only helper: jump into the round-2 funnel entry
            # (STAGE_RATE_VIDEO_2 — the "friend activated → 2nd video" branch),
            # so the later part of the free flow can be reviewed. Not in the menu.
            if message.chat is None or message.from_user is None:
                return
            uid = int(message.from_user.id)
            is_owner = uid == self.settings.manager_chat_id
            if not (is_owner or await self.credits_db.is_admin(uid)):
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе — дождись завершения, потом зови /freeflow2.")
                return
            st.video_round = 2
            st.last_rating = ""
            st.stage = STAGE_RATE_VIDEO_2
            await self.store.set(st)
            await message.answer(
                "🧪 Тест free-флоу (раунд 2): ты в точке входа второго ролика "
                "(как будто друг подписался и пришёл 2-й ролик). Оцени по "
                "10-балльной шкале — дальше пойдёт обычный сценарий раунда 2.",
                reply_markup=_kb(BTN_RATE_BUTTONS),
            )

        @self.router.message(Command("cancelsubscription"))
        async def _on_cancel_subscription(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            await self.credits_db.log_event(chat_id, "cancel_subscription_request")
            active_sub = await self.credits_db.get_active_subscription(chat_id)
            if not active_sub:
                await message.answer(
                    "Активной подписки нет. Если ты считаешь, что это ошибка — "
                    "напиши @impulsemanage.\n\n/packages — посмотреть тарифы",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return
            pkg = active_sub.get("package", "")
            nc = active_sub.get("next_charge_at")
            nc_str = nc.strftime("%d.%m.%Y") if hasattr(nc, "strftime") else "—"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Да, отменить подписку",
                        callback_data="cancel_sub:confirm",
                    )],
                    [InlineKeyboardButton(
                        text="Передумал, оставить",
                        callback_data="cancel_sub:abort",
                    )],
                ]
            )
            await message.answer(
                f"Сейчас активна подписка «{pkg}». Следующее списание было бы {nc_str}.\n\n"
                "Если отменишь:\n"
                "• С тебя больше ничего не спишется.\n"
                "• Накопленные кредиты остаются — можешь использовать в любое время.\n"
                "• Чтобы возобновиться, нужно оформить подписку заново через /packages.\n\n"
                "Точно отменяем?",
                reply_markup=kb,
            )

        @self.router.callback_query(lambda c: c.data and c.data.startswith("cancel_sub:"))
        async def _on_cancel_subscription_choice(callback: CallbackQuery) -> None:
            if callback.message is None or callback.message.chat is None:
                return
            chat_id = int(callback.message.chat.id)
            choice = str(callback.data or "").split(":", 1)[1] if callback.data else ""
            await callback.answer()
            if choice == "abort":
                try:
                    await callback.message.edit_text("Хорошо, подписка остаётся активной.")
                except Exception:
                    await callback.message.answer("Хорошо, подписка остаётся активной.")
                return
            if choice != "confirm":
                return
            cancelled = await self.credits_db.cancel_subscription(chat_id)
            if cancelled:
                await self.credits_db.log_event(
                    chat_id, "subscription_cancelled", "user_request",
                )
                try:
                    await callback.message.edit_text(
                        "Подписка отменена. Списаний больше не будет.\n\n"
                        "Если захочешь вернуться — /packages.",
                    )
                except Exception:
                    await callback.message.answer(
                        "Подписка отменена. Списаний больше не будет.",
                    )
                # Notify manager so they're in the loop.
                if self.settings.manager_chat_id:
                    try:
                        user_info = await self.credits_db.get_user(chat_id)
                        uname = (
                            f"@{user_info['username']}"
                            if user_info and user_info.get("username") else str(chat_id)
                        )
                        await self._require_bot().send_message(
                            self.settings.manager_chat_id,
                            f"⛔ Подписка отменена\n\nПользователь: {uname}\ntg_id: {chat_id}",
                        )
                    except Exception as e:
                        log.warning("cancel_sub manager notify failed: %s", e)
            else:
                try:
                    await callback.message.edit_text(
                        "Активной подписки не нашёл. Если что-то не так — @impulsemanage.",
                    )
                except Exception:
                    await callback.message.answer(
                        "Активной подписки не нашёл. Напиши @impulsemanage.",
                    )

        @self.router.message(Command("sendtrack"))
        async def _on_sendtrack(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            self._log_incoming_message(message, handler="sendtrack_command")
            if await self._maybe_reply_maintenance_stub(message):
                return
            st = await self.store.get(chat_id)
            self._log_incoming_message(message, handler="sendtrack_command_state", stage=st.stage)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return
            await self._move_to_wait_audio(chat_id, message)

        @self.router.callback_query(lambda c: c.data and c.data.startswith("improve:"))
        async def _on_improve_callback(callback: CallbackQuery) -> None:
            if callback.message is None or callback.message.chat is None:
                return
            chat_id = int(callback.message.chat.id)
            st = await self.store.get(chat_id)
            area = str(callback.data or "").replace("improve:", "")
            await callback.answer()

            await self.credits_db.log_event(
                chat_id, "improvement_feedback",
                f"rating=5-6 area={area}",
            )

            if area == "other":
                st.stage = STAGE_IMPROVEMENT_OTHER_TEXT
                await self.store.set(st)
                await callback.message.answer(
                    "Напиши, что бы изменил — мы учтём.",
                    reply_markup=ReplyKeyboardRemove(),
                )
            else:
                await self._send_improvement_thanks(callback.message, st)

        @self.router.callback_query(lambda c: c.data == "sendtrack")
        async def _on_sendtrack_callback(callback: CallbackQuery) -> None:
            if callback.message is None or callback.message.chat is None:
                return
            chat_id = int(callback.message.chat.id)
            await callback.answer()
            await self._move_to_wait_audio(chat_id, callback.message)

        @self.router.callback_query(lambda c: c.data and c.data.startswith(VIBE_CB_PREFIX))
        async def _on_vibe_callback(callback: CallbackQuery) -> None:
            # Phase 2b footage precision flow (mirror of tg_bot_botapi). The
            # methods are gated upstream by FOOTAGE_VIBE_FLOW_ENABLED (the picker
            # is only ever shown when the flag is on), and the handler no-ops on
            # any chat not parked on STAGE_WAIT_VIBE.
            await self._handle_vibe_callback(callback)

        @self.router.message(Command("bigtest"))
        async def _on_bigtest(message: Message) -> None:
            # Parity stub — /bigtest is available only on the team bot.
            await message.answer("Эта команда недоступна.")

        @self.router.message()
        async def _on_any_message(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            if await self._maybe_reply_maintenance_stub(message):
                return
            st = await self.store.get(chat_id)
            user_changed = self._sync_state_user_from_message(st, message)
            if user_changed:
                await self.store.set(st)
            self._log_incoming_message(message, handler="message", stage=st.stage)

            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            if _is_packages_command_text(str(message.text or "")):
                await self._show_all_packages(message, st)
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

            if st.stage == STAGE_WAIT_BG_MODE:
                await self._handle_wait_bg_mode(message, st)
                return

            if st.stage == STAGE_WAIT_BG_INFO:
                await self._handle_wait_bg_info(message, st)
                return

            if st.stage == STAGE_WAIT_BG_COLOR:
                await self._handle_wait_bg_color(message, st)
                return

            if st.stage == STAGE_WAIT_STROBE_CUT:
                await self._handle_wait_strobe_cut(message, st)
                return

            if st.stage == STAGE_WAIT_FOOTAGE_GENRE:
                await self._handle_wait_footage_genre(message, st)
                return

            if st.stage == STAGE_WAIT_FOOTAGE_ARTIST:
                await self._handle_wait_footage_artist(message, st)
                return

            if st.stage == STAGE_WAIT_VIBE:
                await self._handle_wait_vibe_text(message, st)
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

            # Customization color + hook flow (behind HOOK_FLOW_ENABLED).
            if st.stage == STAGE_WAIT_SUBTITLE_COLOR:
                await self._handle_wait_subtitle_color(message, st)
                return
            if st.stage == STAGE_WAIT_ACCENT_COLOR:
                await self._handle_wait_accent_color(message, st)
                return
            if st.stage == STAGE_WAIT_HOOK_CHOICE:
                await self._handle_wait_hook_choice(message, st)
                return
            if st.stage == STAGE_WAIT_HOOK_DROP:
                await self._handle_wait_hook_drop(message, st)
                return
            if st.stage == STAGE_WAIT_HOOK_DROP_MANUAL:
                await self._handle_wait_hook_drop_manual(message, st)
                return
            if st.stage == STAGE_WAIT_HOOK_TYPE:
                await self._handle_wait_hook_type(message, st)
                return
            if st.stage == STAGE_WAIT_HOOK_DEVICE:
                await self._handle_wait_hook_device(message, st)
                return
            if st.stage == STAGE_WAIT_EFFECT_HOOK:
                await self._handle_wait_effect_hook(message, st)
                return
            if st.stage == STAGE_WAIT_EFFECT_TRANSITION:
                await self._handle_wait_effect_transition(message, st)
                return
            if st.stage == STAGE_WAIT_EFFECT_EXTRA_FULL:
                await self._handle_wait_effect_extra_full(message, st)
                return
            if st.stage == STAGE_WAIT_EFFECT_EXTRA:
                await self._handle_wait_effect_extra(message, st)
                return
            if st.stage == STAGE_WAIT_VISUAL_TRANSITION:
                await self._handle_wait_visual_transition(message, st)
                return
            if st.stage == STAGE_WAIT_VISUAL_STYLE:
                await self._handle_wait_visual_style(message, st)
                return
            if st.stage == STAGE_WAIT_EFFECT_EXTEND:
                await self._handle_wait_effect_extend(message, st)
                return
            if st.stage == STAGE_WAIT_F2_SHAPE:
                await self._handle_wait_f2_shape(message, st)
                return
            if st.stage == STAGE_WAIT_PHOTO_STYLE:
                await self._handle_wait_photo_style(message, st)
                return
            if st.stage == STAGE_WAIT_PHOTO_TRANSITION:
                await self._handle_wait_photo_transition(message, st)
                return
            if st.stage == STAGE_WAIT_F1_SOUND:
                await self._handle_wait_f1_sound(message, st)
                return
            if st.stage == STAGE_WAIT_F1_TEXT:
                await self._handle_wait_f1_text(message, st)
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
                STAGE_IMPROVEMENT_FEEDBACK: self._handle_improvement_feedback_text,
                STAGE_IMPROVEMENT_OTHER_TEXT: self._handle_improvement_other_text,
                STAGE_SALES_PITCH: self._handle_sales_pitch,
                STAGE_PACKAGES_OFFER: self._handle_packages_offer,
                STAGE_PACKAGE_DETAILS: self._handle_package_details,
                STAGE_ALL_PACKAGES: self._handle_all_packages,
                STAGE_PACKAGE_INFO: self._handle_package_info,
                STAGE_PURCHASE_CHOICE: self._handle_purchase_choice,
                STAGE_SUBSCRIPTION_CONFIRM: self._handle_subscription_confirm,
                STAGE_WAIT_PAYMENT: self._handle_wait_payment,
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
        self.runtime_store = GenerationRuntimeStore(self.credits_db._pool_or_fail())
        await self.runtime_store.init_schema()

        # Set bot menu commands
        await bot.set_my_commands([
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="sendtrack", description="Отправить трек"),
            # /packets stays a valid alias (handler still accepts it) but is
            # dropped from the menu — it duplicated /packages.
            BotCommand(command="packages", description="Посмотреть тарифы"),
            BotCommand(command="cancelsubscription", description="Отменить подписку"),
        ])

        # Start admin web panel as background task
        self._admin_panel_task = asyncio.create_task(
            start_admin_panel(
                self.credits_db, self.store, self.settings,
                tbank_client=self.tbank, bot_ref=self._bot_ref,
                bot_app=self,
            ),
            name="admin_panel",
        )

        # Broadcast + lifecycle workers
        bc_task, lc_task, bc_stop = await start_broadcast_workers(
            self.credits_db, self._bot_ref,
        )
        self._broadcast_task = bc_task
        self._lifecycle_task = lc_task
        self._broadcast_stop = bc_stop

        self._processing_task = asyncio.create_task(self._processing_loop(), name="tg_bot_processing_loop")
        self._recovery_task = asyncio.create_task(self._recovery_loop(), name="tg_bot_recovery_loop")
        self._reminder_task = asyncio.create_task(self._reminder_loop(), name="tg_bot_reminder_loop")
        self._payment_poll_task = asyncio.create_task(self._payment_poll_loop(), name="tg_bot_payment_poll")
        self._state_cleanup_task = asyncio.create_task(self._state_cleanup_loop(), name="tg_bot_state_cleanup_loop")
        self._fs_cleanup_task = asyncio.create_task(self._fs_cleanup_loop(), name="tg_bot_fs_cleanup_loop")
        self._subscription_charge_task = asyncio.create_task(self._subscription_charge_loop(), name="tg_bot_subscription_charge")
        await self._restore_runtime_processing_states()
        self._outbox_task = asyncio.create_task(self._runtime_outbox_loop(), name="tg_bot_outbox_dispatcher")
        if bool(getattr(self.settings, "tg_auto_startup_maintenance", False)) and not bool(self.settings.tg_maintenance_mode):
            await self._set_startup_maintenance_enabled(
                True,
                ttl_s=max(60, int(float(getattr(self.settings, "tg_startup_maintenance_timeout_s", 600.0) or 600.0) + 30.0)),
            )
            self._startup_maintenance_task = asyncio.create_task(
                self._startup_auto_maintenance_loop(),
                name="tg_bot_startup_maintenance_loop",
            )
        else:
            await self._set_startup_maintenance_enabled(False)
        log.info("startup complete: background workers started")

    async def _on_shutdown(self, bot: Bot) -> None:
        del bot
        stop_fn = getattr(self, "_broadcast_stop", None)
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception:
                pass
        for task in [
            self._processing_task,
            self._recovery_task,
            self._state_cleanup_task,
            self._fs_cleanup_task,
            getattr(self, "_reminder_task", None),
            getattr(self, "_admin_panel_task", None),
            getattr(self, "_payment_poll_task", None),
            getattr(self, "_subscription_charge_task", None),
            getattr(self, "_broadcast_task", None),
            getattr(self, "_lifecycle_task", None),
            getattr(self, "_startup_maintenance_task", None),
            getattr(self, "_outbox_task", None),
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
        self.runtime_store = None
        self._bot = None
        log.info("shutdown complete")

    async def _move_to_onboarding(self, chat_id: int, message: Message) -> None:
        await self.store.set_stage(chat_id, STAGE_WAIT_START)
        welcome_text = (
            "Добро пожаловать! Это Blast — co-pilot в продвижении музыки.\n\n"
            "Наш AI-агент помогает артистам развивать контент: создавать, "
            "выкладывать и анализировать ролики с нуля.\n\n"
            "Готов затестить его на своём треке? Тогда жми на кнопку «Едем!», "
            "чтобы продолжить."
        )
        try:
            await self._timed_send_video(
                bot=message.bot,
                chat_id=chat_id,
                video=ONBOARDING_VIDEO_FILE_ID,
                op="onboarding_video",
                caption=welcome_text,
                reply_markup=_kb([BTN_LETS_GO]),
            )
        except Exception:
            # Fallback to plain text if the reel can't be sent (e.g. bad file_id).
            await self._timed_answer(message, welcome_text, op="onboarding_text", reply_markup=_kb([BTN_LETS_GO]))

    async def _move_to_subscription(self, chat_id: int, message: Message) -> None:
        await self.store.set_stage(chat_id, STAGE_WAIT_SUBSCRIPTION)
        await self._timed_answer(
            message,
            "Супер! Тогда не будем медлить, единственное условие — подписка на наш тгк: @impulsemarketing\n\n"
            "Там делимся главными фишками по продукту и продвижению, которые помогают эффективно вести контент артисту.",
            op="subscription_prompt",
            reply_markup=_kb([BTN_SUBSCRIBED]),
        )

    async def _check_subscription(self, user_id: int) -> bool:
        if bool(getattr(self.settings, "tg_test_bypass_subscription", False)):
            log.info("subscription_check_bypassed_for_telegram_test_env user_id=%s", user_id)
            return True
        bot = self._require_bot()
        t0 = time.monotonic()
        try:
            member = await bot.get_chat_member(
                chat_id=self.settings.subscription_channel,
                user_id=user_id,
            )
            log.info(
                "tg_api_ok op=subscription_check method=get_chat_member user_id=%s status=%s dur_ms=%d",
                user_id,
                str(getattr(member, "status", "")),
                int((time.monotonic() - t0) * 1000.0),
            )
            return member.status in {"member", "administrator", "creator"}
        except Exception as e:
            log.warning(
                "tg_api_failed op=subscription_check method=get_chat_member user_id=%s dur_ms=%d err=%s",
                user_id,
                int((time.monotonic() - t0) * 1000.0),
                e,
            )
            return False

    async def _handle_wait_start(self, message: Message, st: ChatState) -> None:
        if str(message.text or "").strip() == BTN_LETS_GO:
            await self._move_to_subscription(int(message.chat.id), message)
        else:
            await message.answer("Нажми «Едем!», чтобы продолжить.", reply_markup=_kb([BTN_LETS_GO]))

    async def _handle_wait_subscription(self, message: Message, st: ChatState) -> None:
        if str(message.text or "").strip() != BTN_SUBSCRIBED:
            await self._timed_answer(
                message,
                "Нажми «Подписался!», когда оформишь подписку.",
                op="subscription_button_reminder",
                reply_markup=_kb([BTN_SUBSCRIBED]),
            )
            return
        user_id = int(message.from_user.id) if message.from_user else 0
        subscribed = await self._check_subscription(user_id)
        if not subscribed:
            await self._timed_answer(message, "Думаешь, мы не будем проверять подписку?)", op="subscription_not_ok")
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
                # Grant the free unique-track slot alongside the video credits
                # (guarded by the same has_initial_grant check so it fires once).
                if self.settings.initial_track_credits > 0:
                    await self.credits_db.add_track_credits(
                        chat_id, self.settings.initial_track_credits, "initial_grant",
                    )
        await self._move_to_wait_audio(chat_id, message)

    async def _move_to_wait_audio(self, chat_id: int, message: Message) -> None:
        await self.store.reset_to_wait_audio(chat_id)
        bal = await self.credits_db.get_balance(chat_id)
        track_bal = await self.credits_db.get_track_balance(chat_id)
        bal_text = f"\n\nДоступно генераций: {bal}" if bal > 0 else ""
        if track_bal > 0:
            bal_text += f"\nДоступно уникальных треков: {track_bal}"
        await self._timed_answer(
            message,
            f"Привет. Отправь трек аудио-файлом, и я соберу клип.{bal_text}",
            op="wait_audio_prompt",
            reply_markup=_kb([BTN_SEND_TRACK]),
        )

    @staticmethod
    def _can_reuse_input(st: ChatState) -> bool:
        if str(st.pending_audio_file_id or "").strip():
            return True
        prepared_raw = str(st.prepared_audio_local_path or "").strip()
        if not prepared_raw:
            return False
        try:
            return Path(prepared_raw).expanduser().resolve().exists()
        except Exception:
            return False

    @staticmethod
    def _wait_audio_reuse_kb() -> ReplyKeyboardMarkup:
        return _kb([BTN_SEND_TRACK], [BTN_REUSE_INPUT])

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
    def _esc_md(text: str) -> str:
        """Escape Telegram Markdown v1 special chars in user-supplied strings."""
        for ch in ("*", "_", "`", "["):
            text = text.replace(ch, f"\\{ch}")
        return text

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
        return "весь трек"

    def _source_label(self, st: ChatState) -> str:
        artist_id = str(st.footage_artist_id or "").strip()
        if not artist_id:
            return "по треку"
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

    @staticmethod
    def _has_forced_alignment_reference_text(st: ChatState) -> bool:
        return bool(str(st.lyrics_text or st.target_fragment or "").strip())

    async def _ask_timing_choice(self, message: Message, st: ChatState) -> None:
        # No fork: the "let AI decide" option was removed — the timing is now
        # always user-supplied. Go straight to the input step.
        st.stage = STAGE_WAIT_TIMING_INPUT
        st.user_clip_start_sec = 0.0
        st.user_clip_end_sec = 0.0
        await self.store.set(st)
        await message.answer(
            "Укажи конкретный тайминг трека для клипа следующим образом: "
            "1:20-1:50 (минуты:секунды).\n\n"
            "Проверь, что отрывок текста и тайминг — сходятся.\n\n"
            "<b>Максимальный тайминг: 15с.</b> Это строгое ограничение — если "
            "поставишь больше, задача вернётся с ошибкой и придётся заполнять "
            "заново.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
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
        # Hard cap is 22s (buffer for the hook scenario); the UI communicates a
        # stricter 15s so users aim short. Deliberate: 15–22s still passes, only
        # past 22s hard-fails. Keep the user-facing number at 15.
        if duration > 22.0:
            await message.answer(
                "Слишком длинный фрагмент (максимум 15 сек) — укажи отрезок "
                "покороче, например 1:20-1:33."
            )
            return
        st.user_clip_start_sec = round(start_sec, 3)
        st.user_clip_end_sec = round(end_sec, 3)
        await self.store.set(st)
        # Pre-warm hook drop/bpm analysis (fire-and-forget) so the picker has
        # candidates ready by the hook step. Self-skips when no focus clip.
        if HOOK_FLOW_ENABLED:
            await self._trigger_hook_analysis_task(st)
        await message.answer(
            f"Тайминг установлен: {self._fmt_timing(start_sec)} - {self._fmt_timing(end_sec)} ({duration:.0f} сек)."
        )
        await self._ask_bg_mode(message, st)

    async def _ask_bg_mode(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_BG_MODE
        st.bg_mode = "footage"
        st.bg_solid_color = ""
        await self.store.set(st)
        # Phase 2b: offer a "pictures (soon)" stub next to footage/solid when the
        # vibe flow is on so the menu matches the target UX. The stub replies "скоро".
        rows = [[BTN_BG_FOOTAGE], [BTN_BG_SOLID], [BTN_BG_STROBE]]
        # Photo flow (4:3): a real "Картинки" button when PHOTO_FLOW_ENABLED;
        # otherwise the "(скоро)" stub if the vibe flow is on. Mirror of team.
        if PHOTO_FLOW_ENABLED:
            rows.append([BTN_BG_PICTURES_PHOTO])
        elif FOOTAGE_VIBE_FLOW_ENABLED:
            rows.append([BTN_BG_PICTURES])
        rows.append([BTN_BACK])
        await message.answer("Что будет на фоне?", reply_markup=_kb(*rows))

    async def _ask_bg_info(self, message: Message, st: ChatState, mode: str) -> None:
        st.stage = STAGE_WAIT_BG_INFO
        st.pending_bg_mode = mode
        await self.store.set(st)
        texts = {
            "footage": (
                "Футажи\n\nПодберём фоновые видео под настроение трека. "
                "Можно выбрать несколько вайбов — бот будет чередовать их в роликах.\n\n"
                "Дальше покажем варианты с превью."
            ),
            "solid": (
                "Цветной фон\n\nВесь ролик будет на одном цвете: без видео на фоне, "
                "только музыка и субтитры.\n\nДальше выбери цвет."
            ),
            "solid_strobe": (
                "Строб Ч/Б\n\nФон будет переключаться между чёрным и белым на склейках. "
                "Текст автоматически меняет цвет и остаётся читаемым.\n\n"
                "Дальше настроим субтитры и стиль переходов."
            ),
            "photo": (
                "Картинки\n\nПодберём изображения под трек и соберём из них ролик. "
                "Потом можно выбрать обработку и переход между картинками.\n\n"
                "Дальше покажем варианты с превью."
            ),
        }
        await message.answer(texts[mode], reply_markup=_kb([BTN_BG_INFO_NEXT], [BTN_BACK]))

    async def _handle_wait_bg_info(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            st.pending_bg_mode = ""
            await self._ask_bg_mode(message, st)
            return
        if text != BTN_BG_INFO_NEXT:
            await message.answer("Выбери «Продолжить» или «Назад».")
            return
        mode = str(st.pending_bg_mode or "")
        st.pending_bg_mode = ""
        st.bg_mode = mode
        st.bg_solid_color = ""
        await self.store.set(st)
        if mode == "footage":
            if FOOTAGE_VIBE_FLOW_ENABLED:
                await self._ask_vibe_shortlist(message, st)
            else:
                await self._ask_footage_genre(message, st)
            return
        if mode == "solid":
            await self._ask_bg_color(message, st)
            return
        if mode == "solid_strobe":
            if not self._ensure_solid_default_artist(st):
                await message.answer("Внутренняя ошибка при выборе фона. Попробуй ещё раз позже.")
                return
            await self.store.set(st)
            await self._ask_subtitles_mode(message, st)
            return
        if mode == "photo" and PHOTO_FLOW_ENABLED:
            await self._ask_vibe_shortlist(message, st)
            return
        raise RuntimeError(f"unsupported pending bg mode: {mode!r}")
    def _ensure_solid_default_artist(self, st: ChatState) -> bool:
        """Solid/strobe bg needs a footage_artist_id so Stage 2 runs (its picks are
        dropped at AE time, but the scene CUTS drive the strobe). Mirror of team."""
        if str(st.footage_artist_id or "").strip():
            return True
        try:
            first_genre = get_genres()[0]
            first_artist_key = str(first_genre["artists"][0]["key"])
            st.footage_genre_key = str(first_genre["key"])
            st.footage_artist_key = first_artist_key
            st.footage_artist_id = first_artist_key
            return True
        except Exception as exc:
            log.exception("solid_bg_default_artist_pick_failed: %s", exc)
            return False

    async def _handle_wait_bg_mode(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_timing_choice(message, st)
            return
        if text == BTN_BG_PICTURES_PHOTO and PHOTO_FLOW_ENABLED:
            await self._ask_bg_info(message, st, "photo")
            return
        if text == BTN_BG_PICTURES and FOOTAGE_VIBE_FLOW_ENABLED:
            await message.answer("Картинки скоро будут доступны. Пока выбери «Футажи» или «Цветной фон».")
            return
        if text == BTN_BG_FOOTAGE:
            await self._ask_bg_info(message, st, "footage")
            return
        if text == BTN_BG_SOLID:
            await self._ask_bg_info(message, st, "solid")
            return
        if text == BTN_BG_STROBE:
            await self._ask_bg_info(message, st, "solid_strobe")
            return
        await message.answer(
            f"Выбери кнопкой: «{BTN_BG_FOOTAGE}», «{BTN_BG_SOLID}» или «{BTN_BG_STROBE}».",
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

    # ===================== Footage precision flow (Phase 2b) =====================
    # Ranked-shortlist vibe picker, ported 1:1 from tg_bot_botapi. After lyrics we
    # rank the bucket catalog in the background (orchestrator /footage/rank-buckets);
    # the user multi-selects vibes from a paged inline shortlist; at enqueue, N
    # videos are distributed round-robin over the selected buckets (exact-slot
    # theme+tags_group each). Artist/genre are dropped → footage_artist_id=""
    # (tag-only matching). Gated behind FOOTAGE_VIBE_FLOW_ENABLED.

    async def _trigger_vibe_ranker_task(self, st: ChatState) -> None:
        """Fire-and-forget background ranking of the footage buckets by lyrics.
        Resets any previous shortlist/selection (called once per track when the
        user submits lyrics). No-op when the vibe flow is disabled."""
        if not FOOTAGE_VIBE_FLOW_ENABLED:
            return
        lyrics = str(st.lyrics_text or "").strip()
        st.vibe_rank_status = "pending"
        st.vibe_ranked_ids = []
        st.vibe_labels_by_id = {}
        st.vibe_selected_ids = []
        st.vibe_page = 0
        await self.store.set(st)
        import asyncio as _asyncio
        _asyncio.create_task(
            self._run_vibe_ranker_bg(chat_id=int(st.chat_id), lyrics=lyrics, mood="")
        )

    async def _run_vibe_ranker_bg(self, *, chat_id: int, lyrics: str, mood: str) -> None:
        """Background runner — must never raise into the asyncio loop."""
        try:
            result = await self.orchestrator.rank_buckets(lyrics=lyrics, mood=mood)
            ranked_ids, labels = self._parse_ranked_buckets(result)
            st = await self.store.get(chat_id)
            # Stale guard: only persist if the user hasn't moved on / re-ranked.
            if st.vibe_rank_status not in {"pending", "ready"}:
                return
            st.vibe_ranked_ids = ranked_ids
            st.vibe_labels_by_id = labels
            st.vibe_rank_status = "ready" if ranked_ids else "failed"
            await self.store.set(st)
            log.info("vibe_rank_ok chat=%s buckets=%d used_llm=%s",
                     chat_id, len(ranked_ids), bool(result.get("used_llm")))
        except Exception as e:
            log.warning("vibe_rank_fail chat=%s err=%r", chat_id, e)
            try:
                st = await self.store.get(chat_id)
                st.vibe_rank_status = "failed"
                await self.store.set(st)
            except Exception:
                pass

    @staticmethod
    def _parse_ranked_buckets(result: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        """(ranked bucket_ids, bucket_id→label) from a rank-buckets response."""
        ranked_ids: List[str] = []
        labels: Dict[str, str] = {}
        for b in (result.get("buckets") or []):
            if not isinstance(b, dict):
                continue
            bid = str(b.get("bucket_id") or "").strip()
            if not bid or ":" not in bid or bid in labels:
                continue
            ranked_ids.append(bid)
            label = str(b.get("label") or "").strip() or str(b.get("tags_group") or "").strip() or bid
            labels[bid] = label
        return ranked_ids, labels

    async def _ensure_vibe_ranked(self, st: ChatState) -> bool:
        """Make sure st has a ranked shortlist. If the background ranker hasn't
        finished (or failed), rank synchronously now. Returns False only when
        ranking yields nothing after retries (caller falls back to the legacy
        genre picker).

        Retries a couple of times with a short backoff: the orchestrator
        endpoint is hardened to never 500/empty, so a failure here is a
        transient client-side hiccup (timeout/connection reset) — without a
        retry, one bad request permanently strands the chat in the legacy
        artist flow (nothing else re-triggers ranking until new lyrics)."""
        if st.vibe_ranked_ids:
            legacy_ids = [
                bid for bid in st.vibe_ranked_ids
                if bid.split(":", 1)[0].endswith(("_major", "_minor"))
            ]
            if not legacy_ids:
                return True
            log.info("vibe_catalog_migration chat=%s legacy_ids=%d action=rerank",
                     st.chat_id, len(legacy_ids))
            st.vibe_ranked_ids = []
            st.vibe_labels_by_id = {}
            st.vibe_selected_ids = []
            st.vibe_rank_status = "pending"
        lyrics = str(st.lyrics_text or "").strip()
        last_err: Exception | None = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(0.5 * attempt)
            try:
                result = await self.orchestrator.rank_buckets(lyrics=lyrics, mood="")
                ranked_ids, labels = self._parse_ranked_buckets(result)
            except Exception as e:
                last_err = e
                log.warning(
                    "vibe_rank_sync_fail chat=%s attempt=%d err=%r",
                    st.chat_id, attempt + 1, e,
                )
                continue
            st.vibe_ranked_ids = ranked_ids
            st.vibe_labels_by_id = labels
            st.vibe_rank_status = "ready" if ranked_ids else "failed"
            await self.store.set(st)
            return bool(ranked_ids)
        log.error(
            "vibe_rank_sync_exhausted chat=%s attempts=3 last_err=%r", st.chat_id, last_err
        )
        return False

    def _vibe_page_count(self, st: ChatState) -> int:
        n = len(st.vibe_ranked_ids or [])
        if n <= 0:
            return 0
        return (n + VIBE_PAGE_SIZE - 1) // VIBE_PAGE_SIZE

    def _build_vibe_keyboard(self, st: ChatState) -> InlineKeyboardMarkup:
        """Inline multi-select for the current page + control row. Toggle state
        (✅) is read from vibe_selected_ids so it persists across pages."""
        ranked = list(st.vibe_ranked_ids or [])
        selected = set(st.vibe_selected_ids or [])
        pages = max(1, self._vibe_page_count(st))
        page = int(st.vibe_page or 0) % pages
        start = page * VIBE_PAGE_SIZE
        rows: List[List[InlineKeyboardButton]] = []
        for idx in range(start, min(start + VIBE_PAGE_SIZE, len(ranked))):
            bid = ranked[idx]
            label = _vibe_display_label(st.vibe_labels_by_id.get(bid, bid))
            mark = "✅ " if bid in selected else "▫️ "
            rows.append([InlineKeyboardButton(
                text=f"{mark}{label}", callback_data=f"{VIBE_CB_PREFIX}tog:{idx}"
            )])
        controls = []
        if pages > 1:
            controls.append(InlineKeyboardButton(
                text=BTN_VIBE_REFRESH, callback_data=f"{VIBE_CB_PREFIX}more"
            ))
        controls.append(InlineKeyboardButton(
            text=BTN_VIBE_DONE, callback_data=f"{VIBE_CB_PREFIX}done"
        ))
        rows.append(controls)
        rows.append([InlineKeyboardButton(
            text=BTN_VIBE_BACK, callback_data=f"{VIBE_CB_PREFIX}back"
        )])
        # NB: the "✨ По треку (авто)" button was removed — vibe selection is now
        # always explicit (multi-select + «Готово»).
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _vibe_shortlist_text(self, st: ChatState) -> str:
        pages = max(1, self._vibe_page_count(st))
        page = int(st.vibe_page or 0) % pages
        n_sel = len(st.vibe_selected_ids or [])
        lines = [
            "Выбери вайб футажей — можно несколько.",
            "",
            f"Страница {page + 1}/{pages}. Выбрано: {n_sel}.",
            "",
            "Механика проста: тап на кнопку — вайб попадает в набор ✓",
            "",
            "Остальные кнопки:",
            "1. «Ещё варианты» — показать другие варианты, выбор сохраняется.",
            "2. «Готово» — перейти к настройке субтитров.",
            "3. «Назад» — выбрать другой тип фона.",
        ]
        return "\n".join(lines)

    async def _ask_vibe_shortlist(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_VIBE
        # Vibe flow drops artist/genre → tag-only footage matching server-side.
        st.footage_genre_key = ""
        st.footage_artist_key = ""
        st.footage_artist_id = ""
        # Fresh selection each time the footage step is entered (e.g. reuse-input
        # re-runs keep the cached ranking but start the multi-select clean).
        st.vibe_selected_ids = []
        st.vibe_page = 0
        await self.store.set(st)
        ok = await self._ensure_vibe_ranked(st)
        if not ok:
            await message.answer(
                "Не удалось подобрать вайбы по треку — выбери стиль вручную."
            )
            await self._ask_footage_genre(message, st)
            return
        # Drop the reply keyboard, then send preview reels + the inline multi-select.
        await message.answer("Готовлю шортлист вайбов…", reply_markup=ReplyKeyboardRemove())
        await self._send_vibe_previews(message, st)
        await message.answer(
            self._vibe_shortlist_text(st),
            reply_markup=self._build_vibe_keyboard(st),
        )

    async def _send_vibe_previews(self, message: Message, st: ChatState) -> None:
        """Send a captionless preview reel for each vibe on the current page (the
        vibe name lives on the video + the button, so no caption — it would just
        get truncated). Buckets without a preview fall back to button-only.
        Uses the file_id_public variant (this is the public bot)."""
        ranked = list(st.vibe_ranked_ids or [])
        if not ranked:
            return
        pages = max(1, self._vibe_page_count(st))
        page = int(st.vibe_page or 0) % pages
        start = page * VIBE_PAGE_SIZE
        for idx in range(start, min(start + VIBE_PAGE_SIZE, len(ranked))):
            fid = _bucket_preview_file_id(ranked[idx])
            if not fid:
                continue
            try:
                await message.answer_video(video=fid)
            except Exception:
                log.warning("failed to send vibe preview for %s", ranked[idx])

    async def _handle_vibe_callback(self, cb: CallbackQuery) -> None:
        """Handle vibe:tog:<idx> | vibe:more | vibe:done | vibe:auto callbacks."""
        data = str(cb.data or "")
        if cb.message is None or cb.message.chat is None:
            await cb.answer()
            return
        chat_id = int(cb.message.chat.id)
        st = await self.store.get(chat_id)
        if st.stage != STAGE_WAIT_VIBE:
            await cb.answer("Шаг уже пройден.", show_alert=False)
            return
        action = data[len(VIBE_CB_PREFIX):]

        if action.startswith("tog:"):
            try:
                idx = int(action.split(":", 1)[1])
            except (ValueError, IndexError):
                await cb.answer()
                return
            ranked = list(st.vibe_ranked_ids or [])
            if not (0 <= idx < len(ranked)):
                await cb.answer()
                return
            bid = ranked[idx]
            sel = list(st.vibe_selected_ids or [])
            if bid in sel:
                sel.remove(bid)
            else:
                sel.append(bid)
            st.vibe_selected_ids = sel
            await self.store.set(st)
            try:
                await cb.message.edit_text(
                    self._vibe_shortlist_text(st),
                    reply_markup=self._build_vibe_keyboard(st),
                )
            except TelegramBadRequest:
                pass
            await cb.answer("В наборе" if bid in sel else "Убрано")
            return

        if action == "back":
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
            await cb.answer()
            await self._ask_bg_mode(cb.message, st)
            return
        if action == "more":
            pages = max(1, self._vibe_page_count(st))
            st.vibe_page = (int(st.vibe_page or 0) + 1) % pages
            await self.store.set(st)
            # show the new page's preview reels, then refresh the keyboard below
            await self._send_vibe_previews(cb.message, st)
            try:
                await cb.message.answer(
                    self._vibe_shortlist_text(st),
                    reply_markup=self._build_vibe_keyboard(st),
                )
            except TelegramBadRequest:
                pass
            await cb.answer()
            return

        if action in {"done", "auto"}:
            # "auto" kept only for backward-compat with in-flight callbacks; the
            # button is gone, so the normal path is an explicit multi-select.
            if action == "auto":
                ranked = list(st.vibe_ranked_ids or [])
                st.vibe_selected_ids = [ranked[0]] if ranked else []
            if not st.vibe_selected_ids:
                await cb.answer("Выбери хотя бы один вайб — тапни по кнопке.", show_alert=True)
                return
            await self.store.set(st)
            labels = [_vibe_display_label(st.vibe_labels_by_id.get(b, b)) for b in st.vibe_selected_ids]
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
            await cb.answer("Готово")
            await cb.message.answer("Вайбы: " + ", ".join(labels))
            # Photo flow (4:3): after the vibe, ask the two photo picker steps.
            # Mirror of tg_bot_botapi (gated by PHOTO_FLOW_ENABLED at entry).
            if st.bg_mode == "photo":
                await self._ask_photo_style(cb.message, st)
            else:
                await self._ask_subtitles_mode(cb.message, st)
            return

        await cb.answer()

    async def _handle_wait_vibe_text(self, message: Message, st: ChatState) -> None:
        """The vibe picker is an inline keyboard; a typed message means the user
        ignored the buttons. Re-send the shortlist so they can tap."""
        if not st.vibe_ranked_ids:
            ok = await self._ensure_vibe_ranked(st)
            if not ok:
                await self._ask_footage_genre(message, st)
                return
        await message.answer(
            self._vibe_shortlist_text(st),
            reply_markup=self._build_vibe_keyboard(st),
        )

    async def _send_hook_intro(self, message: Message, key: str) -> None:
        """Send a hook option's intro (video+caption once a clip is set, else
        text). Mirror of tg_bot_botapi — used when the public hook flow lands."""
        intro = hook_intro(key)
        if not intro:
            return
        if intro["video"]:
            await message.answer_video(
                video=intro["video"], caption=intro["text"], parse_mode="Markdown"
            )
        else:
            await message.answer(intro["text"], parse_mode="Markdown")

    # ====================================================================
    # Hook flow — ported 1:1 from tg_bot_botapi (behind HOOK_FLOW_ENABLED).
    # Exit goes to _proceed_to_versions_or_confirm (public's post-settings).
    # ====================================================================
    def _color_kb(self):
        rows = [COLOR_PALETTE_BUTTONS[i:i + 3] for i in range(0, len(COLOR_PALETTE_BUTTONS), 3)]
        rows.append([BTN_COLOR_DEFAULT])
        return _kb(*rows)

    async def _ask_subtitle_color(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_SUBTITLE_COLOR
        await self.store.set(st)
        await message.answer(
            "Цвет субтитров? Выбери из палитры или «По умолчанию».",
            reply_markup=self._color_kb(),
        )

    async def _handle_wait_subtitle_color(self, message: Message, st: ChatState) -> None:
        choice = _parse_color_choice(message.text or "")
        if choice is None:
            await message.answer("Выбери цвет кнопкой из палитры или «По умолчанию».")
            return
        st.subtitle_color_hex = choice
        await self._ask_accent_color(message, st)

    async def _ask_accent_color(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_ACCENT_COLOR
        await self.store.set(st)
        await message.answer(
            "Выбери акцентный цвет для фигур и фокус-слов: палитра или «По умолчанию».",
            reply_markup=self._color_kb(),
        )

    async def _handle_wait_accent_color(self, message: Message, st: ChatState) -> None:
        choice = _parse_color_choice(message.text or "")
        if choice is None:
            await message.answer("Выбери цвет кнопкой из палитры или «По умолчанию».")
            return
        st.accent_color_hex = choice
        st.colors_done = True
        await self.store.set(st)
        await self._proceed_to_versions_or_confirm(message, st)

    async def _trigger_hook_analysis_task(self, st: ChatState) -> None:
        audio_path = str(st.prepared_audio_local_path or "").strip()
        if not audio_path:
            log.info("hook_bg_skip reason=no_audio chat=%s", st.chat_id)
            return
        clip_start = float(st.user_clip_start_sec or 0.0)
        clip_end = float(st.user_clip_end_sec or 0.0)
        if clip_end <= clip_start:
            log.info("hook_bg_skip reason=no_focus_clip chat=%s", st.chat_id)
            return
        if (
            st.hook_analysis_status == "ready"
            and st.hook_analysis_audio_path == audio_path
            and abs(st.hook_analysis_clip_start - clip_start) < 1e-3
            and abs(st.hook_analysis_clip_end - clip_end) < 1e-3
        ):
            return
        st.hook_analysis_status = "pending"
        st.hook_analysis_audio_path = audio_path
        st.hook_analysis_clip_start = clip_start
        st.hook_analysis_clip_end = clip_end
        st.hook_drop_candidates = []
        st.hook_analysis_error = ""
        await self.store.set(st)
        import asyncio as _asyncio
        _asyncio.create_task(
            self._run_hook_analysis_bg(
                chat_id=int(st.chat_id),
                audio_path=audio_path,
                clip_start=clip_start,
                clip_end=clip_end,
            )
        )

    async def _run_hook_analysis_bg(
        self, *, chat_id: int, audio_path: str, clip_start: float, clip_end: float
    ) -> None:
        try:
            from pathlib import Path as _Path
            prepared = _Path(audio_path).expanduser().resolve()
            key = self._build_raw_audio_key(chat_id=chat_id, file_name=prepared.name)
            audio_s3_url = await asyncio.to_thread(
                self.s3.upload_file,
                path=prepared,
                bucket=self.settings.s3_bucket_raw_audio,
                key=key,
                content_type="audio/mpeg",
            )
            result = await self.orchestrator.analyze_hook(
                audio_s3_url=str(audio_s3_url),
                clip_start_sec=clip_start,
                clip_end_sec=clip_end,
            )
            raw_cands = result.get("drop_candidates") or []
            candidates = [
                {
                    "t": float(c.get("t")),
                    "confidence": float(c.get("confidence", 0.0)),
                    "snapped_to_beat": bool(c.get("snapped_to_beat", False)),
                    "source": str(c.get("source", "")),
                }
                # Keep the FULL detected pool (not just the top 3) so auto drop
                # selection can reach a later candidate; picker still shows [:3].
                for c in raw_cands
                if isinstance(c, dict) and c.get("t") is not None
            ]
            bpm = float(result.get("bpm") or 0.0)
            st = await self.store.get(chat_id)
            if (
                st.hook_analysis_audio_path == audio_path
                and abs(st.hook_analysis_clip_start - clip_start) < 1e-3
                and abs(st.hook_analysis_clip_end - clip_end) < 1e-3
            ):
                st.hook_drop_candidates = candidates
                st.hook_analysis_bpm = bpm
                st.hook_analysis_status = "ready"
                st.hook_analysis_error = ""
                await self.store.set(st)
                log.info("hook_bg_ok chat=%s bpm=%.2f cands=%d", chat_id, bpm, len(candidates))
        except Exception as e:
            log.warning("hook_bg_fail chat=%s err=%r", chat_id, e)
            try:
                st = await self.store.get(chat_id)
                st.hook_analysis_status = "failed"
                st.hook_analysis_error = str(e)[:300]
                await self.store.set(st)
            except Exception:
                pass

    async def _ask_hook_choice(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_HOOK_CHOICE
        await self.store.set(st)
        note = ""
        if st.hook_analysis_status == "ready" and st.hook_drop_candidates:
            top = st.hook_drop_candidates[0]
            note = (
                f"\n\nЗвуковой дроп найден на ~{self._fmt_timing(float(top['t']))} "
                f"(уверенность {float(top['confidence']):.0%})."
            )
        elif st.hook_analysis_status == "pending":
            note = "\n\nЕщё считаю анализ — выбери, когда определишься."
        elif st.hook_analysis_status == "failed":
            note = "\n\nАнализ аудио не удался, дроп можно ввести вручную."
        await message.answer(
            "Сделать хук в ролик? Хук — это короткий FX-акцент на дропе, "
            "помогает удерживать зрителя." + note,
            reply_markup=_kb([BTN_HOOK_YES, BTN_HOOK_NO]),
        )

    async def _handle_wait_hook_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_HOOK_NO:
            st.hook_enabled = False
            st.hook_drop_t = None
            st.hook_category = ""
            st.hook_device = ""
            st.f2_shape = ""
            st.f1_sound_url = ""
            st.f1_sound_text = ""
            await self.store.set(st)
            await self._proceed_to_versions_or_confirm(message, st)
            return
        if text == BTN_HOOK_YES:
            st.hook_enabled = True
            await self.store.set(st)
            await self._ask_hook_drop(message, st)
            return
        await message.answer(f"Выбери кнопку: «{BTN_HOOK_YES}» или «{BTN_HOOK_NO}».")

    async def _ask_hook_drop(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_HOOK_DROP
        await self.store.set(st)
        cands = list(st.hook_drop_candidates or [])
        cs = float(st.user_clip_start_sec or 0.0)
        ce = float(st.user_clip_end_sec or 0.0)
        has_clip = ce > cs
        primary_rows: List[List[str]] = []
        breakdown: List[str] = []
        if cands:
            for idx, c in enumerate(cands[:3]):
                t = float(c["t"])
                t_label = self._fmt_timing(t)
                tag = "🎯 " if idx == 0 else ""
                primary_rows.append([f"{tag}{t_label}"])
                if has_clip:
                    lead = max(0, int(round(t - cs)))
                    core = max(0, int(round(ce - t)))
                    line_tag = "🎯 " if idx == 0 else "     "
                    breakdown.append(f"{line_tag}{t_label} — разгон {lead}с · кора {core}с")
        primary_rows.append([BTN_HOOK_DROP_MANUAL])
        primary_rows.append([BTN_BACK])
        if not cands:
            if st.hook_analysis_status == "pending":
                hint = ("Анализ ещё не готов — попробуй через несколько секунд, "
                        "или выбери «Ввести вручную».")
            elif st.hook_analysis_status == "failed":
                hint = (f"Анализ не удался ({st.hook_analysis_error or 'unknown'}). "
                        "Можно ввести тайминг вручную.")
            else:
                hint = ("Анализ дропа недоступен (нет focus clip). "
                        "Введи тайминг вручную.")
            await message.answer(hint, reply_markup=_kb(*primary_rows))
            return
        clip_span = f" (отрезок {self._fmt_timing(cs)}–{self._fmt_timing(ce)})" if has_clip else ""
        body: List[str] = [f"Выбери момент дропа в треке{clip_span}:"]
        if breakdown:
            body.append("")
            body.extend(breakdown)
        body.append("")
        body.append(
            "Тайминг с 🎯 — лучший кандидат. Если ни один не подходит — "
            "«Ввести вручную»."
        )
        body.append("")
        body.append("Хук: ~3–4с разгон до дропа + 10–12с кора после.")
        await message.answer("\n".join(body), reply_markup=_kb(*primary_rows))

    async def _handle_wait_hook_drop(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_choice(message, st)
            return
        if text == BTN_HOOK_DROP_MANUAL:
            st.stage = STAGE_WAIT_HOOK_DROP_MANUAL
            await self.store.set(st)
            await message.answer(
                "Отправь момент дропа в формате 1:23 или 83 (секунды).",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        chosen = self._parse_hook_drop_label(text, candidates=st.hook_drop_candidates)
        if chosen is None:
            await message.answer("Не распознал выбор — нажми одну из кнопок ниже.")
            return
        if not self._validate_hook_drop_inside_clip(chosen, st):
            await message.answer(
                "Момент дропа должен быть внутри выбранного фрагмента. "
                "Попробуй другой вариант или введи вручную."
            )
            return
        st.hook_drop_t = float(chosen)
        await self.store.set(st)
        await message.answer(f"Дроп зафиксирован на {self._fmt_timing(float(chosen))}.")
        await self._ask_hook_type(message, st)

    async def _handle_wait_hook_drop_manual(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        parsed = self._parse_single_timing(text)
        if parsed is None:
            await message.answer("Не распознал тайминг. Формат: 1:23 или 83 (секунды). Попробуй ещё раз.")
            return
        if not self._validate_hook_drop_inside_clip(parsed, st):
            await message.answer(
                "Этот момент за пределами выбранного фрагмента. "
                f"Допустимый диапазон: {self._fmt_timing(st.user_clip_start_sec)} – "
                f"{self._fmt_timing(st.user_clip_end_sec)}."
            )
            return
        st.hook_drop_t = float(parsed)
        await self.store.set(st)
        await message.answer(f"Дроп зафиксирован на {self._fmt_timing(float(parsed))}.")
        await self._ask_hook_type(message, st)

    async def _ask_hook_type(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_HOOK_TYPE
        await self.store.set(st)
        # One compact teaser message instead of 5 separate intro bubbles: the
        # per-category one-liner sits next to its button (full descriptions live
        # in core.hook_intros for the team bot / future on-demand expansion).
        await message.answer(
            "Хук — приём в первые секунды, который цепляет зрителя.\n\n"
            "🔊 Звук — свой звук до дропа + вспышка-молния\n"
            "🟦 Объект — фигура в такт на склейке до дропа\n"
            "✨ Эффект — визуальные FX: хук, переход, грейд\n"
            "👆 Движение — engagement-байт: рука/голова в такт\n"
            "💭 Мысль — голос-ИИ перед дропом\n\n"
            "Выбери тип ↓",
            reply_markup=_kb(
                [BTN_HOOK_CAT_SOUND, BTN_HOOK_CAT_OBJECT],
                [BTN_HOOK_CAT_EFFECT, BTN_HOOK_CAT_MOTION],
                [BTN_HOOK_CAT_THOUGHT],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_hook_type(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_drop(message, st)
            return
        if text in _HOOK_CATEGORY_NOT_READY:
            await message.answer(f"«{text}» пока в разработке — скоро добавим.")
            return
        if text == BTN_HOOK_CAT_OBJECT:
            if st.hook_drop_t is None:
                await message.answer("Для «Объекта» нужен момент дропа — вернись и выбери его.")
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "object"
            st.f2_shape = ""
            await self.store.set(st)
            await self._ask_f2_shape(message, st)
            return
        if text == BTN_HOOK_CAT_SOUND:
            if st.hook_drop_t is None:
                await message.answer("Для «Звука» нужен момент дропа — вернись и выбери его.")
                await self._ask_hook_drop(message, st)
                return
            _clip_start = float(st.user_clip_start_sec or 0.0)
            if (float(st.hook_drop_t) - _clip_start) <= 1.0:
                await message.answer(
                    "Дроп слишком близко к началу отрывка: для «Звука» нужно ≥1с "
                    "до дропа (звук играет в окне до хука). Выбери дроп позже."
                )
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "sound"
            st.f1_sound_url = ""
            st.f1_sound_text = ""
            await self.store.set(st)
            await self._ask_f1_sound(message, st)
            return
        if text == BTN_HOOK_CAT_EFFECT:
            if st.hook_drop_t is None:
                await message.answer("Для «Эффекта» нужен момент дропа — вернись и выбери его.")
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "effect"
            st.effect_hook = ""
            st.effect_transition = ""
            st.effect_extra = ""
            st.effect_extra_full = False
            st.effect_hook_extend = ""
            st.visual_transition = ""
            st.visual_style = ""
            st.visuals_done = False
            await self.store.set(st)
            await self._ask_effect_hook(message, st)
            return
        if text == BTN_HOOK_CAT_THOUGHT:
            st.hook_category = "thought"
            await self.store.set(st)
            await self._ask_hook_device(message, st)
            return
        if text == BTN_HOOK_CAT_MOTION:
            if st.hook_drop_t is None:
                await message.answer("Для «Движения» нужен момент дропа — вернись и выбери его.")
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "motion"
            await self.store.set(st)
            await self._ask_hook_device(message, st)
            return
        await message.answer("Выбери тип хука кнопкой ниже.")

    async def _send_option_previews(self, message: Message, keys: List[str]) -> None:
        """Mirror of tg_bot_botapi: send example reels for a menu step's options
        WITH a short caption = the option name (the effect name is not on the
        video). Uses file_id_public. Keys without a preview are skipped."""
        previews = _load_hook_previews()
        for key in keys:
            e = previews.get(key) or {}
            fid = str(e.get(_BUCKET_PREVIEW_FILE_ID_FIELD) or "").strip()
            if not fid:
                continue
            caption = str(e.get("label") or "").strip()
            try:
                await message.answer_video(video=fid, caption=caption or None)
            except Exception:
                log.warning("failed to send option preview for %s", key)

    async def _ask_hook_device(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_HOOK_DEVICE
        await self.store.set(st)
        if st.hook_category == "motion":
            await self._send_option_previews(
                message, [f"motion:{v}" for v in _HOOK_MOTION_DEVICE_BY_BUTTON.values()]
            )
            await message.answer(
                "Какой приём для «Движения» пойдёт в клип?\n\n"
                "• Свайп — палец свайпает.\n"
                "• Тап — палец тапает по кругу.\n"
                "• Зум — пальцы разводят зум.\n"
                "• Задержи палец — палец держит круг.\n"
                "• Качай головой — голова качает в такт.",
                reply_markup=_kb(
                    [BTN_HOOK_DEV_SWIPE, BTN_HOOK_DEV_TAP],
                    [BTN_HOOK_DEV_PINCH, BTN_HOOK_DEV_HOLD],
                    [BTN_HOOK_DEV_HEAD],
                    [BTN_BACK],
                ),
            )
            return
        await message.answer(
            "Какой приём «Мысли»?\n"
            "• Панчлайн — голос подводит, трек добивает.\n"
            "• Пропущенное слово — голос обрывается, трек закрывает.\n"
            "• Эхо — голос заранее произносит фразу-крючок трека.\n"
            "• Вопрос к треку — голос спрашивает, трек отвечает.\n"
            "• Инверсия — голос говорит противоположное.",
            reply_markup=_kb(
                [BTN_HOOK_DEV_PUNCHLINE, BTN_HOOK_DEV_MISSING_WORD],
                [BTN_HOOK_DEV_LYRIC_ECHO, BTN_HOOK_DEV_QUESTION],
                [BTN_HOOK_DEV_INVERSE],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_hook_device(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_type(message, st)
            return
        if st.hook_category == "motion":
            device = _HOOK_MOTION_DEVICE_BY_BUTTON.get(text)
            if device is None:
                await message.answer("Выбери приём кнопкой ниже.")
                return
            if st.hook_drop_t is None:
                await message.answer("Сначала выбери момент дропа.")
                await self._ask_hook_drop(message, st)
                return
            bpm = float(st.hook_analysis_bpm or 0.0)
            if bpm <= 0.0:
                await message.answer(
                    "Для «Движения» нужен анализ трека (BPM ещё не посчитан). "
                    "Подожди пару секунд и попробуй снова, либо выбери другой хук."
                )
                return
            lead = self._f4_effective_lead(device, bpm)
            drop = float(st.hook_drop_t)
            if drop - lead < 0.0:
                await message.answer(
                    f"Хук слишком близко к началу трека: для «{text}» при {bpm:.0f} BPM "
                    f"нужно ≥ {lead:.1f}с разгона до дропа. Выбери момент дропа позже."
                )
                await self._ask_hook_drop(message, st)
                return
            st.hook_device = device
            st.hook_type = "standard"
            await self.store.set(st)
            core = float(st.user_clip_end_sec or 0.0) - drop
            core_note = ""
            if core < 6.0:
                core_note = (
                    f"\n⚠️ После дропа всего ~{core:.0f}с — кора будет короткой. "
                    "В идеале 10–12с после дропа: выбери дроп раньше или расширь отрывок."
                )
            await message.answer(
                f"Ок, «Движение»: {text}. Дроп на {self._fmt_timing(drop)}, "
                f"кора ~{core:.0f}с." + core_note
            )
            await self._proceed_to_versions_or_confirm(message, st)
            return
        device = _HOOK_DEVICE_BY_BUTTON.get(text)
        if device is None:
            await message.answer("Выбери приём кнопкой ниже.")
            return
        st.hook_device = device
        st.hook_type = "standard"
        await self.store.set(st)
        await message.answer(f"Ок, «Мысль»: {text}.")
        await self._proceed_to_versions_or_confirm(message, st)

    # ── Photo flow (4:3) — 2-step picker (style -> transition). Mirror of team;
    # UX gated behind PHOTO_FLOW_ENABLED, exit goes to public's post-settings. ──
    async def _ask_photo_style(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_PHOTO_STYLE
        await self.store.set(st)
        await message.answer(
            "Картинки — шаг 1/2: стилизация (грейд на весь ролик).\n"
            "• Тёплый / Холодный — цветовая температура.\n"
            "• Винтаж / Ч/Б / VHS — плёночные луки.\n"
            "• Без стилизации — оставить как есть.",
            reply_markup=_kb(
                [BTN_PHOTO_STYLE_WARM, BTN_PHOTO_STYLE_COLD],
                [BTN_PHOTO_STYLE_VINTAGE, BTN_PHOTO_STYLE_BW],
                [BTN_PHOTO_STYLE_VHS, BTN_PHOTO_STYLE_NONE],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_photo_style(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_vibe_shortlist(message, st)
            return
        style = _PHOTO_STYLE_BY_BUTTON.get(text)
        if style is None:
            await message.answer("Выбери стилизацию кнопкой ниже.")
            return
        st.photo_style = style
        await self.store.set(st)
        await self._ask_photo_transition(message, st)

    async def _ask_photo_transition(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_PHOTO_TRANSITION
        await self.store.set(st)
        await message.answer(
            "Шаг 2/2: переход между фото.\n"
            "• Вспышка / Слайд / Зум / Вжух — варианты смены кадра.\n"
            "• Без перехода — резкая склейка.",
            reply_markup=_kb(
                [BTN_PHOTO_TR_FLASH, BTN_PHOTO_TR_SLIDE],
                [BTN_PHOTO_TR_ZOOM, BTN_PHOTO_TR_WHIP],
                [BTN_PHOTO_TR_NONE],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_photo_transition(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_photo_style(message, st)
            return
        tr = _PHOTO_TRANSITION_BY_BUTTON.get(text)
        if tr is None:
            await message.answer("Выбери переход кнопкой ниже.")
            return
        st.photo_transition = tr
        await self.store.set(st)
        # Photo render is horizontal 1920×1440 — subtitles aren't baked in 4:3,
        # so skip the subtitles/hook steps and go straight to the version count.
        await self._proceed_to_versions_or_confirm(message, st)

    async def _ask_visual_transition(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_VISUAL_TRANSITION
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"effect_transition:{v}" for v in _FX_TRANSITION_BY_BUTTON.values()]
        )
        await message.answer(
            "Шаг 1/2: переход на склейках футажа.\n\n"
            "• Снап-вайп\n• Минимакс\n• Инверт\n• Экстракт\n• Вспышки\n\n"
            "Можно пропустить.",
            reply_markup=_kb(
                [BTN_FX_TR_SNAP, BTN_FX_TR_MINIMAX],
                [BTN_FX_TR_INVERT, BTN_FX_TR_EXTRACT],
                [BTN_FX_TR_FLASH], [BTN_FX_SKIP], [BTN_BACK],
            ),
        )

    async def _handle_wait_visual_transition(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_choice(message, st)
            return
        if text == BTN_FX_SKIP:
            st.visual_transition = ""
        else:
            transition = _FX_TRANSITION_BY_BUTTON.get(text)
            if transition is None:
                await message.answer("Выбери переход кнопкой ниже или «Пропустить».")
                return
            st.visual_transition = transition
        await self.store.set(st)
        await self._ask_visual_style(message, st)

    async def _ask_visual_style(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_VISUAL_STYLE
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"effect_extra:{v}" for v in _FX_EXTRA_BY_BUTTON.values()]
        )
        await message.answer(
            "Шаг 2/2: стилизация футажа.\n\n"
            "Выбранный эффект применяется ко всему ролику.\n\n"
            "• Ксерокс\n• Аналог-глитч\n• Неон\n• Старая камера\n"
            "• Ч/Б\n• Crystal Glow\n• Night Vision\n• Wave\n\n"
            "Можно пропустить.",
            reply_markup=_kb(
                [BTN_FX_EX_XEROX, BTN_FX_EX_ANALOG],
                [BTN_FX_EX_NEON, BTN_FX_EX_OLDCAM],
                [BTN_FX_EX_BLACKWHITE, BTN_FX_EX_CRYSTAL],
                [BTN_FX_EX_NIGHT, BTN_FX_EX_WAVE],
                [BTN_FX_SKIP], [BTN_BACK],
            ),
        )

    async def _handle_wait_visual_style(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_visual_transition(message, st)
            return
        if text == BTN_FX_SKIP:
            st.visual_style = ""
        else:
            style = _FX_EXTRA_BY_BUTTON.get(text)
            if style is None:
                await message.answer("Выбери стилизацию кнопкой ниже или «Пропустить».")
                return
            st.visual_style = style
        st.visuals_done = True
        await self.store.set(st)
        await self._proceed_to_versions_or_confirm(message, st)
    async def _ask_effect_hook(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_HOOK
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"effect_hook:{v}" for v in _FX_HOOK_BY_BUTTON.values()]
        )
        await message.answer(
            "«Эффект» — шаг 1/3: хук на дропе.\n\n"
            "• Молния — вспышка-молнии + шейк.\n"
            "• Затвор — нарезка затвора + лого-штамп.\n"
            "• Слоу-шаттер — echo-шлейф + вспышка (можно растянуть).\n"
            "• Негатив-зум — инверт-вспышка + зум на дропе.\n\n"
            "Можно пропустить, если хук не нужен.",
            reply_markup=_kb(
                [BTN_FX_HOOK_LIGHT, BTN_FX_HOOK_SHUTTER],
                [BTN_FX_HOOK_SLOW, BTN_FX_HOOK_NEGZOOM],
                [BTN_FX_SKIP],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_effect_hook(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_type(message, st)
            return
        if text == BTN_FX_SKIP:
            st.effect_hook = ""
            await self.store.set(st)
            await self._effect_summary_and_continue(message, st)
            return
        hook = _FX_HOOK_BY_BUTTON.get(text)
        if hook is None:
            await message.answer("Выбери хук кнопкой ниже или «Пропустить».")
            return
        st.effect_hook = hook
        await self.store.set(st)
        await self._after_effect_extra(message, st)

    async def _ask_effect_transition(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_TRANSITION
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"effect_transition:{v}" for v in _FX_TRANSITION_BY_BUTTON.values()]
        )
        await message.answer(
            "Шаг 2/3: переход на склейках футажа.\n\n"
            "• Снап-вайп\n"
            "• Минимакс\n"
            "• Инверт\n"
            "• Экстракт\n"
            "• Вспышки\n\n"
            "Можно пропустить.",
            reply_markup=_kb(
                [BTN_FX_TR_SNAP, BTN_FX_TR_MINIMAX],
                [BTN_FX_TR_INVERT, BTN_FX_TR_EXTRACT],
                [BTN_FX_TR_FLASH],
                [BTN_FX_SKIP],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_effect_transition(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_effect_hook(message, st)
            return
        if text == BTN_FX_SKIP:
            st.effect_transition = ""
            await self.store.set(st)
            await self._ask_effect_extra(message, st)
            return
        tr = _FX_TRANSITION_BY_BUTTON.get(text)
        if tr is None:
            await message.answer("Выбери переход кнопкой ниже или «Пропустить».")
            return
        st.effect_transition = tr
        await self.store.set(st)
        await self._ask_effect_extra(message, st)

    async def _ask_effect_extra(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_EXTRA
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"effect_extra:{v}" for v in _FX_EXTRA_BY_BUTTON.values()]
        )
        await message.answer(
            "Шаг 3/3: стилизация футажа до дропа.\n\n"
            "• Ксерокс\n"
            "• Аналог-глитч\n"
            "• Неон\n"
            "• Старая камера\n\n"
            "Можно пропустить.",
            reply_markup=_kb(
                [BTN_FX_EX_XEROX, BTN_FX_EX_ANALOG],
                [BTN_FX_EX_NEON, BTN_FX_EX_OLDCAM],
                [BTN_FX_EX_BLACKWHITE, BTN_FX_EX_CRYSTAL],
                [BTN_FX_EX_NIGHT, BTN_FX_EX_WAVE],
                [BTN_FX_SKIP],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_effect_extra(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_effect_transition(message, st)
            return
        if text == BTN_FX_SKIP:
            st.effect_extra = ""
        else:
            ex = _FX_EXTRA_BY_BUTTON.get(text)
            if ex is None:
                await message.answer("Выбери эффект кнопкой ниже или «Пропустить».")
                return
            st.effect_extra = ex
        if not st.effect_extra:
            st.effect_extra_full = False
        await self.store.set(st)
        if not (st.effect_hook or st.effect_transition or st.effect_extra):
            await message.answer("Нужно выбрать хотя бы один эффект из трёх. Начнём заново с хука.")
            await self._ask_effect_hook(message, st)
            return
        if st.effect_extra:
            await self._ask_effect_extra_full(message, st)
            return
        await self._after_effect_extra(message, st)

    async def _ask_effect_extra_full(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_EXTRA_FULL
        await self.store.set(st)
        await message.answer(
            f"Стилизация «{st.effect_extra}»: на весь ролик или только до дропа?\n"
            "• Весь ролик — грейд тянется на всё видео, выше уникальность.\n"
            "• Только до дропа — как обычно, грейд в интро.",
            reply_markup=_kb([BTN_FX_EXTRA_FULL_ALL], [BTN_FX_EXTRA_FULL_PREDROP], [BTN_BACK]),
        )

    async def _handle_wait_effect_extra_full(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_effect_extra(message, st)
            return
        if text == BTN_FX_EXTRA_FULL_ALL:
            st.effect_extra_full = True
        elif text == BTN_FX_EXTRA_FULL_PREDROP:
            st.effect_extra_full = False
        else:
            await message.answer("Выбери кнопкой ниже.")
            return
        await self.store.set(st)
        await self._after_effect_extra(message, st)

    async def _after_effect_extra(self, message: Message, st: ChatState) -> None:
        if st.effect_hook == "flash_slow_shutter":
            await self._ask_effect_extend(message, st)
            return
        await self._effect_summary_and_continue(message, st)

    async def _ask_effect_extend(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_EXTEND
        await self.store.set(st)
        await message.answer(
            "Слоу-шаттер: длина echo-шлейфа?\n"
            "• Стандарт — короткий импульс.\n• До конца ролика — футажи наслаиваются.\n"
            "• 3 футажа после — шлейф до 3-й склейки после дропа.",
            reply_markup=_kb(
                [BTN_FX_EXT_STD],
                [BTN_FX_EXT_END, BTN_FX_EXT_3],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_effect_extend(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_effect_hook(message, st)
            return
        if text not in _FX_EXTEND_BY_BUTTON:
            await message.answer("Выбери вариант длины кнопкой ниже.")
            return
        st.effect_hook_extend = _FX_EXTEND_BY_BUTTON[text]
        await self.store.set(st)
        await self._effect_summary_and_continue(message, st)

    async def _effect_summary_and_continue(self, message: Message, st: ChatState) -> None:
        st.hook_type = "standard"
        await self.store.set(st)
        parts: List[str] = []
        if st.effect_hook:
            parts.append(f"хук «{st.effect_hook}»")
        if st.effect_transition:
            parts.append(f"переход «{st.effect_transition}»")
        if st.effect_extra:
            parts.append(
                f"грейд «{st.effect_extra}»"
                + (" (весь ролик)" if st.effect_extra_full else "")
            )
        if st.effect_hook_extend:
            parts.append(f"растяжка «{st.effect_hook_extend}»")
        if parts:
            await message.answer("Ок, «Эффект»: " + ", ".join(parts) + ".")
        else:
            await message.answer("Хук-эффект пропущен.")
        await self._proceed_to_versions_or_confirm(message, st)

    async def _ask_f2_shape(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_F2_SHAPE
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"shape:{v}" for v in _F2_SHAPE_BY_BUTTON.values()]
        )
        await message.answer(
            "Какая фигура пойдёт на склейку до дропа?\n\n"
            "• Ромб\n"
            "• Квадрат\n"
            "• Звезда-10\n"
            "• Звезда-5\n"
            "• Эллипс",
            reply_markup=_kb(
                [BTN_F2_SHAPE_RHOMB, BTN_F2_SHAPE_SQUARE],
                [BTN_F2_SHAPE_STAR1, BTN_F2_SHAPE_STAR2],
                [BTN_F2_SHAPE_ELIPSE],
                [BTN_BACK],
            ),
        )

    async def _handle_wait_f2_shape(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_type(message, st)
            return
        shape = _F2_SHAPE_BY_BUTTON.get(text)
        if shape is None:
            await message.answer("Выбери фигуру кнопкой ниже.")
            return
        if st.hook_drop_t is None:
            await message.answer("Для «Объекта» нужен момент дропа — вернись и выбери его.")
            await self._ask_hook_drop(message, st)
            return
        st.f2_shape = shape
        st.hook_type = "standard"
        await self.store.set(st)
        await message.answer(
            f"Ок, «Объект»: фигура «{text}». На склейках до дропа — она; "
            f"на дропе — молния; после дропа — рандомные F3-переходы."
        )
        await self._proceed_to_versions_or_confirm(message, st)

    async def _ask_f1_sound(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_F1_SOUND
        await self.store.set(st)
        await message.answer(
            "«Звук»: пришли аудио-файл, который заиграет ДО дропа (разгон/риза).\n"
            "Он встанет в окно до хука; на дропе сработает молния, после — "
            "рандомный визуал-переход.\nПросто отправь аудио сообщением (mp3/m4a/wav).",
            reply_markup=_kb([BTN_BACK]),
        )

    async def _handle_wait_f1_sound(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_type(message, st)
            return
        spec = _extract_audio_spec(message)
        if spec is None:
            await message.answer(
                "Нужен аудио-файл для «Звука». Пришли mp3/m4a/wav сообщением или нажми «Назад»."
            )
            return
        if message.chat is None:
            return
        if st.hook_drop_t is None:
            await message.answer("Для «Звука» нужен момент дропа — вернись и выбери его.")
            await self._ask_hook_drop(message, st)
            return
        chat_id = int(message.chat.id)
        file_id, original_name = spec
        incoming_dir = self.settings.tmp_dir / str(chat_id) / "hook_sound"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        src_name = f"{_now_tag()}_{uuid.uuid4().hex[:8]}_{_safe_name(original_name)}"
        src_path = incoming_dir / src_name
        try:
            await message.answer("Загружаю звук…")
            await self._download_telegram_audio_with_retry(
                bot=message.bot, file_id=file_id, dest=src_path,
                chat_id=chat_id, original_name=original_name,
            )
            key = self._build_raw_audio_key(chat_id=chat_id, file_name=f"f1hook_{src_path.name}")
            sound_url = await asyncio.to_thread(
                self.s3.upload_file, path=src_path,
                bucket=self.settings.s3_bucket_raw_audio, key=key,
                content_type="audio/mpeg",
            )
        except TelegramBadRequest as e:
            log.exception("f1_sound_tg_bad_request chat=%s file_id=%s err=%s", chat_id, file_id, e)
            await message.answer(
                "Не удалось скачать звук из Telegram (возможно, слишком большой). "
                "Пришли файл полегче (mp3/m4a) или нажми «Назад»."
            )
            return
        except Exception as e:
            log.exception("f1_sound_upload_failed chat=%s file_id=%s err=%s", chat_id, file_id, e)
            await message.answer(f"Не удалось загрузить звук: {e}. Попробуй ещё раз или «Назад».")
            return
        st.f1_sound_url = str(sound_url)
        st.hook_type = "standard"
        await self.store.set(st)
        await message.answer(
            "Ок, «Звук»: твой звук заиграет до дропа, на дропе — молния, "
            "после — рандомный визуал-переход."
        )
        await self._ask_f1_text(message, st)

    async def _ask_f1_text(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_F1_TEXT
        await self.store.set(st)
        await message.answer(
            "Хочешь субтитры под этот звук? Пришли текст сообщением — он ляжет "
            "поверх трека тем же стилем, что субтитры (и трек на это время "
            "приглушится). Или нажми «Без субтитров».",
            reply_markup=_kb([BTN_F1_NO_SUBS, BTN_BACK]),
        )

    async def _handle_wait_f1_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_f1_sound(message, st)
            return
        if text == BTN_F1_NO_SUBS:
            st.f1_sound_text = ""
            await self.store.set(st)
            await message.answer("Ок, без субтитров — только звук + визуал.")
            await self._proceed_to_versions_or_confirm(message, st)
            return
        if not text:
            await message.answer("Пришли текст субтитра сообщением или нажми «Без субтитров».")
            return
        st.f1_sound_text = text
        await self.store.set(st)
        await message.answer("Принял текст субтитра для звука.")
        await self._proceed_to_versions_or_confirm(message, st)

    @staticmethod
    def _f4_effective_lead(device: str, bpm: float) -> float:
        from mlcore.hooks.f4_motion.overlay import effective_lead
        return effective_lead(device, float(bpm) if bpm else 0.0)

    @staticmethod
    def _parse_single_timing(text: str) -> Optional[float]:
        s = str(text or "").strip()
        if not s:
            return None
        try:
            if ":" in s:
                m_str, sec_str = s.split(":", 1)
                mins = int(m_str.strip())
                secs = float(sec_str.strip())
                if mins < 0 or secs < 0 or secs >= 60.0:
                    return None
                return float(mins) * 60.0 + secs
            v = float(s)
            return v if v >= 0.0 else None
        except Exception:
            return None

    @staticmethod
    def _parse_hook_drop_label(text: str, *, candidates: List[Dict[str, Any]]) -> Optional[float]:
        s = str(text or "").strip()
        if not s:
            return None
        s = s.lstrip("🎯 ").strip()
        for c in candidates or []:
            try:
                t = float(c["t"])
            except Exception:
                continue
            if s == BlastBotApp._fmt_timing(t):
                return t
        return None

    @staticmethod
    def _validate_hook_drop_inside_clip(t_sec: float, st: ChatState) -> bool:
        cs = float(st.user_clip_start_sec or 0.0)
        ce = float(st.user_clip_end_sec or 0.0)
        if ce <= cs:
            return float(t_sec) >= 0.0
        return cs <= float(t_sec) <= ce

    async def _ask_subtitles_mode(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_SUBTITLES_MODE
        if not str(st.subtitles_mode or "").strip():
            st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND
        await self.store.set(st)
        # Send example videos for each mode (legacy hardcoded impulse/scenes/4th +
        # the registered trendy/brat reels from hook_previews.json).
        for btn_name, file_id in _SUBTITLES_EXAMPLE_VIDEO.items():
            await message.answer_video(video=file_id, caption=f"Пример: *{btn_name}*", parse_mode="Markdown")
        await self._send_option_previews(
            message, ["subtitles:trendy_5th", "subtitles:brat_5th"]
        )
        await message.answer(
            "Выбери режим субтитров:",
            reply_markup=_kb(
                [BTN_SUB_MODE_IMPULSE],
                [BTN_SUB_MODE_SCENES],
                [BTN_SUB_MODE_4TH],
                [BTN_SUB_MODE_TRENDY, BTN_SUB_MODE_BRAT],
            ),
        )

    def _reset_reuse_selection(self, st: ChatState) -> None:
        """Reuse-input («Сделать под тот же трек»): wipe the per-run *selection*
        (background / footage / vibe / hook / colors) so the user re-picks from
        a clean slate, while audio / lyrics / timing are preserved.

        Crucially this re-enters at the background step (_ask_bg_mode), NOT the
        legacy genre picker — otherwise reuse skipped the «Футажи / Цветной /
        Строб» choice and dropped straight into the old artist flow, bypassing
        the vibe bucket picker (and stale hook/color flags leaked forward)."""
        st.footage_genre_key = ""
        st.footage_artist_key = ""
        st.footage_artist_id = ""
        st.vibe_selected_ids = []
        st.hook_enabled = False
        st.hook_category = ""
        st.hook_device = ""
        st.hook_drop_t = None
        st.effect_hook = ""
        st.effect_transition = ""
        st.effect_extra = ""
        st.effect_extra_full = False
        st.effect_hook_extend = ""
        st.visual_transition = ""
        st.visual_style = ""
        st.visuals_done = False
        st.f1_sound_url = ""
        st.f1_sound_text = ""
        st.f2_shape = ""
        st.colors_done = False
        st.subtitle_color_hex = ""
        st.accent_color_hex = ""
        st.versions_count = 1

    async def _handle_wait_audio(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_ALL_PACKAGES:
            await self._show_all_packages(message, st)
            return
        if text == BTN_REUSE_INPUT:
            if not self._can_reuse_input(st):
                await message.answer(
                    "Не вижу сохраненного трека. Нажми «Отправить трек» и пришли файл.",
                    reply_markup=self._wait_audio_reuse_kb(),
                )
                return
            st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND
            self._reset_reuse_selection(st)
            await self._ask_bg_mode(message, st)
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
                await self._notify_ops_alert(
                    title="Audio prepare telegram error",
                    chat_id=chat_id,
                    username=st.chat_username,
                    stage="audio_prepare",
                    error_text=str(e),
                    extra_lines=[f"file_name: {_safe_name(original_name)}"],
                )
                await message.answer(_AUDIO_PREPARE_TG_FAILED_USER_TEXT)
            return
        except Exception as e:
            log.exception(
                "audio_prepare_failed chat=%s file_id=%s name=%s err=%s",
                chat_id,
                file_id,
                original_name,
                str(e),
            )
            await self._notify_ops_alert(
                title="Audio prepare failure",
                chat_id=chat_id,
                username=st.chat_username,
                stage="audio_prepare",
                error_text=str(e),
                extra_lines=[f"file_name: {_safe_name(original_name)}"],
            )
            await message.answer(_AUDIO_PREPARE_FAILED_USER_TEXT)
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
        st.bg_mode = "footage"
        st.bg_solid_color = ""
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
        st.stage = STAGE_WAIT_LYRICS_TEXT
        await self.store.set(st)

        await message.answer(
            "Трек готов! Пришли текст песни обычным сообщением. "
            "Он нужен для точной синхронизации субтитров с аудио.",
            reply_markup=ReplyKeyboardRemove(),
        )

    async def _ensure_prepared_audio_for_confirm(self, *, message: Message, st: ChatState) -> Path | None:
        prepared_raw = str(st.prepared_audio_local_path or "").strip()
        if prepared_raw:
            prepared_path = Path(prepared_raw).expanduser().resolve()
            if prepared_path.exists():
                return prepared_path

        file_id = str(st.pending_audio_file_id or "").strip()
        original_name = str(st.pending_audio_filename or "").strip() or "audio.mp3"
        if not file_id or message.chat is None:
            log.warning(
                "prepared_audio_missing_unrecoverable chat=%s has_file_id=%s path=%r",
                st.chat_id,
                bool(file_id),
                prepared_raw,
            )
            return None

        chat_id = int(message.chat.id)
        incoming_dir = self.settings.tmp_dir / str(chat_id) / "incoming"
        prepared_dir = self.settings.tmp_dir / str(chat_id) / "prepared"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        prepared_dir.mkdir(parents=True, exist_ok=True)
        src_name = f"{_now_tag()}_{uuid.uuid4().hex[:8]}_{_safe_name(original_name)}"
        src_path = incoming_dir / src_name

        log.warning(
            "prepared_audio_missing_recover_start chat=%s file_name=%s path=%r",
            chat_id,
            original_name,
            prepared_raw,
        )
        try:
            await message.answer("Восстанавливаю подготовленный трек…")
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
        except Exception as e:
            log.exception(
                "prepared_audio_recover_failed chat=%s file_id=%s name=%s err=%s",
                chat_id,
                file_id,
                original_name,
                str(e),
            )
            return None

        recovered_path = Path(prep.output_path).expanduser().resolve()
        st.prepared_audio_local_path = str(recovered_path)
        await self.store.set(st)
        await self.credits_db.log_event(chat_id, "audio_recovered", original_name)
        log.info("prepared_audio_recover_ok chat=%s path=%s", chat_id, recovered_path)
        return recovered_path

    async def _handle_wait_lyrics_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text and text not in {BTN_SEND_LYRICS, BTN_SKIP_LYRICS} and not _is_control_button_text(text):
            await self._handle_wait_lyrics_text(message, st)
            return

        if text == BTN_SEND_LYRICS:
            st.stage = STAGE_WAIT_LYRICS_TEXT
            await self.store.set(st)
            await message.answer(
                "Пришли текст песни обычным сообщением (не кнопкой).",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if text == BTN_SKIP_LYRICS:
            st.stage = STAGE_WAIT_LYRICS_TEXT
            await self.store.set(st)
            await message.answer(
                "Теперь запускаем генерацию только с текстом песни — пришли его обычным сообщением.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        st.stage = STAGE_WAIT_LYRICS_TEXT
        await self.store.set(st)
        await message.answer(
            "Пришли текст песни обычным сообщением.",
            reply_markup=ReplyKeyboardRemove(),
        )

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
        # No fork: go straight to the "paste the lines" step (the "let AI decide"
        # branch was removed — the fragment is now always user-supplied).
        st.stage = STAGE_WAIT_FRAGMENT_TEXT
        await self.store.set(st)
        # Phase 2b: kick off the footage-bucket ranker in the background now that
        # we have lyrics. By the time the user reaches the "Футажи" step the
        # ranked shortlist is ready (zero added latency). No-op when flow is off.
        await self._trigger_vibe_ranker_task(st)
        await message.answer(
            "Скопируй и пришли нужные строки прямо из текста песни — те слова, "
            "которые хочешь видеть в клипе. Например — припев трека, без "
            "пояснительных слов типа «куплет:» и т.п.",
            reply_markup=ReplyKeyboardRemove(),
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
            st.stage = STAGE_WAIT_LYRICS_TEXT
            await self.store.set(st)
            await message.answer(
                "Пришли текст песни обычным сообщением.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        await message.answer("Выбери: «Да» или «Вернуться назад».", reply_markup=_kb([BTN_CONFIRM_YES, BTN_CONFIRM_BACK]))

    async def _handle_wait_subtitles_mode(self, message: Message, st: ChatState) -> None:
        mode = _parse_subtitles_mode_choice(message.text or "")
        if mode is None:
            await message.answer(
                "Выбери режим кнопкой: «Impulse», «Jakson», «Tape», «Trendy» или «Brat»."
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

    def _hook_summary_line(self, st: ChatState) -> str:
        if not st.hook_enabled:
            return "*Хук:* нет"
        cat = {
            "sound": "Звук", "object": "Объект", "effect": "Эффект",
            "motion": "Движение", "thought": "Мысль",
        }.get(st.hook_category, self._esc_md(st.hook_category or "—"))
        # Echo the picks in Russian (state holds pipeline ids). Drop timing is
        # shown on its own «Тайминг дропа» line, so it's not repeated here.
        extra = ""
        if st.hook_category == "motion" and st.hook_device:
            extra = f" / {self._esc_md(_F4_DEVICE_RU_BY_ID.get(st.hook_device, st.hook_device))}"
        elif st.hook_category == "thought" and st.hook_device:
            extra = f" / {self._esc_md(_F5_DEVICE_RU_BY_ID.get(st.hook_device, st.hook_device))}"
        elif st.hook_category == "object" and st.f2_shape:
            extra = f" / {self._esc_md(_F2_SHAPE_RU_BY_ID.get(st.f2_shape, st.f2_shape))}"
        elif st.hook_category == "sound":
            extra = " / звук" + (" + субтитр" if st.f1_sound_text else "")
        elif st.hook_category == "effect":
            fx: List[str] = []
            if st.effect_hook:
                fx.append(_F3_HOOK_RU_BY_ID.get(st.effect_hook, st.effect_hook))
            if st.effect_transition:
                fx.append(_F3_TRANSITION_RU_BY_ID.get(st.effect_transition, st.effect_transition))
            if st.effect_extra:
                fx.append(_F3_EXTRA_RU_BY_ID.get(st.effect_extra, st.effect_extra))
            if fx:
                extra = " / " + ", ".join(self._esc_md(x) for x in fx)
        return f"*Хук:* «{cat}»{extra}"

    def _color_summary_line(self, st: ChatState) -> str:
        parts: List[str] = []
        if st.subtitle_color_hex:
            parts.append(f"субтитры {st.subtitle_color_hex}")
        if st.accent_color_hex:
            parts.append(f"акцент {st.accent_color_hex}")
        return ("*Цвета:* " + ", ".join(parts)) if parts else ""

    def _sources_summary_line(self, st: ChatState) -> str:
        """Confirm-summary line for the footage source. Prefers the explicit vibe
        multi-select (labels the user actually picked); falls back to the legacy
        artist/source label when the vibe flow wasn't used."""
        sel = list(st.vibe_selected_ids or [])
        if sel:
            labels = [
                _vibe_display_label(st.vibe_labels_by_id.get(b, b)) for b in sel
            ]
            labels = [lbl for lbl in labels if lbl]
            if labels:
                return "*Вайбы:* " + self._esc_md(", ".join(labels))
        return f"*Исходники:* «{self._esc_md(self._source_label(st))}»"

    def _final_confirm_text(self, st: ChatState, *, versions: Optional[int] = None) -> str:
        mode_display = self._esc_md(_BUTTON_BY_SUBTITLES_MODE.get(st.subtitles_mode, st.subtitles_mode))
        fragment_display = self._esc_md(st.target_fragment or "весь трек")
        version_count = versions if versions is not None else int(st.versions_count or 1)
        # Grouped, spaced layout so the user can scan the summary at a glance.
        lines: List[str] = [
            "*Отрывок текста:*",
            fragment_display,
            "",
            f"*Тайминг отрывка:* {self._esc_md(self._timing_label(st))}",
        ]
        if HOOK_FLOW_ENABLED and st.hook_enabled and st.hook_drop_t is not None:
            lines.append(f"*Тайминг дропа:* {self._fmt_timing(float(st.hook_drop_t))}")
        lines.append("")
        lines.append(self._sources_summary_line(st))
        lines.append(f"*Режим субтитров:* «{mode_display}»")
        # Hook/color echo only when the hook flow is active.
        if HOOK_FLOW_ENABLED:
            lines.append(self._hook_summary_line(st))
            cl = self._color_summary_line(st)
            if cl:
                lines.append(cl)
        lines.append("")
        lines.append(f"*Количество версий:* {version_count} видео")
        lines.append("")
        lines.append("Запустить генерацию?")
        return "\n".join(lines)

    async def _ask_strobe_cut(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_STROBE_CUT
        await self.store.set(st)
        await self._send_option_previews(
            message, [f"effect_transition:{v}" for v in _FX_TRANSITION_BY_BUTTON.values()]
        )
        label = "Строб Ч/Б" if st.bg_mode == "solid_strobe" else "Цветной фон"
        await message.answer(
            f"{label}: стиль перехода на склейках видео.\n"
            "Снап-вайп / Минимакс / Инверт / Экстракт / Вспышки — или без перехода.",
            reply_markup=_kb(
                [BTN_FX_TR_SNAP, BTN_FX_TR_MINIMAX],
                [BTN_FX_TR_INVERT, BTN_FX_TR_EXTRACT],
                [BTN_FX_TR_FLASH],
                [BTN_FX_SKIP],
                [BTN_BACK],
            ),
        )

    def _default_strobe_drop(self, st: ChatState) -> float:
        cs = float(st.user_clip_start_sec or 0.0)
        ce = float(st.user_clip_end_sec or 0.0)
        cand = None
        for c in (st.hook_drop_candidates or []):
            try:
                cand = float(c.get("t"))
                break
            except (TypeError, ValueError):
                continue
        drop = cand if (cand is not None and cs < cand < ce) else (cs + 1.0)
        if ce > cs:
            drop = min(max(drop, cs + 0.5), ce - 0.1)
        return float(drop)

    async def _handle_wait_strobe_cut(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_subtitles_mode(message, st)
            return
        if text == BTN_FX_SKIP:
            st.visual_transition = ""
        else:
            tr = _FX_TRANSITION_BY_BUTTON.get(text)
            if tr is None:
                await message.answer("Выбери стиль кнопкой или «Пропустить».")
                return
            st.visual_transition = tr
        st.visual_style = ""
        st.visuals_done = True
        st.hook_enabled = False
        st.hook_category = ""
        st.hook_drop_t = None
        st.colors_done = True
        await self.store.set(st)
        await self._proceed_to_versions_or_confirm(message, st)

    async def _proceed_to_versions_or_confirm(self, message: Message, st: ChatState) -> None:
        """Post-settings entry: paid → version count picker, else → final confirm.

        Customization (colors) runs AFTER the hook flow — if it hasn't run yet
        this redirects into the color pickers once, then comes back here."""
        # Strobe bg: skip color pickers (white auto-invert) + offer ONLY the cut
        # transition style once (not the full hook flow).
        if st.bg_mode in {"solid", "solid_strobe"} and not st.visuals_done and st.stage != STAGE_WAIT_STROBE_CUT:
            await self._ask_strobe_cut(message, st)
            return
        if st.bg_mode == "footage" and not st.visuals_done and st.stage not in {
            STAGE_WAIT_VISUAL_TRANSITION, STAGE_WAIT_VISUAL_STYLE
        }:
            await self._ask_visual_transition(message, st)
            return
        if HOOK_FLOW_ENABLED and not st.colors_done:
            await self._ask_subtitle_color(message, st)
            return
        paid = await self.credits_db.has_paid(st.chat_id)
        if paid:
            st.stage = STAGE_WAIT_VERSIONS
            await self.store.set(st)
            await message.answer(
                "Сколько версий сгенерировать?",
                reply_markup=_kb(["1", "2", "3", "4", "5"]),
            )
            return
        st.versions_count = 1
        st.stage = STAGE_WAIT_CONFIRM
        await self.store.set(st)
        await message.answer(
            self._final_confirm_text(st),
            parse_mode="Markdown",
            reply_markup=_kb([BTN_LAUNCH, BTN_RESTART]),
        )

    async def _handle_wait_confirm_mode(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_CONFIRM_YES:
            # Strobe bg: NO hook flow and NO color pickers. The strobe already
            # IS the effect (B/W flip on cuts); text is forced white + auto-
            # inverts. The ONLY strobe setting is the cut-transition style,
            # asked once by _proceed_to_versions_or_confirm. Routing through the
            # full hook flow here duplicated it (hook prompt + then cut prompt).
            if st.bg_mode in {"solid", "solid_strobe"}:
                st.colors_done = False
                await self.store.set(st)
                await self._proceed_to_versions_or_confirm(message, st)
                return
            # Hook flow first; customization (colors) runs after it (see
            # _proceed_to_versions_or_confirm). Legacy path when the flag is off.
            if HOOK_FLOW_ENABLED:
                st.colors_done = False
                await self.store.set(st)
                await self._ask_hook_choice(message, st)
            else:
                await self._proceed_to_versions_or_confirm(message, st)
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
        await message.answer(
            self._final_confirm_text(st, versions=int(n)),
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
        if not self._has_forced_alignment_reference_text(st):
            st.stage = STAGE_WAIT_LYRICS_TEXT
            await self.store.set(st)
            await message.answer(
                "Для запуска нужен текст песни. Пришли его обычным сообщением.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        # Check and reserve credits before generation (1 credit per version)
        versions = max(1, min(5, int(st.versions_count or 1)))
        balance = await self.credits_db.get_balance(user_id)
        if balance < versions:
            await self.credits_db.log_event(chat_id, "no_credits")
            await message.answer(
                "Твои кредиты закончились. Хочешь посмотреть тарифы?\n\n"
                "/packages — посмотреть тарифы",
                reply_markup=_kb([BTN_ALL_PACKAGES]),
            )
            st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            return

        prepared_path = await self._ensure_prepared_audio_for_confirm(message=message, st=st)
        if prepared_path is None:
            await self.credits_db.log_event(
                chat_id,
                "generation_prepare_missing",
                "prepared_mp3_missing_or_unrecoverable",
            )
            await message.answer("Подготовленный mp3 не найден. Пришли трек заново.")
            await self._move_to_wait_audio(chat_id, message)
            return

        # Gate on the unique-track quota before touching credits: a reused
        # (already-known) track never spends a slot; a brand-new track does,
        # and is blocked outright if the tariff's track quota is exhausted.
        # Fail closed on a hashing error — never let it bypass the track gate.
        try:
            audio_hash = await asyncio.to_thread(self._sha256_file, prepared_path)
        except Exception as hash_e:
            log.warning("audio_hash_gate_failed chat=%s err=%s", chat_id, str(hash_e))
            await message.answer(
                "Не удалось обработать трек. Пришли файл ещё раз.",
                reply_markup=self._wait_audio_reuse_kb(),
            )
            return
        is_known_track = await self.credits_db.has_track_hash(chat_id, audio_hash)
        if not is_known_track and await self.credits_db.get_track_balance(chat_id) < 1:
            await self.credits_db.log_event(chat_id, "no_track_slots")
            await message.answer(
                "Лимит уникальных треков на твоём тарифе исчерпан. "
                "Пришли уже использованный трек ещё раз или обнови тариф — "
                "количество треков увеличится.\n\n"
                "/packages — посмотреть тарифы",
                reply_markup=_kb([BTN_ALL_PACKAGES]),
            )
            st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            return

        # Reserve credits only after we have a prepared mp3 on current node.
        deducted_versions = 0
        for _ in range(versions):
            ok = await self.credits_db.deduct_credit(chat_id)
            if not ok:
                break
            deducted_versions += 1
        if deducted_versions != versions:
            if deducted_versions > 0:
                try:
                    await self.credits_db.add_credits(
                        chat_id,
                        int(deducted_versions),
                        "generation_failed_refund",
                        admin_note="batch=reserve_partial",
                    )
                except Exception as add_e:
                    log.warning("reserve_partial_refund_failed chat=%s err=%s", chat_id, str(add_e))
            await self.credits_db.log_event(
                chat_id,
                "generation_failed",
                "job=enqueue_start stage=reserve_credits",
            )
            await message.answer("Не удалось зарезервировать генерации. Нажми «Запустить» еще раз.")
            return
        await self.credits_db.log_event(chat_id, "credits_reserved", f"versions={versions}")

        # Spend the track slot now (right before the point of no return). A
        # "blocked" result here means another concurrent request took the
        # last slot between the check above and here — refund the reserved
        # credits and bail, same as the reserve_partial_refund path above.
        track_status = await self.credits_db.consume_track_slot(chat_id, audio_hash)
        if track_status == "blocked":
            try:
                await self.credits_db.add_credits(
                    chat_id,
                    int(versions),
                    "generation_failed_refund",
                    admin_note="batch=no_track_slots",
                )
            except Exception as add_e:
                log.warning("track_slot_refund_failed chat=%s err=%s", chat_id, str(add_e))
            await self.credits_db.log_event(chat_id, "no_track_slots")
            await message.answer(
                "Лимит уникальных треков на твоём тарифе исчерпан. "
                "Пришли уже использованный трек ещё раз или обнови тариф.\n\n"
                "/packages — посмотреть тарифы",
                reply_markup=_kb([BTN_ALL_PACKAGES]),
            )
            st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            return

        key = self._build_raw_audio_key(chat_id=chat_id, file_name=prepared_path.name)
        try:
            versions = max(1, min(5, int(st.versions_count or 1)))
            await message.answer("Запускаю генерацию…")
            audio_s3_url = await asyncio.to_thread(
                self.s3.upload_file,
                path=prepared_path,
                bucket=self.settings.s3_bucket_raw_audio,
                key=key,
                content_type="audio/mpeg",
            )

            batch_id = f"tg-{chat_id}-{uuid.uuid4().hex[:12]}"
            st.generation_run_id = await self._runtime_start_run(
                st=st,
                batch_id=batch_id,
                audio_s3_url=audio_s3_url,
                versions_total=versions,
            )
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
            st.last_backpressure_notice = ""
            st.poll_attempts = 0
            st.last_job_stage = ""
            st.last_job_error = ""
            st.last_result_url = ""

            initial_rows = [
                {"job_id": master_job_id, "status": "QUEUED", "stage": "build", "error": "", "version": 1}
            ]
            st.last_backpressure_notice = await self._current_backpressure_notice()
            initial_text = self._jobs_progress_message(
                rows=initial_rows,
                poll_attempts=0,
                total_versions=versions,
                backpressure_notice=st.last_backpressure_notice,
                queue_estimate=await self._queue_estimate_for_rows(initial_rows),
            )
            sent = await message.answer(initial_text)
            st.status_message_id = int(getattr(sent, "message_id", 0) or 0)
            st.last_status_text = initial_text
            st.last_status_msg_at = time.time()
            await self.store.set(st)
        except Exception as e:
            err_text = str(e)
            if deducted_versions > 0:
                try:
                    await self.credits_db.add_credits(
                        chat_id,
                        int(deducted_versions),
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
                refunded_versions=int(deducted_versions),
            )
            await message.answer(_GENERATION_FAILED_USER_TEXT, reply_markup=self._wait_audio_reuse_kb())
            await self._runtime_update_run(
                st=st,
                status="failed",
                current_stage="enqueue_start",
                last_error_code="enqueue_start_failed",
                last_error_text=err_text,
            )
            self._reset_processing_state(st, next_stage=STAGE_WAIT_AUDIO)
            await self.store.set(st)

    async def _handle_wait_next(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()

        if text == BTN_REUSE_INPUT:
            if not self._can_reuse_input(st):
                await message.answer(
                    "Не вижу сохранённого трека. Нажми «Отправить трек» и пришли файл.",
                    reply_markup=_kb([BTN_NEXT]),
                )
                return
            if message.chat is None:
                return
            st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND
            self._reset_reuse_selection(st)
            await self._ask_bg_mode(message, st)
            return

        can_reuse = self._can_reuse_input(st)
        if text != BTN_NEXT:
            await message.answer(
                "Если хочешь новый ролик, нажми «Сделать следующий».\n\n"
                "/sendtrack — отправить трек\n"
                "/packages — посмотреть тарифы",
                reply_markup=_kb([BTN_NEXT], [BTN_REUSE_INPUT]) if can_reuse else _kb([BTN_NEXT]),
            )
            return

        if message.chat is None:
            return

        await self._move_to_wait_audio(int(message.chat.id), message)

    # ── Package descriptions ──────────────────────────────────────────
    # Card images as Telegram photo file_ids (Trial dropped from the UI).
    _PKG_PHOTOS = {
        BTN_PKG_BLAST: "AgACAgIAAxkBAAEB-lRqS3TXIZtZYKPuCIPVRNwq0VbFFgACIh1rG3C7YEo3e6VUDRm8YQEAAwIAA3gAAzwE",
        BTN_PKG_GLOW: "AgACAgIAAxkBAAEB-nBqS4y3js5NA102ZOI0xkD230J_bAACzR1rG3C7YErGuRJtKFP7zwEAAwIAA3kAAzwE",
        BTN_PKG_IMPULSE: "AgACAgIAAxkBAAEB-nJqS4zFo7igpAQTIt6NRJrGILOHYwACzh1rG3C7YEpbGqYB1yLB9AEAAwIAA3kAAzwE",
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
            "Отправляешь трек и текст — ИИ генерирует 100 видео под твой звук(и).\n\n"
            "Держишь соцсети живыми, органически растишь нужную аудиторию "
            "и повышаешь шансы попасть в тренд.\n\n"
            "Нажимай «Приобрести» — и соцсети работают на тебя каждый месяц."
        ),
        BTN_PKG_GLOW: (
            "Глоу — 7 990₽\n\n"
            "Глубокое тестирование трека с подключением CapCut шаблона.\n\n"
            "ИИ генерирует контент-план и 400 видео под твой звук(и) — мы выявляем "
            "лучшие форматы, затем контент-менеджер запускает твой личный CapCut "
            "шаблон на основе успешного формата.\n\n"
            "Условия:\n"
            "— 400 генераций видео\n"
            "— до 10 треков к загрузке\n"
            "— генерация роликов пачками и расширенный тайминг видео\n\n"
            "Максимум данных, реальный охват и шанс задать тренд. Нажимай "
            "«Приобрести» — запусти рост трека."
        ),
        BTN_PKG_IMPULSE: (
            "Импульс — 29 990₽\n\n"
            "Контент без ограничений и головной боли на 1 год. Ты обеспечишь себя "
            "контентом и видео на каждый день, чтобы развить любую соц. сеть.\n\n"
            "Условия:\n"
            "— генерация видео без лимитов\n"
            "— до 24 треков к загрузке\n"
            "— персональная аналитика\n\n"
            "В итоге — проверенный материал, большой пул контента и шанс создать "
            "тренд. Нажимай «Приобрести»."
        ),
    }

    # Cards temporarily disabled (tariffs mid-refresh, no card file_ids yet).
    # Flip to True once the new card assets/file_ids are wired.
    _PKG_CARDS_ENABLED = True

    async def _send_package_photo(self, *, chat_id: int, package_name: str, op: str) -> bool:
        if not self._PKG_CARDS_ENABLED:
            return False
        file_id = self._PKG_PHOTOS.get(str(package_name or ""))
        if not file_id:
            return False
        try:
            await self._timed_send_photo(
                bot=self._require_bot(),
                chat_id=int(chat_id),
                photo=file_id,
                op=op,
            )
            return True
        except Exception as e:
            log.warning("pkg_photo_send_failed pkg=%s err=%s", package_name, str(e))
            return False

    async def _send_package_overview_photos(self, *, chat_id: int) -> None:
        sent = 0
        # Trial dropped from the UI enumeration (its billing infra stays intact).
        for package_name in (BTN_PKG_BLAST, BTN_PKG_GLOW, BTN_PKG_IMPULSE):
            if await self._send_package_photo(
                chat_id=int(chat_id),
                package_name=package_name,
                op="packages_overview_photo",
            ):
                sent += 1
        log.info("pkg_overview_photos_done chat=%s sent=%d total=3", int(chat_id), sent)

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

    async def _notify_ops_alert(
        self,
        *,
        title: str,
        chat_id: int = 0,
        username: str = "",
        job_id: str = "",
        stage: str = "",
        error_text: str = "",
        extra_lines: Optional[List[str]] = None,
        raise_on_error: bool = False,
    ) -> bool:
        token = str(self.settings.alert_telegram_bot_token or "").strip()
        target_chat_id = str(self.settings.alert_telegram_chat_id or "").strip()
        if not token or not target_chat_id:
            return False

        clean_username = str(username or "").strip().lstrip("@")
        lines = [f"⚠️ {title}"]
        if clean_username:
            lines.append(f"user: @{clean_username}")
        if chat_id:
            lines.append(f"chat_id: {chat_id}")
        if job_id:
            lines.append(f"job_id: {job_id}")
        if stage:
            lines.append(f"stage: {stage}")
        for raw in list(extra_lines or []):
            txt = str(raw or "").strip()
            if txt:
                lines.append(txt)
        if error_text:
            lines.extend(["", f"error: {_compact_text(error_text, limit=1200)}"])

        payload = {
            "chat_id": target_chat_id,
            "text": "\n".join(lines)[:3500],
            "disable_web_page_preview": True,
        }
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        tg_proxy = str(self.settings.tg_file_proxy_url or "").strip()
        client_kwargs: Dict[str, Any] = {"timeout": 10.0}
        if tg_proxy:
            client_kwargs["proxy"] = tg_proxy
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code >= 300:
                raise RuntimeError(f"status={resp.status_code} body={resp.text[:300]}")
            return True
        except Exception as e:
            log.warning("ops_alert_notify_failed title=%s err=%s", title, str(e))
            if raise_on_error:
                raise
        return False

    async def _notify_manager_generation_error(self, *, username: str, chat_id: int, job_id: str, stage: str, error_text: str) -> None:
        await self._notify_ops_alert(
            title="Public bot generation error",
            chat_id=chat_id,
            username=username,
            job_id=job_id,
            stage=stage,
            error_text=error_text,
        )
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
        strict: bool = False,
    ) -> None:
        username = str(st.chat_username or "").strip()
        uname = f"@{username}" if username else "(нет username)"
        err_short = _compact_text(error_text or "без деталей", limit=700)
        await self._notify_ops_alert(
            title="Public bot generation failure",
            chat_id=st.chat_id,
            username=username,
            job_id=job_id,
            stage=stage,
            error_text=error_text,
            extra_lines=[
                f"succeeded_versions: {succeeded_versions}/{total_versions}",
                f"refunded_versions: {refunded_versions}",
            ],
            raise_on_error=strict,
        )
        mgr = self.settings.manager_chat_id
        if not mgr:
            return
        bot = self._require_bot()
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
            if strict:
                raise

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

    async def _show_all_packages(self, message: Message, st: ChatState, *, include_photos: bool = True) -> None:
        await self.credits_db.log_event(st.chat_id, "view_packages")
        st.stage = STAGE_ALL_PACKAGES
        await self.store.set(st)
        if include_photos:
            await self._send_package_overview_photos(chat_id=st.chat_id)
        await self._timed_answer(
            message,
            "Вот пул пакетов:\n"
            "— Бласт за 1 990₽/мес | 100 роликов\n"
            "— Глоу за 7 990₽ | 400 роликов\n"
            "— Импульс за 29 990₽ | безлимит роликов\n\n"
            "О каком рассказать подробнее?\n\n"
            "/sendtrack — вернуться к генерации\n"
            "/cancelsubscription — отменить подписку",
            op="packages_list",
            reply_markup=_kb([BTN_PKG_BLAST], [BTN_PKG_GLOW], [BTN_PKG_IMPULSE]),
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
        "Бласт": 100,
        "Глоу": 400,
        # Импульс = видео без ограничения; сентинел вместо реального счётчика,
        # чтобы не трогать существующие проверки/показ баланса кредитов.
        "Импульс": 100_000,
    }

    # Лимит уникальных треков на тариф (топ-ап при каждой покупке/продлении,
    # как и _PKG_CREDITS — не сбрасывается, а прибавляется). Триала нет
    # намеренно — пакет убирается.
    _PKG_TRACKS = {
        "Бласт": 4,
        "Глоу": 10,
        "Импульс": 24,
    }

    async def _show_purchase_stub(self, message: Message, st: ChatState, recurrent: bool = False) -> None:
        username = (st.chat_username or "").lstrip("@") or str(st.chat_id)
        pkg = st.selected_package or "не указан"
        event = "purchase_intent_recurrent" if recurrent else "purchase_intent"
        await self.credits_db.log_event(st.chat_id, event, pkg)

        price = self._PKG_PRICES.get(pkg, 0)

        # Try to create T-Bank payment link
        if self.tbank and price > 0:
            suffix = "sub" if recurrent else ""
            order_id = f"{st.chat_id}-{pkg.replace(' ', '_')}-{suffix}{uuid.uuid4().hex[:8]}"
            try:
                last_utm = await self.credits_db.get_last_utm(st.chat_id)
                if recurrent:
                    await self.credits_db.create_recurrent_payment(order_id, st.chat_id, price, pkg, utm=last_utm)
                else:
                    await self.credits_db.create_payment(order_id, st.chat_id, price, pkg, utm=last_utm)
                pay_url = await self.tbank.create_payment(
                    amount_rub=price,
                    order_id=order_id,
                    description=f"Подписка «{pkg}»" if recurrent else f"Пакет «{pkg}»",
                    recurrent=recurrent,
                    customer_key=str(st.chat_id) if recurrent else "",
                )
                if pay_url:

                    buttons = [
                        [InlineKeyboardButton(text=f"Оплатить {price:,}₽".replace(",", "."), url=pay_url)],
                    ]
                    if self.settings.offer_url:
                        buttons.append([InlineKeyboardButton(text="Договор оферты", url=self.settings.offer_url)])
                    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
                    price_str = f"{price:,}".replace(",", ".")
                    if recurrent:
                        await message.answer(
                            f"Подписка активирована! «{pkg}» — {price_str}₽/мес.\n\n"
                            "Нажми кнопку ниже для оплаты. После успешной оплаты кредиты "
                            "начислятся автоматически.\n\n"
                            "У нас все официально: прозрачный эквайринг и, конечно, чек об оплате.",
                            reply_markup=_kb([BTN_BACK]),
                        )
                    else:
                        await message.answer(
                            f"Отлично! Пакет «{pkg}» — {price_str}₽.\n\n"
                            "Нажми кнопку ниже для оплаты. После успешной оплаты кредиты "
                            "начислятся автоматически.\n\n"
                            "У нас все официально: прозрачный эквайринг и, конечно, чек об оплате.",
                            reply_markup=_kb([BTN_BACK]),
                        )
                    await message.answer(
                        "Ссылка на оплату:",
                        reply_markup=kb,
                    )
                    status_label = "Подписка создана" if recurrent else "Создан"
                    await self._notify_manager_payment(username, pkg, price, status_label)
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
            st.stage = STAGE_IMPROVEMENT_FEEDBACK
            await self.store.set(st)
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Субтитры", callback_data="improve:subtitles"),
                    InlineKeyboardButton(text="Исходники", callback_data="improve:sources"),
                    InlineKeyboardButton(text="Переходы", callback_data="improve:transitions"),
                    InlineKeyboardButton(text="Другое", callback_data="improve:other"),
                ],
            ])
            await message.answer(
                "Спасибо за честность! Мы хотим, чтобы следующий ролик зашёл сильнее. "
                "Что бы ты поменял в первую очередь?",
                reply_markup=kb,
            )
        elif text in {BTN_RATE_MID_HIGH, BTN_RATE_HIGH}:
            await self.credits_db.log_event(st.chat_id, "rate_video", "high")
            st.last_rating = "high"
            st.stage = STAGE_SALES_PITCH
            await self.store.set(st)
            await message.answer(
                "Отлично, значит мы попали!\n\n"
                "Это всего один ролик — а теперь представь, что это не разовая "
                "проба, а поток: 3-4 таких ролика каждый день, весь месяц, под "
                "твои треки, в твоём стиле и вайбе.\n\n"
                "Без съёмок и монтажа — просто закидываешь треки, а Blast собирает "
                "контент.",
                reply_markup=_kb([BTN_WANT_THIS]),
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

    # --- Improvement feedback (5-6 rating) ---
    async def _handle_improvement_feedback_text(self, message: Message, st: ChatState) -> None:
        """Handle text messages while waiting for inline button press."""
        await message.answer("Выбери один из вариантов кнопкой выше.")

    # --- Improvement feedback (5-6 rating, "Другое" text input) ---
    async def _handle_improvement_other_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if not text:
            await message.answer("Напиши, что бы изменил — мы учтём.")
            return
        await self.credits_db.log_event(
            st.chat_id, "improvement_feedback",
            f"rating=5-6 area=other text={text}",
        )
        await self._send_improvement_thanks(message, st)

    async def _send_improvement_thanks(self, message: Message, st: ChatState) -> None:
        chat_id = st.chat_id
        bal = await self.credits_db.get_balance(chat_id)
        track_bal = await self.credits_db.get_track_balance(chat_id)
        tracks_note = f" Доступно уникальных треков: {track_bal}." if track_bal > 0 else ""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отправить трек", callback_data="sendtrack")],
        ])
        await message.answer(
            f"Записал! Мы постоянно докручиваем качество — и с каждым обновлением "
            f"ролики становятся точнее. А пока — у тебя ещё {bal} бесплатных генераций, "
            f"попробуй на другом треке.{tracks_note} Результат может быть совсем другим, "
            f"тк это итеративная работа. Особенно, если ты точно укажешь тайминг "
            f"и текст отрывка.",
            reply_markup=kb,
        )
        st.stage = STAGE_IDLE
        await self.store.set(st)

    # --- Sales pitch ("Как же?" / "Хочу так") ---
    async def _handle_sales_pitch(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text in {BTN_HOW_SO, BTN_WANT_THIS}:
            await self.credits_db.log_event(st.chat_id, "sales_pitch")
            st.stage = STAGE_PACKAGES_OFFER
            await self.store.set(st)
            await message.answer(
                "Соц. сети продвигают тех, кто выкладывает часто и стабильно!\n\n"
                "Чтобы попасть в этот ритм без выгорания: Blast — 100 роликов под "
                "твой стиль, жанр и настроение за 1 990₽ в месяц.\n\n"
                "Для сравнения: у монтажёра ролик такого уровня стоит от 300₽ — "
                "то есть за эти же деньги ты получишь в 15 раз больше контента:\n\n"
                "— регулярный, разнообразный контент без повторов\n"
                "— без съёмок, продюсеров и мишуры\n\n"
                "Рассказать больше про все плюшки?",
                reply_markup=_kb([BTN_TELL_MORE], [BTN_ALL_PACKAGES], [BTN_NOT_NOW]),
            )
        else:
            # Show the right button depending on last rating
            btn = BTN_WANT_THIS if st.last_rating == "high" else BTN_HOW_SO
            await message.answer(f"Нажми «{btn}»", reply_markup=_kb([btn]))

    # --- Packages offer (Рассказывайте / Все пакеты / Пока неактуально) ---
    async def _handle_packages_offer(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_TELL_MORE:
            st.stage = STAGE_PACKAGE_DETAILS
            await self.store.set(st)
            await message.answer(
                "Отлично! Уже в этом месяце:\n"
                "— 100 роликов в разных стилях\n"
                "— до 4 треков к загрузке\n"
                "— генерация роликов пачками и расширенный тайминг видео\n\n"
                "А дальше:\n"
                "— лимит по трекам растёт на 1 штуку каждый месяц\n"
                "— каждый третий месяц: безлимит на ролики\n"
                "— с релизом сайта — загрузка своих исходников, автопостинг и аналитика\n\n"
                "Готов попробовать всё за 1 990₽ в месяц?",
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
            # Бласт is subscription-only now (no one-time option).
            await self._show_subscription_confirm(message, st)
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
            await self._send_package_photo(chat_id=st.chat_id, package_name=text, op="package_detail_photo")
            await self._timed_answer(
                message,
                self._PKG_TEXTS[text] + "\n\n/sendtrack — вернуться к генерации",
                op="package_detail_text",
                reply_markup=_kb([BTN_TO_TARIFFS], [BTN_NOT_NOW], [BTN_PURCHASE]),
            )
        else:
            await self._show_all_packages(message, st, include_photos=False)

    # --- Package info (К тарифам / Пока неактуально / Приобрести) ---
    async def _handle_package_info(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_TO_TARIFFS:
            await self._show_all_packages(message, st)
        elif text == BTN_NOT_NOW:
            await self._show_why_not(message, st)
        elif text == BTN_PURCHASE:
            if st.selected_package == "Бласт":
                # Бласт is subscription-only now (no one-time option).
                await self._show_subscription_confirm(message, st)
            else:
                await self._show_purchase_stub(message, st)
                st.stage = STAGE_WAIT_PAYMENT
                await self.store.set(st)
        else:
            await message.answer(
                "Выбери из кнопок ниже.",
                reply_markup=_kb([BTN_TO_TARIFFS], [BTN_NOT_NOW], [BTN_PURCHASE]),
            )

    # --- Purchase choice: one-time vs subscription (Бласт only) ---
    async def _show_purchase_choice(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_PURCHASE_CHOICE
        await self.store.set(st)
        await message.answer(
            "Бласт можно приобрести разово или по подписке.\n\n"
            "В рамках подписки будут включены все плюшки, о которых мы говорили "
            "ранее, а при разовой оплате — нет.\n\n"
            "Какой вариант выбираешь?",
            reply_markup=_kb([BTN_BUY_ONCE], [BTN_BUY_SUBSCRIPTION]),
        )

    async def _handle_purchase_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BUY_ONCE:
            await self._show_purchase_stub(message, st)
            st.stage = STAGE_WAIT_PAYMENT
            await self.store.set(st)
        elif text == BTN_BUY_SUBSCRIPTION:
            await self._show_subscription_confirm(message, st)
        else:
            await message.answer(
                "Выбери вариант кнопкой.",
                reply_markup=_kb([BTN_BUY_ONCE], [BTN_BUY_SUBSCRIPTION]),
            )

    # --- Subscription confirm ---
    async def _show_subscription_confirm(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_SUBSCRIPTION_CONFIRM
        await self.store.set(st)
        await message.answer(
            "Подписка на Бласт — 1 990₽/мес.\n\n"
            "Списание 1 990₽ каждый месяц, доступно уже в этом месяце:\n"
            "— 100 роликов в разных стилях\n"
            "— до 4 треков к загрузке\n"
            "— генерация роликов пачками и расширенный тайминг видео\n\n"
            "А дальше:\n"
            "— лимит по трекам растёт на 1 штуку каждый месяц\n"
            "— каждый третий месяц: безлимит на ролики\n"
            "— с релизом сайта: загрузка своих исходников, автопостинг и аналитика\n\n"
            "Отмена в любой момент.\n\n"
            "Нажимая «Подтвердить», ты соглашаешься на ежемесячную подписку "
            "с автоматическим списанием 1 990₽/мес и условиями оферты.\n\n"
            "Отменить подписку можно в любой момент — /cancelsubscription",
            reply_markup=_kb([BTN_CONFIRM], [BTN_BACK]),
        )

    async def _handle_subscription_confirm(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_CONFIRM:
            await self._show_purchase_stub(message, st, recurrent=True)
            st.stage = STAGE_WAIT_PAYMENT
            await self.store.set(st)
        elif text == BTN_BACK:
            # No more purchase-choice fork — go back to the packages list.
            await self._show_all_packages(message, st)
        else:
            await message.answer(
                "Выбери кнопку: «Подтвердить» или «Назад».",
                reply_markup=_kb([BTN_CONFIRM], [BTN_BACK]),
            )

    # --- Wait payment (Назад → return to package info) ---
    async def _handle_wait_payment(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            pkg = st.selected_package or ""
            if pkg in self._PKG_TEXTS:
                st.stage = STAGE_PACKAGE_INFO
                await self.store.set(st)
                await message.answer(
                    self._PKG_TEXTS[pkg] + "\n\n/sendtrack — вернуться к генерации",
                    reply_markup=_kb([BTN_TO_TARIFFS], [BTN_NOT_NOW], [BTN_PURCHASE]),
                )
            else:
                await self._show_all_packages(message, st)
        else:
            await message.answer(
                "Ожидаем оплату. Нажми «Назад», чтобы вернуться к описанию пакета.",
                reply_markup=_kb([BTN_BACK]),
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
                referrer_st.generation_run_id = await self._runtime_start_run(
                    st=referrer_st,
                    batch_id=batch_id,
                    audio_s3_url=str(referrer_st.batch_audio_s3_url or ""),
                    versions_total=1,
                )
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
                referrer_st.last_backpressure_notice = ""
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
        elif text == BTN_RATE_MID_LOW:
            await self.credits_db.log_event(st.chat_id, "rate_video_2", "mid_low")
            st.last_rating = "mid"
            # 2nd gen 5-6 → referral + feedback form
            await self._show_referral_ask(message, st)
        elif text in {BTN_RATE_MID_HIGH, BTN_RATE_HIGH}:
            await self.credits_db.log_event(st.chat_id, "rate_video_2", "high")
            st.last_rating = "high"
            # 2nd gen 7+ → standard sales pitch
            st.stage = STAGE_SALES_PITCH
            await self.store.set(st)
            await message.answer(
                "Отлично, значит мы попали!\n\n"
                "Это всего один ролик — а теперь представь, что это не разовая "
                "проба, а поток: 3-4 таких ролика каждый день, весь месяц, под "
                "твои треки, в твоём стиле и вайбе.\n\n"
                "Без съёмок и монтажа — просто закидываешь треки, а Blast собирает "
                "контент.",
                reply_markup=_kb([BTN_WANT_THIS]),
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

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _build_referral_batch_id(self, chat_id: int) -> str:
        return f"tg-{int(chat_id)}-referral-round-2"

    @staticmethod
    def _build_batch_idempotency_key(*, chat_id: int, batch_id: str, version_index: int) -> str:
        return f"tg-{int(chat_id)}-batch-{str(batch_id or '').strip()}-v{int(version_index)}"

    async def _resolve_rotation_slot_for_enqueue(
        self, *, st: ChatState, offset: int = 0
    ) -> Tuple[str, str, List[str]]:
        """Return (theme, group, persistent_history_names) for the current user.

        Returns empty ("", "", []) when artist_id has no rotation slots
        (unknown artist or no themes) — callers should then skip override.

        `offset` spreads a multi-version batch across consecutive rotation slots:
        version 0 keeps the persisted cursor (the advance-on-exhaustion base),
        versions 1..N step forward so each batch video lands on a different
        subgroup instead of all sharing one slot.
        """
        # Phase 2b: when the vibe flow drove the footage choice, distribute the
        # N versions round-robin over the user-selected buckets (exact-slot
        # theme+tags_group each). offset == version_index (1-based) → (offset-1)
        # picks the top-ranked selected bucket for version 1, next for version 2…
        if FOOTAGE_VIBE_FLOW_ENABLED:
            sel = [s for s in (getattr(st, "vibe_selected_ids", None) or []) if ":" in s]
            if sel:
                # Phase 3 distribution rule: video[i] = selected[i % K]. offset is
                # the 1-based version_index → i = offset-1. resolve_bucket_slot
                # with catalog=[] forces the pure id-split (no footage_v2.py read
                # in the slim bot image).
                from mlcore.footage_batch_distribution import resolve_bucket_slot
                bucket_id = sel[(int(offset) - 1) % len(sel)]
                theme, group = resolve_bucket_slot(bucket_id, catalog=[])
                return theme, group, []
        artist_id = str(st.footage_artist_id or "").strip()
        if not artist_id:
            return "", "", []
        slots = get_artist_rotation_slots(artist_id)
        if not slots:
            return "", "", []
        cursor = await self.store.get_rotation_cursor(int(st.chat_id), artist_id)
        slot = slots[(int(cursor) + int(offset)) % len(slots)]
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
        # F4 «Движение»: reframe the clip so the overlay cover-end lands on the
        # drop (clip_start := drop - lead_eff; lead scales with bpm like the JSX).
        f4_device: str | None = None
        f4_bpm: float | None = None
        if st.hook_enabled and st.hook_category == "motion" and st.hook_device:
            if st.hook_drop_t is None:
                raise RuntimeError("F4 motion hook requires a drop (hook_drop_t)")
            bpm = float(st.hook_analysis_bpm or 0.0)
            if bpm <= 0.0:
                raise RuntimeError("F4 motion hook requires measured bpm (hook_analysis_bpm)")
            f4_device = str(st.hook_device)
            from mlcore.hooks.f4_motion.overlay import LEAD_BY_DEVICE
            if f4_device not in LEAD_BY_DEVICE:
                raise RuntimeError(f"unknown F4 device {f4_device!r}")
            # Early drop (drop < lead): clamp clip_start to 0 instead of erroring;
            # the overlay anchors the intro END on the drop (TOFF < 0) and AE clips
            # the pre-0 intro. No compression. (Parity mirror.)
            lead = self._f4_effective_lead(f4_device, bpm)
            new_start = max(0.0, float(st.hook_drop_t) - lead)
            user_clip_start_sec = new_start
            if user_clip_end_sec is None or user_clip_end_sec <= new_start:
                user_clip_end_sec = float(end)
            f4_bpm = bpm

        # F5 «Мысль»: reframe the clip toward the drop like F4 so the TTS voice
        # (~3s) plays in the run-up and lands INTO the drop, post-drop focus line
        # right after. clip_start := drop − F5_LEAD_SEC; clip_end stays. drop_at_sec
        # and the focus line follow this reframe orchestrator-side. (Parity mirror.)
        if st.hook_enabled and st.hook_category == "thought" and st.hook_device:
            if st.hook_drop_t is None:
                raise RuntimeError("F5 thought hook requires a drop (hook_drop_t)")
            # Adaptive lead: up to F5_LEAD_SEC of voice run-up, capped at the room
            # before the drop, so clip_start = max(0, drop − lead) ≥ 0 always and
            # F5 never hard-fails on an early drop. (Parity mirror.)
            lead_f5 = min(F5_LEAD_SEC, float(st.hook_drop_t))
            new_start = max(0.0, float(st.hook_drop_t) - lead_f5)
            user_clip_start_sec = new_start
            if user_clip_end_sec is None or user_clip_end_sec <= new_start:
                user_clip_end_sec = float(end)

        maintenance_bypass_token = ""
        allow_bypass = self._allow_maintenance_bypass_for_state(st)
        if (
            not allow_bypass
            and bool(getattr(self.settings, "tg_maintenance_allow_paid_clients", True))
            and int(st.chat_id or 0) > 0
        ):
            try:
                allow_bypass = await self.credits_db.has_paid(int(st.chat_id))
            except Exception as e:
                log.warning("maintenance_paid_client_check_failed chat=%s err=%r", st.chat_id, e)
        if allow_bypass:
            maintenance_bypass_token = str(self.settings.system_maintenance_bypass_token or "").strip()
        rotation_theme, rotation_group, rotation_history = (
            await self._resolve_rotation_slot_for_enqueue(st=st, offset=int(version_index))
        )
        merged_exclude_seen: set[str] = set()
        merged_exclude: List[str] = []
        for name in list(exclude_file_names or []) + list(rotation_history or []):
            clean = str(name or "").strip()
            if not clean or clean in merged_exclude_seen:
                continue
            merged_exclude_seen.add(clean)
            merged_exclude.append(clean)
        if not self._has_forced_alignment_reference_text(st):
            raise RuntimeError("public_bot_forced_alignment_requires_reference_text")
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
            exclude_file_names=merged_exclude,
            variant_index=int(version_index),
            variants_total=int(versions_total),
            maintenance_bypass_token=maintenance_bypass_token,
            rotation_theme=rotation_theme,
            rotation_tags_group=rotation_group,
            bg_mode=str(st.bg_mode or "footage"),
            bg_solid_color=str(st.bg_solid_color or ""),
            hook_enabled=bool(st.hook_enabled),
            user_drop_t=(float(st.hook_drop_t) if st.hook_drop_t is not None else None),
            hook_device=(
                str(st.hook_device)
                if (st.hook_enabled and st.hook_category == "thought" and st.hook_device)
                else None
            ),
            f4_device=f4_device,
            f4_bpm=f4_bpm,
            effect_hook=(
                str(st.effect_hook)
                if (st.hook_enabled and st.hook_category == "effect" and st.effect_hook)
                else None
            ),
            effect_transition=(str(st.visual_transition) if st.visual_transition else None),
            effect_extra=(str(st.visual_style) if st.visual_style else None),
            effect_extra_full=bool(st.visual_style),
            effect_hook_extend=(
                str(st.effect_hook_extend)
                if (st.hook_enabled and st.hook_category == "effect" and st.effect_hook_extend)
                else None
            ),
            f2_shape=(
                str(st.f2_shape)
                if (st.hook_enabled and st.hook_category == "object" and st.f2_shape)
                else None
            ),
            f1_sound_url=(
                str(st.f1_sound_url)
                if (st.hook_enabled and st.hook_category == "sound" and st.f1_sound_url)
                else None
            ),
            f1_sound_text=(
                str(st.f1_sound_text)
                if (
                    st.hook_enabled
                    and st.hook_category == "sound"
                    and st.f1_sound_url
                    and st.f1_sound_text
                )
                else None
            ),
            # Photo flow (4:3): stylization + transition, only when bg_mode=="photo".
            photo_style=(
                str(st.photo_style)
                if (st.bg_mode == "photo" and st.photo_style)
                else None
            ),
            photo_transition=(
                str(st.photo_transition)
                if (st.bg_mode == "photo" and st.photo_transition)
                else None
            ),
            # Strobe bg auto-inverts WHITE text (Difference) — ignore custom color.
            subtitle_color_hex=(None if st.bg_mode == "solid_strobe" else (str(st.subtitle_color_hex) or None)),
            accent_color_hex=(None if st.bg_mode == "solid_strobe" else (str(st.accent_color_hex) or None)),
            render_engine=(
                "rust-gen"
                if str(getattr(st, "render_engine", "") or "").strip().lower() == "rust-gen"
                else ("rust-gen" if self.settings.rust_gen_bot_default_enabled else "ae")
            ),
        )
        job_id = str(enqueue.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError(f"enqueue response has no job_id: {enqueue}")
        await self._runtime_attach_version(
            st=st,
            version_index=int(version_index),
            job_id=job_id,
            reuse_text_job_id=str(reuse_text_job_id or ""),
        )
        await self._runtime_update_run(
            st=st,
            current_stage="queued",
            next_version_to_enqueue=max(1, int(version_index) + 1),
        )
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

    async def _current_backpressure_notice(self) -> str:
        try:
            metrics = await self.orchestrator.get_metrics()
        except Exception:
            return ""
        capacity_policy = metrics.get("capacity_policy") if isinstance(metrics, dict) else {}
        if not isinstance(capacity_policy, dict):
            return ""
        state = str(capacity_policy.get("state") or "").strip().lower()
        if state in {"", "normal"}:
            return ""
        return str(capacity_policy.get("user_message") or "").strip()

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
        backpressure_notice: str = "",
        queue_estimate: Dict[str, Any] | None = None,
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

        lines = [
            "Прогресс:",
            f"{self._progress_bar(percent)} {percent}%",
        ]
        queue_lines = format_queue_estimate_lines(queue_estimate)
        if queue_lines:
            lines.extend(["", *queue_lines])
        note = str(backpressure_notice or "").strip()
        if note:
            lines.extend(["", note])
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

    def _reset_processing_state(self, st: ChatState, *, next_stage: str = STAGE_RATE_VIDEO) -> None:
        st.stage = str(next_stage)
        st.generation_run_id = ""
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
        st.last_backpressure_notice = ""
        st.poll_attempts = 0
        st.last_job_stage = ""
        st.last_job_error = ""
        st.footage_genre_key = ""
        st.footage_artist_key = ""
        st.footage_artist_id = ""
        st.bg_mode = "footage"
        st.bg_solid_color = ""
        st.subtitles_mode = SUBTITLES_MODE_IMPULSE_2ND

    async def _send_post_generation_message_best_effort(
        self,
        *,
        bot: Bot,
        st: ChatState,
        text: str,
        reply_markup=None,
        context: str,
    ) -> bool:
        try:
            await bot.send_message(st.chat_id, text, reply_markup=reply_markup)
            return True
        except Exception as exc:
            log.warning(
                "post_generation_message_send_failed chat=%s context=%s err=%s",
                st.chat_id,
                context,
                str(exc),
            )
            if self._runtime_outbox_terminal_error(exc):
                try:
                    await self.credits_db.log_event(
                        st.chat_id,
                        "bot_blocked",
                        f"post_generation:{context}",
                    )
                except Exception as log_exc:
                    log.warning(
                        "post_generation_bot_blocked_log_failed chat=%s err=%s",
                        st.chat_id,
                        str(log_exc),
                    )
            return False

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
                concurrency = max(1, int(getattr(self.settings, "tg_processing_max_concurrency", 8) or 8))
                semaphore = asyncio.Semaphore(concurrency)

                async def _process_one(st: ChatState) -> None:
                    lock_acquired = False
                    try:
                        async with semaphore:
                            lock_acquired = await self.store.acquire_processing_lock(
                                chat_id=st.chat_id,
                                owner_id=self._processing_owner_id,
                                ttl_s=self._processing_lock_ttl_s,
                            )
                            if not lock_acquired:
                                return
                            await self._process_chat_job(
                                st,
                                lock_owner_id=self._processing_owner_id,
                                lock_ttl_s=self._processing_lock_ttl_s,
                            )
                    except Exception as e:
                        log.warning("processing loop chat=%s err=%r", st.chat_id, e)
                    finally:
                        if lock_acquired:
                            try:
                                await self.store.release_processing_lock(
                                    chat_id=st.chat_id,
                                    owner_id=self._processing_owner_id,
                                )
                            except Exception as release_err:
                                log.warning("processing lock release failed chat=%s err=%r", st.chat_id, release_err)

                if states:
                    await asyncio.gather(*[_process_one(st) for st in states])
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
                                await self.credits_db.update_payment_status(order_id, "CONFIRMED", payment_id)
                                await self.credits_db.add_credits(
                                    tg_id,
                                    credits_to_add,
                                    "payment",
                                    f"Пакет «{pkg}» order={order_id}",
                                    actor="tbank_poll",
                                    order_id=order_id,
                                )
                                # Track quota: the FIRST track-eligible payment grants
                                # the tariff base; every later one adds just +1 (a
                                # renewal tops up the limit by a single track). Count
                                # is taken after the status flip, so it includes this one.
                                track_base = self._PKG_TRACKS.get(pkg, 0)
                                if track_base:
                                    prior = await self.credits_db.count_confirmed_track_payments(tg_id)
                                    tracks_to_add = track_base if prior <= 1 else 1
                                    await self.credits_db.add_track_credits(
                                        tg_id,
                                        tracks_to_add,
                                        "payment",
                                        f"Пакет «{pkg}» order={order_id}",
                                    )
                                await self.credits_db.log_event(tg_id, "payment_confirmed", f"{pkg} +{credits_to_add} кредитов")
                                # Save RebillId and create subscription for recurrent payments
                                is_recurrent = pay.get("is_recurrent", False)
                                if is_recurrent and payment_id and self.tbank:
                                    try:
                                        gs = await self.tbank.get_state(payment_id)
                                        rebill_id = str(gs.get("RebillId", "")) if gs else ""
                                        if rebill_id:
                                            await self.credits_db.update_rebill_id(order_id, rebill_id)
                                            await self.credits_db.create_subscription(
                                                tg_id, pkg, rebill_id, pay["amount_rub"],
                                            )
                                            await self.credits_db.log_event(
                                                tg_id, "subscription_created", f"{pkg} rebill=***{rebill_id[-6:]}",
                                            )
                                            log.info("subscription created order=%s rebill=***%s", order_id, rebill_id[-6:])
                                    except Exception as e:
                                        log.warning("subscription create failed order=%s err=%s", order_id, e)
                                username = ""
                                try:
                                    user_data = await self.credits_db.get_user(tg_id)
                                    username = user_data.get("username", "") if user_data else ""
                                except Exception:
                                    pass
                                bal = await self.credits_db.get_balance(tg_id)
                                track_bal = await self.credits_db.get_track_balance(tg_id)
                                try:
                                    await bot.send_message(
                                        tg_id,
                                        f"Оплата прошла успешно! Пакет «{pkg}» активирован.\n\n"
                                        f"Начислено кредитов: {credits_to_add}\n"
                                        f"Баланс: {bal}\n"
                                        f"Доступно уникальных треков: {track_bal}\n\n"
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
                                await self.credits_db.update_payment_status(order_id, status, payment_id)
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

    # ── Subscription charge loop: keys for heartbeat + cross-instance lock ──
    _SUB_LOOP_LOCK_KEY = "tg_bot_public:sub_charge_loop:lock"
    _SUB_LOOP_HEARTBEAT_KEY = "tg_bot_public:sub_charge_loop:last_tick"
    _SUB_LOOP_STATS_KEY = "tg_bot_public:sub_charge_loop:last_stats"

    async def charge_subscription_once(self, sub: dict, *, manual: bool = False) -> tuple[bool, str]:
        """Run a single Charge for one subscription row.

        Used by the daily loop and by the admin manual-trigger endpoint.
        Returns (success, error_message). On failure, the subscription row
        is already advanced (retries+=1 or paused).
        """
        if not self.tbank:
            return False, "tbank not configured"
        tg_id = sub["tg_id"]
        pkg = sub["package"]
        rebill_id = sub["rebill_id"]
        amount_rub = sub["amount_rub"]
        sub_id = sub["id"]
        bot = self._require_bot()

        order_id = f"{tg_id}-{pkg.replace(' ', '_')}-sub-{uuid.uuid4().hex[:8]}"
        last_utm = await self.credits_db.get_last_utm(tg_id)
        await self.credits_db.create_recurrent_payment(
            order_id, tg_id, amount_rub, pkg, utm=last_utm,
        )
        desc = f"Подписка «{pkg}» — ежемесячное списание"
        if manual:
            desc = f"Подписка «{pkg}» — ручное списание (admin)"
        payment_id = await self.tbank.init_for_charge(
            amount_rub=amount_rub, order_id=order_id, description=desc,
        )
        if not payment_id:
            log.warning("sub charge init failed sub=%s tg_id=%s manual=%s", sub_id, tg_id, manual)
            new_status = await self.credits_db.subscription_charge_failed(sub_id)
            if new_status == "paused":
                await self._notify_subscription_paused(bot, tg_id, pkg)
            return False, "init_for_charge failed"

        success, err = await self.tbank.charge(payment_id, rebill_id)
        if success:
            await self.credits_db.update_payment_status(order_id, "confirmed", payment_id)
            credits_to_add = self._PKG_CREDITS.get(pkg, 5)
            await self.credits_db.add_credits(tg_id, credits_to_add, "subscription", f"Подписка «{pkg}»")
            # First track-eligible charge grants the tariff base; renewals +1.
            track_base = self._PKG_TRACKS.get(pkg, 0)
            if track_base:
                prior = await self.credits_db.count_confirmed_track_payments(tg_id)
                tracks_to_add = track_base if prior <= 1 else 1
                await self.credits_db.add_track_credits(tg_id, tracks_to_add, "subscription", f"Подписка «{pkg}»")
            await self.credits_db.subscription_charge_success(sub_id)
            event = "subscription_charged_manual" if manual else "subscription_charged"
            await self.credits_db.log_event(tg_id, event, f"{pkg} +{credits_to_add}")
            bal = await self.credits_db.get_balance(tg_id)
            track_bal = await self.credits_db.get_track_balance(tg_id)
            try:
                await bot.send_message(
                    tg_id,
                    f"Подписка «{pkg}» продлена!\n\n"
                    f"Начислено кредитов: {credits_to_add}\n"
                    f"Баланс: {bal}\n"
                    f"Доступно уникальных треков: {track_bal}\n\n"
                    "Отправь трек, чтобы начать генерацию.\n\n"
                    "/cancelsubscription — отменить подписку",
                    reply_markup=_kb(["Отправить трек"]),
                )
            except Exception as e:
                log.warning("sub charge notify user=%s err=%s", tg_id, e)
            username = ""
            try:
                user_data = await self.credits_db.get_user(tg_id)
                username = user_data.get("username", "") if user_data else ""
            except Exception:
                pass
            uname = f"@{username}" if username else str(tg_id)
            await self._notify_manager_payment(uname, pkg, amount_rub, "Подписка" + (" (ручное)" if manual else ""))
            await self._notify_finance_bot_income(amount_rub, uname, pkg)
            log.info("subscription charged sub=%s tg_id=%s pkg=%s manual=%s", sub_id, tg_id, pkg, manual)
            return True, ""

        await self.credits_db.update_payment_status(order_id, "charge_failed", payment_id)
        new_status = await self.credits_db.subscription_charge_failed(sub_id)
        await self.credits_db.log_event(tg_id, "subscription_charge_failed", err)
        if new_status == "paused":
            await self._notify_subscription_paused(bot, tg_id, pkg)
        else:
            log.info("sub charge failed, will retry sub=%s retries=%s", sub_id, sub["charge_retries"] + 1)
        return False, err

    async def _subscription_charge_loop(self) -> None:
        """Once per day, charge active subscriptions that are due.

        Cross-instance safe: SET NX EX advisory lock prevents two bot replicas
        from racing the same Charge. Writes a heartbeat marker to Redis at the
        start of every successful iteration so the admin dashboard can show
        liveness.
        """
        instance_id = uuid.uuid4().hex[:12]
        loop_ttl_s = 7200  # 2h — far longer than any iteration but expires if process dies
        heartbeat_ttl_s = 172800  # 2 days — admin reads it for liveness

        while True:
            acquired = False
            try:
                redis = self.store.redis
                acquired = bool(
                    await redis.set(self._SUB_LOOP_LOCK_KEY, instance_id, nx=True, ex=loop_ttl_s)
                )
                if not acquired:
                    log.info("sub_charge_loop: lock held by another instance, skipping tick")
                else:
                    await redis.set(
                        self._SUB_LOOP_HEARTBEAT_KEY,
                        datetime.now(timezone.utc).isoformat(),
                        ex=heartbeat_ttl_s,
                    )
                    succeeded = 0
                    failed = 0
                    if self.tbank:
                        due = await self.credits_db.get_subscriptions_due()
                        for sub in due:
                            try:
                                ok, _ = await self.charge_subscription_once(sub, manual=False)
                                if ok:
                                    succeeded += 1
                                else:
                                    failed += 1
                            except Exception as e:
                                failed += 1
                                log.warning("sub charge error sub=%s err=%r", sub.get("id"), e)
                    await redis.set(
                        self._SUB_LOOP_STATS_KEY,
                        json.dumps({
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "succeeded": succeeded,
                            "failed": failed,
                            "instance": instance_id,
                        }),
                        ex=heartbeat_ttl_s,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("subscription charge loop error=%r", e)
            finally:
                if acquired:
                    try:
                        # Best-effort lock release; expiry covers crashes.
                        cur = await self.store.redis.get(self._SUB_LOOP_LOCK_KEY)
                        if cur == instance_id:
                            await self.store.redis.delete(self._SUB_LOOP_LOCK_KEY)
                    except Exception:
                        pass
            await asyncio.sleep(86400)  # once per day

    async def _notify_subscription_paused(self, bot: Bot, tg_id: int, pkg: str) -> None:
        """Notify user that their subscription is paused due to failed charges."""
        try:
            await bot.send_message(
                tg_id,
                f"Не удалось списать оплату за подписку «{pkg}».\n\n"
                "Подписка приостановлена. Проверь карту и свяжись с менеджером "
                "для возобновления: @impulsemanage\n\n"
                "/packages — посмотреть тарифы",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as e:
            log.warning("sub paused notify failed tg_id=%s err=%s", tg_id, e)

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
            await self._runtime_update_run(
                st=st,
                current_stage=stage or "failed",
                last_error_code="job_failed",
                last_error_text=error_text,
            )
            return

        source = _resolve_job_video_source(job, self.settings)
        if not source:
            claimed_missing_source, missing_source_key = await self._runtime_claim_outbox(
                st=st,
                kind="result_source_missing_notice",
                job_id=job_id,
                payload={
                    "job_id": job_id,
                    "ver_label": ver_label,
                    "stage": stage or "render",
                },
            )
            if claimed_missing_source:
                try:
                    await self._notify_ops_alert(
                        title="Render result without output source",
                        chat_id=st.chat_id,
                        username=st.chat_username,
                        job_id=job_id,
                        stage=stage or "render",
                        error_text=json.dumps(job, ensure_ascii=False)[:1200],
                    )
                    await bot.send_message(
                        st.chat_id,
                        f"{ver_label}: {_RESULT_SOURCE_MISSING_USER_TEXT}",
                    )
                    await self._runtime_mark_outbox_sent(
                        dedupe_key=missing_source_key,
                        payload_patch={"sent_mode": "user_notice"},
                    )
                except Exception as exc:
                    await self._runtime_mark_outbox_failed(
                        dedupe_key=missing_source_key,
                        error_text=repr(exc),
                        keep_leased=True,
                    )
            return

        st.last_result_url = source

        claimed_video_delivery, video_delivery_key = await self._runtime_claim_outbox(
            st=st,
            kind="telegram_video_delivery",
            job_id=job_id,
            payload={
                "job_id": job_id,
                "ver_label": ver_label,
                "source": source,
                "stage": stage or "render_delivery",
            },
        )
        if claimed_video_delivery:
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
                await self._runtime_mark_outbox_sent(
                    dedupe_key=video_delivery_key,
                    payload_patch={"sent_mode": "telegram_video"},
                )
            except Exception as e:
                send_file_error = str(e)
                log.warning("send file failed chat=%s job=%s err=%s", st.chat_id, job_id, send_file_error)

            if not file_sent:
                fallback_link = await self._build_fallback_link(source)
                try:
                    await self._notify_ops_alert(
                        title="Telegram video delivery failed",
                        chat_id=st.chat_id,
                        username=st.chat_username,
                        job_id=job_id,
                        stage=stage or "render_delivery",
                        error_text=send_file_error,
                        extra_lines=[f"has_fallback_link: {bool(fallback_link)}"],
                    )
                    msg = f"{ver_label}: {_VIDEO_DELIVERY_FAILED_USER_TEXT}"
                    if fallback_link:
                        msg += f"\nСсылка: {fallback_link}"
                    await bot.send_message(st.chat_id, msg)
                    await self._runtime_mark_outbox_sent(
                        dedupe_key=video_delivery_key,
                        payload_patch={
                            "sent_mode": "fallback_link",
                            "fallback_link": fallback_link,
                        },
                    )
                except Exception as exc:
                    await self._runtime_mark_outbox_failed(
                        dedupe_key=video_delivery_key,
                        error_text=repr(exc),
                        keep_leased=True,
                    )

            try:
                for p in {video_path, send_video_path}:
                    if p.exists():
                        p.unlink()
            except Exception:
                pass

        if self.settings.tg_send_project_archive and self._allow_archive_for_state(st):
            archive_source = _resolve_job_project_archive_source(job)
            claimed_archive_notice, archive_notice_key = await self._runtime_claim_outbox(
                st=st,
                kind="telegram_project_archive_notice",
                job_id=job_id,
                payload={"job_id": job_id, "ver_label": ver_label, "archive_source": archive_source},
            )
            if claimed_archive_notice:
                try:
                    if archive_source:
                        archive_link = await self._build_fallback_link(archive_source)
                        if not archive_link:
                            archive_link = archive_source
                        await bot.send_message(
                            st.chat_id,
                            f"{ver_label}: проект (AEP + ресурсы): {archive_link}",
                        )
                        await self._runtime_mark_outbox_sent(
                            dedupe_key=archive_notice_key,
                            payload_patch={"archive_link": archive_link},
                        )
                    else:
                        await bot.send_message(
                            st.chat_id,
                            f"{ver_label}: видео готово, но ссылка на архив проекта в ответе рендера не найдена.",
                        )
                        await self._runtime_mark_outbox_sent(
                            dedupe_key=archive_notice_key,
                            payload_patch={"archive_link": ""},
                        )
                except Exception as exc:
                    await self._runtime_mark_outbox_failed(
                        dedupe_key=archive_notice_key,
                        error_text=repr(exc),
                        keep_leased=True,
                    )

        if self._allow_archive_for_state(st):
            try:
                dbg_text = _build_subtitles_debug_text_for_job(job_id=job_id, ver_label=ver_label)
                if dbg_text:
                    await self._send_long_html_message(bot=bot, chat_id=st.chat_id, text=dbg_text)
            except Exception as e:
                log.warning("subtitles_debug_send_failed chat=%s job=%s err=%s", st.chat_id, job_id, str(e))
        await self._runtime_update_run(st=st, current_stage=stage or "render_delivery")

    async def _refresh_processing_lock_or_raise(self, *, chat_id: int, owner_id: str, ttl_s: int) -> None:
        owner = str(owner_id or "").strip()
        if not owner:
            return
        ok = await self.store.refresh_processing_lock(
            chat_id=int(chat_id),
            owner_id=owner,
            ttl_s=max(5, int(ttl_s)),
        )
        if not ok:
            raise RuntimeError(f"processing_lock_lost chat={int(chat_id)} owner={owner!r}")

    @staticmethod
    def _is_processing_lock_lost_error(exc: BaseException) -> bool:
        return "processing_lock_lost" in str(exc)

    def _processing_lock_heartbeat_interval_s(self, ttl_s: int) -> float:
        ttl = max(5, int(ttl_s or 0))
        return max(5.0, min(60.0, float(ttl) / 3.0))

    @asynccontextmanager
    async def _processing_lock_heartbeat(
        self,
        *,
        chat_id: int,
        owner_id: str,
        ttl_s: int,
    ):
        owner = str(owner_id or "").strip()
        if not owner:
            yield
            return

        ttl = max(5, int(ttl_s or 0))
        interval_s = self._processing_lock_heartbeat_interval_s(ttl)
        stop = asyncio.Event()

        async def _heartbeat() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval_s)
                    return
                except asyncio.TimeoutError:
                    pass
                ok = await self.store.refresh_processing_lock(
                    chat_id=int(chat_id),
                    owner_id=owner,
                    ttl_s=ttl,
                )
                if not ok:
                    log.warning(
                        "processing_lock_heartbeat_lost chat=%s owner=%r",
                        int(chat_id),
                        owner,
                    )
                    return

        task = asyncio.create_task(_heartbeat())
        try:
            yield
        finally:
            stop.set()
            try:
                await task
            except Exception as exc:
                log.warning(
                    "processing_lock_heartbeat_failed chat=%s owner=%r err=%r",
                    int(chat_id),
                    owner,
                    exc,
                )

    async def _process_chat_job(
        self,
        st: ChatState,
        *,
        lock_owner_id: str = "",
        lock_ttl_s: int = 0,
    ) -> None:
        await self._refresh_processing_lock_or_raise(
            chat_id=st.chat_id,
            owner_id=lock_owner_id,
            ttl_s=lock_ttl_s,
        )
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
        await self._refresh_processing_lock_or_raise(
            chat_id=st.chat_id,
            owner_id=lock_owner_id,
            ttl_s=lock_ttl_s,
        )
        jobs_by_id = await self.orchestrator.get_jobs(job_ids)
        for jid in job_ids:
            job = jobs_by_id[jid]
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
            if status not in {"SUCCEEDED", "FAILED"} and jid in completed:
                completed.discard(jid)
            await self._runtime_sync_version_from_job(st=st, job_id=jid, job=job)
            if status in {"SUCCEEDED", "FAILED"} and jid not in completed:
                new_finals.append((jid, job))

        if st.poll_attempts == 1 or (st.poll_attempts % 3) == 0:
            st.last_backpressure_notice = await self._current_backpressure_notice()

        status_text = self._jobs_progress_message(
            rows=rows,
            poll_attempts=st.poll_attempts,
            total_versions=total_versions,
            backpressure_notice=st.last_backpressure_notice,
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
            await self._refresh_processing_lock_or_raise(
                chat_id=st.chat_id,
                owner_id=lock_owner_id,
                ttl_s=lock_ttl_s,
            )
            async with self._processing_lock_heartbeat(
                chat_id=st.chat_id,
                owner_id=lock_owner_id,
                ttl_s=lock_ttl_s,
            ):
                await self._finalize_one_job(bot=bot, st=st, job_id=jid, job=job)
            await self._refresh_processing_lock_or_raise(
                chat_id=st.chat_id,
                owner_id=lock_owner_id,
                ttl_s=lock_ttl_s,
            )
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
                    await self._runtime_record_version_succeeded(
                        st=st,
                        job_id=jid,
                        used_file_names=list(used_now),
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

                # Always advance rotation cursor on success — diag signals
                # are folded into the reason code for log observability only.
                # Phase 2b: the vibe flow makes rotation explicit + deterministic
                # (re-running the same track → same shortlist), so the legacy
                # auto-cursor is disabled there. It already no-ops on empty
                # artist_id (vibe sets it ""), but gate it explicitly too.
                if artist_id_for_rotation and not FOOTAGE_VIBE_FLOW_ENABLED:
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
        failed_rows = [r for r in rows if str(r.get("status") or "").upper() == "FAILED"]
        if failed_rows:
            failed = failed_rows[0]
            failed_job_id = str(failed.get("job_id") or "")
            failed_stage = str(failed.get("stage") or "")
            failed_error = str(failed.get("error") or "")
            succeeded_versions = sum(1 for r in rows if str(r.get("status") or "").upper() == "SUCCEEDED")
            refund_versions = max(0, int(total_versions) - int(succeeded_versions))
            if refund_versions > 0:
                claimed_refund, refund_key = await self._runtime_claim_outbox(
                    st=st,
                    kind="generation_failed_refund",
                    suffix=failed_job_id or "batch",
                    payload={
                        "failed_job_id": failed_job_id,
                        "refund_versions": int(refund_versions),
                    },
                )
                if claimed_refund:
                    try:
                        await self.credits_db.add_credits(
                            st.chat_id,
                            int(refund_versions),
                            "generation_failed_refund",
                            admin_note=f"batch={st.batch_id or '-'} job={failed_job_id or '-'}",
                            actor="outbox_inline",
                            order_id=refund_key,
                        )
                        await self._runtime_mark_outbox_sent(
                            dedupe_key=refund_key,
                            payload_patch={"refund_versions": int(refund_versions)},
                        )
                    except Exception as e:
                        log.warning("generation_failed_refund_add_credits_failed chat=%s err=%s", st.chat_id, str(e))
                        await self._runtime_mark_outbox_failed(
                            dedupe_key=refund_key,
                            error_text=repr(e),
                            keep_leased=True,
                        )
            await self.credits_db.log_event(
                st.chat_id,
                "generation_failed",
                f"job={failed_job_id or '-'} stage={failed_stage or '-'}",
            )
            claimed_manager_alert, manager_alert_key = await self._runtime_claim_outbox(
                st=st,
                kind="generation_failed_manager_alert",
                suffix=failed_job_id or "batch",
                payload={
                    "failed_job_id": failed_job_id,
                    "failed_stage": failed_stage,
                    "error_text": failed_error,
                    "succeeded_versions": int(succeeded_versions),
                    "total_versions": int(total_versions),
                    "refunded_versions": int(refund_versions),
                },
            )
            if claimed_manager_alert:
                try:
                    await self._notify_manager_generation_failure(
                        st=st,
                        job_id=failed_job_id,
                        stage=failed_stage,
                        error_text=failed_error,
                        succeeded_versions=int(succeeded_versions),
                        total_versions=int(total_versions),
                        refunded_versions=int(refund_versions),
                        strict=True,
                    )
                    await self._runtime_mark_outbox_sent(dedupe_key=manager_alert_key)
                except Exception as exc:
                    await self._runtime_mark_outbox_failed(
                        dedupe_key=manager_alert_key,
                        error_text=repr(exc),
                        keep_leased=True,
                    )
            claimed_user_notice, user_notice_key = await self._runtime_claim_outbox(
                st=st,
                kind="generation_failed_user_notice",
                suffix=failed_job_id or "batch",
                payload={"failed_job_id": failed_job_id},
            )
            if claimed_user_notice:
                try:
                    await bot.send_message(
                        st.chat_id,
                        _GENERATION_FAILED_USER_TEXT,
                        reply_markup=self._wait_audio_reuse_kb(),
                    )
                    await self._runtime_mark_outbox_sent(dedupe_key=user_notice_key)
                except Exception as exc:
                    await self._runtime_mark_outbox_failed(
                        dedupe_key=user_notice_key,
                        error_text=repr(exc),
                        keep_leased=True,
                    )
            await self._runtime_update_run(
                st=st,
                status="failed",
                current_stage=failed_stage or "failed",
                last_error_code="batch_failed",
                last_error_text=failed_error,
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
                    await self._refresh_processing_lock_or_raise(
                        chat_id=st.chat_id,
                        owner_id=lock_owner_id,
                        ttl_s=lock_ttl_s,
                    )
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
                    if self._is_processing_lock_lost_error(e):
                        log.warning(
                            "enqueue_next_version_processing_lock_lost_handoff chat=%s owner=%r err=%s",
                            st.chat_id,
                            str(lock_owner_id or ""),
                            str(e),
                        )
                        return
                    err_text = str(e)
                    succeeded_versions = sum(1 for r in rows if str(r.get("status") or "").upper() == "SUCCEEDED")
                    refund_versions = max(0, int(total_versions) - int(succeeded_versions))
                    if refund_versions > 0:
                        claimed_refund, refund_key = await self._runtime_claim_outbox(
                            st=st,
                            kind="enqueue_next_failed_refund",
                            suffix="enqueue_next_version",
                            payload={
                                "failed_job_id": "enqueue_next_version",
                                "refund_versions": int(refund_versions),
                            },
                        )
                        if claimed_refund:
                            try:
                                await self.credits_db.add_credits(
                                    st.chat_id,
                                    int(refund_versions),
                                    "generation_failed_refund",
                                    admin_note=f"batch={st.batch_id or '-'} job=enqueue_next_version_failed",
                                    actor="outbox_inline",
                                    order_id=refund_key,
                                )
                                await self._runtime_mark_outbox_sent(dedupe_key=refund_key)
                            except Exception as add_e:
                                log.warning("enqueue_next_refund_failed chat=%s err=%s", st.chat_id, str(add_e))
                                await self._runtime_mark_outbox_failed(
                                    dedupe_key=refund_key,
                                    error_text=repr(add_e),
                                    keep_leased=True,
                                )
                    await self.credits_db.log_event(
                        st.chat_id,
                        "generation_failed",
                        f"job=enqueue_next stage=enqueue_next_version error={_compact_text(err_text, limit=140)}",
                    )
                    claimed_manager_alert, manager_alert_key = await self._runtime_claim_outbox(
                        st=st,
                        kind="enqueue_next_failed_manager_alert",
                        suffix="enqueue_next_version",
                        payload={
                            "failed_job_id": "enqueue_next_version",
                            "failed_stage": "enqueue_next_version",
                            "error_text": err_text,
                            "succeeded_versions": int(succeeded_versions),
                            "total_versions": int(total_versions),
                            "refunded_versions": int(refund_versions),
                        },
                    )
                    if claimed_manager_alert:
                        try:
                            await self._notify_manager_generation_failure(
                                st=st,
                                job_id="enqueue_next_version",
                                stage="enqueue_next_version",
                                error_text=err_text,
                                succeeded_versions=int(succeeded_versions),
                                total_versions=int(total_versions),
                                refunded_versions=int(refund_versions),
                                strict=True,
                            )
                            await self._runtime_mark_outbox_sent(dedupe_key=manager_alert_key)
                        except Exception as exc:
                            await self._runtime_mark_outbox_failed(
                                dedupe_key=manager_alert_key,
                                error_text=repr(exc),
                                keep_leased=True,
                            )
                    claimed_user_notice, user_notice_key = await self._runtime_claim_outbox(
                        st=st,
                        kind="enqueue_next_failed_user_notice",
                        suffix="enqueue_next_version",
                        payload={"error_text": err_text[:500]},
                    )
                    if claimed_user_notice:
                        try:
                            await bot.send_message(
                                st.chat_id,
                                _GENERATION_FAILED_USER_TEXT,
                                reply_markup=self._wait_audio_reuse_kb(),
                            )
                            await self._runtime_mark_outbox_sent(dedupe_key=user_notice_key)
                        except Exception as exc:
                            await self._runtime_mark_outbox_failed(
                                dedupe_key=user_notice_key,
                                error_text=repr(exc),
                                keep_leased=True,
                            )
                    await self._runtime_update_run(
                        st=st,
                        status="failed",
                        current_stage="enqueue_next_version",
                        last_error_code="enqueue_next_failed",
                        last_error_text=err_text,
                    )
                    self._reset_processing_state(st, next_stage=STAGE_WAIT_AUDIO)
                    await self.store.set(st)
                    return

        await self._runtime_update_run(
            st=st,
            status="succeeded",
            current_stage="completed",
            next_version_to_enqueue=int(total_versions) + 1,
            last_error_code="",
            last_error_text="",
        )
        self._reset_processing_state(st)  # sets stage = RATE_VIDEO

        # Credits already deducted at launch time
        bal = await self.credits_db.get_balance(st.chat_id)
        track_bal = await self.credits_db.get_track_balance(st.chat_id)
        log.info("generation_complete chat=%s remaining=%s tracks_remaining=%s", st.chat_id, bal, track_bal)

        paid = await self.credits_db.has_paid(st.chat_id)

        # Hidden test override: force the free funnel for whitelisted chat_ids so
        # the rating → pitch → referral flow can be reviewed from a paid account.
        if paid and int(st.chat_id) in self.settings.tg_force_free_funnel_chat_ids:
            log.info("force_free_funnel_override chat=%s", st.chat_id)
            paid = False

        # Paid users: no rating/funnel, just loop back to generation
        if paid:
            if bal > 0:
                st.stage = STAGE_WAIT_AUDIO
                await self.store.set(st)
                await self.credits_db.log_event(st.chat_id, "generation_done")
                tracks_line = f"Доступно уникальных треков: {track_bal}\n" if track_bal > 0 else ""
                await self._send_post_generation_message_best_effort(
                    bot=bot,
                    st=st,
                    text=(
                        f"Готово — лови контент! Давай сделаем ещё:\n\n"
                        f"Остаток генераций: {bal}\n"
                        f"{tracks_line}"
                        f"/packages — посмотреть тарифы"
                    ),
                    reply_markup=(
                        _kb([BTN_GENERATE_MORE], [BTN_REUSE_INPUT])
                        if self._can_reuse_input(st)
                        else _kb([BTN_GENERATE_MORE])
                    ),
                    context="paid_more",
                )
            else:
                st.stage = STAGE_PACKAGES_OFFER
                await self.store.set(st)
                await self.credits_db.log_event(st.chat_id, "generation_done")
                await self._send_post_generation_message_best_effort(
                    bot=bot,
                    st=st,
                    text=(
                        "Готово — лови контент!\n\n"
                        "Твои кредиты закончились. Хочешь посмотреть тарифы?\n\n"
                        "/packages — посмотреть тарифы"
                    ),
                    reply_markup=_kb([BTN_ALL_PACKAGES]),
                    context="paid_no_credits",
                )
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

        await self.store.set(st)
        await self.credits_db.log_event(st.chat_id, "generation_done")
        await self._send_post_generation_message_best_effort(
            bot=bot,
            st=st,
            text=rating_text,
            reply_markup=_kb(BTN_RATE_BUTTONS),
            context="rating_prompt",
        )

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

    @staticmethod
    def _normalize_webhook_path(path: str) -> str:
        p = str(path or "").strip()
        if not p:
            raise RuntimeError("TG_WEBHOOK_PATH is empty")
        if not p.startswith("/"):
            raise RuntimeError(f"TG_WEBHOOK_PATH must start with '/', got {p!r}")
        return p

    async def _handle_telegram_webhook(self, request: web.Request, *, bot: Bot) -> web.Response:
        t0 = time.monotonic()
        expected_secret = str(self.settings.tg_webhook_secret or "").strip()
        if expected_secret:
            got_secret = str(request.headers.get(_TG_WEBHOOK_SECRET_HEADER) or "").strip()
            if got_secret != expected_secret:
                log.warning("telegram_webhook_rejected reason=bad_secret")
                raise web.HTTPForbidden(text="forbidden")

        payload = await request.json()
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="invalid payload")

        update_id = int(payload.get("update_id") or 0)
        log.info("tg_webhook_in update_id=%s keys=%s", update_id, sorted(str(k) for k in payload.keys()))
        if update_id > 0:
            is_new = await self.store.mark_webhook_update_seen(
                update_id=update_id,
                ttl_s=int(self.settings.tg_webhook_dedup_ttl_s),
            )
            if not is_new:
                log.info(
                    "tg_webhook_done update_id=%s dedup=1 dur_ms=%d",
                    update_id,
                    int((time.monotonic() - t0) * 1000.0),
                )
                return web.json_response({"ok": True, "dedup": True})

        try:
            update = Update.model_validate(payload, context={"bot": bot})
        except Exception as exc:
            log.warning(
                "tg_webhook_failed update_id=%s dur_ms=%d err=%r",
                update_id,
                int((time.monotonic() - t0) * 1000.0),
                exc,
            )
            raise web.HTTPBadRequest(text="invalid update") from exc

        task = asyncio.create_task(
            self._dispatch_telegram_webhook_update(bot=bot, update=update, update_id=update_id, started_at=t0),
            name=f"tg_webhook_dispatch_{update_id or 'unknown'}",
        )

        def _log_task_error(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                log.error("tg_webhook_task_crashed update_id=%s err=%r", update_id, exc)

        task.add_done_callback(_log_task_error)
        log.info(
            "tg_webhook_accepted update_id=%s dedup=0 dur_ms=%d",
            update_id,
            int((time.monotonic() - t0) * 1000.0),
        )
        return web.json_response({"ok": True})

    async def _dispatch_telegram_webhook_update(
        self,
        *,
        bot: Bot,
        update: Update,
        update_id: int,
        started_at: float,
    ) -> None:
        try:
            await self.dp.feed_update(bot, update)
        except Exception as exc:
            log.warning(
                "tg_webhook_dispatch_failed update_id=%s dur_ms=%d err=%r",
                update_id,
                int((time.monotonic() - started_at) * 1000.0),
                exc,
            )
            return
        log.info(
            "tg_webhook_done update_id=%s dedup=0 dur_ms=%d",
            update_id,
            int((time.monotonic() - started_at) * 1000.0),
        )

    async def _run_webhook(self, *, bot: Bot) -> None:
        webhook_path = self._normalize_webhook_path(self.settings.tg_webhook_path)
        webhook_url_base = str(self.settings.tg_webhook_url or "").strip().rstrip("/")
        if not webhook_url_base:
            raise RuntimeError("TG_WEBHOOK_URL is required when TG_DELIVERY_MODE=webhook")
        webhook_url = f"{webhook_url_base}{webhook_path}"

        await self.dp.emit_startup(bot=bot)
        runner: web.AppRunner | None = None
        try:
            async def _health_handler(_request: web.Request) -> web.Response:
                return web.json_response({"ok": True, "mode": "webhook"})

            async def _telegram_webhook_handler(request: web.Request) -> web.Response:
                return await self._handle_telegram_webhook(request, bot=bot)

            app = web.Application()
            app.router.add_get("/health", _health_handler)
            app.router.add_post(webhook_path, _telegram_webhook_handler)

            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(
                runner,
                host=str(self.settings.tg_webhook_bind_host or "0.0.0.0"),
                port=int(self.settings.tg_webhook_port),
            )
            await site.start()

            ok = await bot.set_webhook(
                url=webhook_url,
                secret_token=str(self.settings.tg_webhook_secret or "").strip() or None,
                ip_address=str(self.settings.tg_webhook_ip_address or "").strip() or None,
                drop_pending_updates=False,
            )
            if not ok:
                raise RuntimeError("Telegram set_webhook returned false")

            log.info(
                "startup complete: webhook loop started url=%s path=%s bind=%s:%s",
                webhook_url,
                webhook_path,
                str(self.settings.tg_webhook_bind_host or "0.0.0.0"),
                int(self.settings.tg_webhook_port),
            )
            await asyncio.Future()
        finally:
            if bool(self.settings.tg_webhook_delete_on_shutdown):
                try:
                    await bot.delete_webhook(drop_pending_updates=False)
                except Exception as e:
                    log.warning("telegram_delete_webhook_failed err=%s", e)
            if runner is not None:
                try:
                    await runner.cleanup()
                except Exception as e:
                    log.warning("telegram_webhook_runner_cleanup_failed err=%s", e)
            await self.dp.emit_shutdown(bot=bot)

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
        delivery_mode = str(self.settings.tg_delivery_mode or "").strip().lower()
        if delivery_mode == "polling":
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                log.info("telegram webhook deleted before polling startup")
            except Exception as e:
                log.warning("telegram_delete_webhook_before_polling_failed err=%s", e)
            await self.dp.start_polling(bot)
            return
        if delivery_mode == "webhook":
            await self._run_webhook(bot=bot)
            return
        raise RuntimeError(f"Unsupported TG_DELIVERY_MODE={delivery_mode!r}")


def main() -> None:
    app = BlastBotApp(SETTINGS)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
