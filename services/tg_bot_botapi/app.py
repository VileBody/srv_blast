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
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
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
    SEASON_STAGES,
    STAGE_IDLE,
    STAGE_LOCKED,
    STAGE_PROCESSING,
    STAGE_SEASON_CONSENT,
    STAGE_SEASON_INTRO_1,
    STAGE_SEASON_INTRO_2,
    STAGE_SEASON_MENU,
    STAGE_WAIT_AUDIO,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_BG_COLOR,
    STAGE_WAIT_BG_MODE,
    STAGE_WAIT_FOOTAGE_ARTIST,
    STAGE_WAIT_FOOTAGE_GENRE,
    STAGE_WAIT_FRAGMENT_CHOICE,
    STAGE_WAIT_HOOK_CHOICE,
    STAGE_WAIT_HOOK_DROP,
    STAGE_WAIT_HOOK_DROP_MANUAL,
    STAGE_WAIT_HOOK_TYPE,
    STAGE_WAIT_HOOK_DEVICE,
    STAGE_WAIT_EFFECT_HOOK,
    STAGE_WAIT_EFFECT_TRANSITION,
    STAGE_WAIT_EFFECT_EXTRA,
    STAGE_WAIT_EFFECT_EXTEND,
    STAGE_WAIT_F2_SHAPE,
    STAGE_WAIT_F1_SOUND,
    STAGE_WAIT_F1_TEXT,
    STAGE_WAIT_BATTERY_SOUND,
    STAGE_WAIT_TIMING_CHOICE,
    STAGE_WAIT_TIMING_INPUT,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
    STAGE_WAIT_NEXT,
    STAGE_WAIT_SUBTITLES_MODE,
    STAGE_WAIT_SUBTITLE_COLOR,
    STAGE_WAIT_ACCENT_COLOR,
    STAGE_WAIT_VERSIONS,
    STAGE_WAITING_REFERRAL,
)
from .user_store import UserStore
from .season import (
    INTRO_1, INTRO_2, CONSENT, WELCOME,
    MENU_HEADER,
    PhaseStore, SeasonReferralStore,
    build_referral_link,
    consent_kb, intro_next_kb, menu_kb, back_to_menu_kb,
    notifications_kb, share_kb, waitlist_kb,
    determine_flow, parse_start_param,
    render_about_season, render_examples_screen, render_generation_screen,
    render_history_screen, render_invite_screen, render_pricing_screen,
)
from .season.keyboards import (
    CB_CONSENT_ALL, CB_CONSENT_FINALS, CB_INTRO_NEXT,
    CB_MENU_ABOUT, CB_MENU_BACK, CB_MENU_EXAMPLES, CB_MENU_GENERATION,
    CB_MENU_HISTORY, CB_MENU_INVITE, CB_MENU_PRICING,
    CB_NOTIF_ALL, CB_NOTIF_FINALS, CB_NOTIF_OFF,
    CB_WAITLIST_JOIN, CB_WAITLIST_SKIP,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_bot: %(message)s",
)
log = logging.getLogger("tg_bot")


def _season_flow_enabled() -> bool:
    """Kill-switch for the season flow in botapi.

    Default OFF: /start lands in the existing product flow (generation works
    end-to-end). Set SEASON_FLOW_ENABLED=1 in env to opt back into the
    onboarding + info menu. Mirrors the public-bot gate so both bots share
    one env var.
    """
    return os.environ.get("SEASON_FLOW_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on", "enabled",
    }


# /bigtest is available only on the team bot. Set False in tg_bot_public/app.py
# so parity code is present in both bots but the command is blocked in public.
BIGTEST_ENABLED: bool = True
# Hook battery (one button → N videos, one per category, random sub-picks).
# Team bot only; mirrored as False in tg_bot_public for parity.
BATTERY_ENABLED: bool = True

# F5 «Мысль»: lead (seconds) the clip is reframed BACK from the drop, so the TTS
# voice (~3s) plays in the run-up and lands INTO the drop (post-drop focus line
# right after). Symmetric to F4's per-device cover lead. clip_start := drop −
# F5_LEAD_SEC; the drop must therefore sit at least this far into the track.
# Mirrored in tg_bot_public for parity.
F5_LEAD_SEC: float = 4.0

# Minimum clip length (seconds) a reframe (F4/F5) must leave AFTER pushing
# clip_start to drop−lead. Picking a drop near the clip end shrinks the window
# below STAGE2_FAST_START_SECONDS (orchestrator, =6) → the build's
# SwitchTimingPayload validator rejects it. Keep ≥ that fast-start. Used to bound
# the battery drop auto-walk so it never reframes against a too-late drop.
# Mirrored in tg_bot_public for parity.
MIN_REFRAME_CLIP_SEC: float = 7.0


BTN_SEND_TRACK = "Отправить трек"
BTN_SEND_LYRICS = "Отправить текст"
BTN_SKIP_LYRICS = "Не присылать текст"
BTN_SEND_FRAGMENT = "Отправить интересующий фрагмент"
BTN_SKIP_FRAGMENT = "На усмотрение ИИ"
BTN_SET_TIMING = "Указать тайминг"
BTN_SKIP_TIMING = "Весь трек / на усмотрение ИИ"
BTN_BACK = "Назад"
# F1 «Звук» — skip the optional subtitle step.
BTN_F1_NO_SUBS = "Без субтитров"

# Customization color palette (label → hex). "По умолчанию" → keep script default.
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


def _parse_color_choice(text: str) -> Optional[str]:
    """Return '' for «По умолчанию», a hex for a palette label, else None."""
    raw = str(text or "").strip()
    if raw == BTN_COLOR_DEFAULT:
        return ""
    return _COLOR_PALETTE.get(raw)
BTN_BG_FOOTAGE = "Футажи"
BTN_BG_SOLID = "Цветной фон"
BTN_BG_WHITE = "Белый"
BTN_BG_BLACK = "Чёрный"
BTN_BG_GREEN = "Зелёный (хромакей)"
BTN_LAUNCH = "Запустить"
BTN_NEXT = "Сделать следующий"
BTN_REUSE_INPUT = "Сделать под тот же трек"
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
BTN_SUB_MODE_TRENDY = "Trendy (1 слово)"
BTN_SUB_MODE_BRAT = "Brat (blocks)"
# Hook feature (Phase A-UX).
BTN_HOOK_YES = "Сделать хук"
BTN_HOOK_NO = "Без хука"
BTN_HOOK_BATTERY = "🎲 Батарея (5 хуков)"
BTN_BATTERY_NO_SOUND = "Без звука (4 ролика)"
BTN_HOOK_DROP_NONE = "В отрывке нет дропа"
BTN_HOOK_DROP_MANUAL = "Ввести вручную"
BTN_HOOK_TYPE_STANDARD = "Стандартный"
# Hook category picker — 5 buttons. Only "Мысль" (F5 Cognition) is implemented;
# the other four are not-yet-available stubs.
BTN_HOOK_CAT_SOUND = "Звук"
BTN_HOOK_CAT_OBJECT = "Объект"
BTN_HOOK_CAT_EFFECT = "Эффект"
BTN_HOOK_CAT_MOTION = "Движение"
BTN_HOOK_CAT_THOUGHT = "Мысль"
HOOK_CATEGORY_BUTTONS = [
    BTN_HOOK_CAT_SOUND,
    BTN_HOOK_CAT_OBJECT,
    BTN_HOOK_CAT_EFFECT,
    BTN_HOOK_CAT_MOTION,
    BTN_HOOK_CAT_THOUGHT,
]
# category button -> internal category key. Only "thought" is wired downstream.
_HOOK_CATEGORY_BY_BUTTON = {
    BTN_HOOK_CAT_SOUND: "sound",
    BTN_HOOK_CAT_OBJECT: "object",
    BTN_HOOK_CAT_EFFECT: "effect",
    BTN_HOOK_CAT_MOTION: "motion",
    BTN_HOOK_CAT_THOUGHT: "thought",
}
# All 5 hook categories are now wired: "Мысль"=F5, "Движение"=F4, "Эффект"=F3,
# "Объект"=F2, "Звук"=F1. Kept as an (empty) gate for future stubs.
_HOOK_CATEGORY_NOT_READY: set[str] = set()
# F4 («Движение») device picker. Button text -> f4 device id. Only "swipe" is
# wired so far (mlcore/hooks/f4_motion). LEAD_BY_DEVICE is imported lazily where
# the clip-window reframe happens (clip_start := drop - LEAD[device]).
BTN_HOOK_DEV_SWIPE = "Свайп"
BTN_HOOK_DEV_TAP = "Тап"
BTN_HOOK_DEV_PINCH = "Зум"
BTN_HOOK_DEV_HOLD = "Задержи палец"
BTN_HOOK_DEV_HEAD = "Качай головой"
HOOK_MOTION_DEVICE_BUTTONS = [
    BTN_HOOK_DEV_SWIPE,
    BTN_HOOK_DEV_TAP,
    BTN_HOOK_DEV_PINCH,
    BTN_HOOK_DEV_HOLD,
    BTN_HOOK_DEV_HEAD,
]
_HOOK_MOTION_DEVICE_BY_BUTTON = {
    BTN_HOOK_DEV_SWIPE: "swipe",
    BTN_HOOK_DEV_TAP: "tap",
    BTN_HOOK_DEV_PINCH: "pinch",
    BTN_HOOK_DEV_HOLD: "holdfinger",
    BTN_HOOK_DEV_HEAD: "head",
}
# F5 («Мысль») device picker — labels mirror DeviceSpec.title_ru in
# mlcore/hooks/f5_cognition/devices.py. Button text -> F5Device value.
BTN_HOOK_DEV_PUNCHLINE = "Панчлайн"
BTN_HOOK_DEV_MISSING_WORD = "Пропущенное слово"
BTN_HOOK_DEV_LYRIC_ECHO = "Эхо"
BTN_HOOK_DEV_QUESTION = "Вопрос к треку"
BTN_HOOK_DEV_INVERSE = "Инверсия"
HOOK_DEVICE_BUTTONS = [
    BTN_HOOK_DEV_PUNCHLINE,
    BTN_HOOK_DEV_MISSING_WORD,
    BTN_HOOK_DEV_LYRIC_ECHO,
    BTN_HOOK_DEV_QUESTION,
    BTN_HOOK_DEV_INVERSE,
]
_HOOK_DEVICE_BY_BUTTON = {
    BTN_HOOK_DEV_PUNCHLINE: "punchline",
    BTN_HOOK_DEV_MISSING_WORD: "missing_word",
    BTN_HOOK_DEV_LYRIC_ECHO: "lyric_echo",
    BTN_HOOK_DEV_QUESTION: "question_to_track",
    BTN_HOOK_DEV_INVERSE: "inverse_lyric",
}
# F3 «Эффект» — 3-step picker. Each step has a "skip" button; at least one of
# hook/transition/extra must be chosen. Effect ids mirror mlcore/hooks/f3_effect.
BTN_FX_SKIP = "Пропустить"
# step 1 — hook (on the drop)
BTN_FX_HOOK_LIGHT = "Молния"
BTN_FX_HOOK_SHUTTER = "Затвор"
BTN_FX_HOOK_SLOW = "Слоу-шаттер"
_FX_HOOK_BY_BUTTON = {
    BTN_FX_HOOK_LIGHT: "hook_light",
    BTN_FX_HOOK_SHUTTER: "shutter_effect",
    BTN_FX_HOOK_SLOW: "flash_slow_shutter",
}
# step 2 — transition (on cuts)
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
# step 3 — extra grade (0..drop)
BTN_FX_EX_XEROX = "Ксерокс"
BTN_FX_EX_ANALOG = "Аналог-глитч"
BTN_FX_EX_NEON = "Неон"
BTN_FX_EX_OLDCAM = "Старая камера"
# pixel_grain / warm_map убраны из пикера — они тянут .aep, который не доезжает
# до рендер-ноды (см. mlcore/hooks/f3_effect/overlay.py::F3_EXTRAS).
_FX_EXTRA_BY_BUTTON = {
    BTN_FX_EX_XEROX: "xerox",
    BTN_FX_EX_ANALOG: "analog_glitch",
    BTN_FX_EX_NEON: "neon_extract",
    BTN_FX_EX_OLDCAM: "old_camera",
}
# slow-shutter trail extend (only when hook == flash_slow_shutter)
BTN_FX_EXT_STD = "Стандарт"
BTN_FX_EXT_END = "До конца ролика"
BTN_FX_EXT_3 = "3 футажа после"
_FX_EXTEND_BY_BUTTON = {
    BTN_FX_EXT_STD: "",
    BTN_FX_EXT_END: "to_end",
    BTN_FX_EXT_3: "after_drop:3",
}
# F2 «Объект» — 5 shape buttons (single sub-picker). Maps to f2_shape, the rest
# of the combo (hook_light on drop + seeded-random F3 transition on post-drop
# cuts) is forced server-side.
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
VERSION_BUTTONS = [BTN_VER_1, BTN_VER_2, BTN_VER_3, BTN_VER_4, BTN_VER_5]
SUBTITLES_MODE_BUTTONS = [
    BTN_SUB_MODE_LEGACY,
    BTN_SUB_MODE_IMPULSE,
    BTN_SUB_MODE_SCENES,
    BTN_SUB_MODE_SCENES_SINGLE,
    BTN_SUB_MODE_4TH,
    BTN_SUB_MODE_TRENDY,
    BTN_SUB_MODE_BRAT,
]
_SUBTITLES_MODE_BY_BUTTON = {
    BTN_SUB_MODE_LEGACY: SUBTITLES_MODE_LEGACY_BLOCKS,
    BTN_SUB_MODE_IMPULSE: SUBTITLES_MODE_IMPULSE_2ND,
    BTN_SUB_MODE_SCENES: SUBTITLES_MODE_SCENES_3RD,
    BTN_SUB_MODE_SCENES_SINGLE: SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    BTN_SUB_MODE_4TH: SUBTITLES_MODE_TEMPLATE_4TH,
    BTN_SUB_MODE_TRENDY: SUBTITLES_MODE_TRENDY_5TH,
    BTN_SUB_MODE_BRAT: SUBTITLES_MODE_BRAT_5TH,
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
    BTN_SUB_MODE_TRENDY,
    BTN_SUB_MODE_BRAT,
    BTN_LAUNCH,
    BTN_NEXT,
    BTN_REUSE_INPUT,
    BTN_COLOR_DEFAULT,
    *COLOR_PALETTE_BUTTONS,
    *VERSION_BUTTONS,
}


# 28-case bigtest matrix: each entry maps to ChatState hook fields.
# F3 tested in isolation (one step at a time); F4/F5 per device.
# hook_drop_t and hook_analysis_bpm are NOT in this dict — reused from state.
_BIGTEST_CASES: List[Dict[str, Any]] = [
    # ── Baseline ────────────────────────────────────────────────────────────
    {"label": "Без хука",
     "hook_enabled": False, "hook_category": "", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    # ── F3 / Хук на дропе ───────────────────────────────────────────────────
    {"label": "F3/хук: Молния",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "hook_light", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/хук: Затвор",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "shutter_effect", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/хук: Слоу-шаттер",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "flash_slow_shutter", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/хук: Слоу-шаттер (до конца)",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "flash_slow_shutter", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "to_end"},
    {"label": "F3/хук: Слоу-шаттер (3 футажа)",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "flash_slow_shutter", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "after_drop:3"},
    # ── F3 / Переходы (step 2 only) ─────────────────────────────────────────
    {"label": "F3/переход: Снап-вайп",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "snap_wipe", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/переход: Минимакс",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "minimax", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/переход: Инверт",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "invert_flash", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/переход: Экстракт",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "extract_flash", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/переход: Вспышки",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "flash_on_cuts", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F3/переход: Тряска",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "layer_shake", "effect_extra": "", "effect_hook_extend": ""},
    # ── F3 / Грейды (step 3 only) ────────────────────────────────────────────
    {"label": "F3/грейд: Ксерокс",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "xerox", "effect_hook_extend": ""},
    {"label": "F3/грейд: Аналог-глитч",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "analog_glitch", "effect_hook_extend": ""},
    {"label": "F3/грейд: Неон",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "neon_extract", "effect_hook_extend": ""},
    {"label": "F3/грейд: Старая камера",
     "hook_enabled": True, "hook_category": "effect", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "old_camera", "effect_hook_extend": ""},
    # pixel_grain / warm_map убраны — тянут .aep, не доезжающий до ноды.
    # ── F4 / Движение ────────────────────────────────────────────────────────
    {"label": "F4/движение: Свайп",
     "hook_enabled": True, "hook_category": "motion", "hook_device": "swipe",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F4/движение: Тап",
     "hook_enabled": True, "hook_category": "motion", "hook_device": "tap",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F4/движение: Зум",
     "hook_enabled": True, "hook_category": "motion", "hook_device": "pinch",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F4/движение: Задержи палец",
     "hook_enabled": True, "hook_category": "motion", "hook_device": "holdfinger",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F4/движение: Качай головой",
     "hook_enabled": True, "hook_category": "motion", "hook_device": "head",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    # ── F5 / Мысль ───────────────────────────────────────────────────────────
    {"label": "F5/мысль: Панчлайн",
     "hook_enabled": True, "hook_category": "thought", "hook_device": "punchline",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F5/мысль: Пропущенное слово",
     "hook_enabled": True, "hook_category": "thought", "hook_device": "missing_word",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F5/мысль: Эхо",
     "hook_enabled": True, "hook_category": "thought", "hook_device": "lyric_echo",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F5/мысль: Вопрос к треку",
     "hook_enabled": True, "hook_category": "thought", "hook_device": "question_to_track",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    {"label": "F5/мысль: Инверсия",
     "hook_enabled": True, "hook_category": "thought", "hook_device": "inverse_lyric",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": ""},
    # ── F2 / Объект (shape packaged-combo, requires drop) ────────────────────
    {"label": "F2/объект: Ромб",
     "hook_enabled": True, "hook_category": "object", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "",
     "f2_shape": "rhomb"},
    {"label": "F2/объект: Квадрат",
     "hook_enabled": True, "hook_category": "object", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "",
     "f2_shape": "square"},
    {"label": "F2/объект: Звезда-10",
     "hook_enabled": True, "hook_category": "object", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "",
     "f2_shape": "star1"},
    {"label": "F2/объект: Звезда-5",
     "hook_enabled": True, "hook_category": "object", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "",
     "f2_shape": "star2"},
    {"label": "F2/объект: Эллипс",
     "hook_enabled": True, "hook_category": "object", "hook_device": "",
     "effect_hook": "", "effect_transition": "", "effect_extra": "", "effect_hook_extend": "",
     "f2_shape": "elipse"},
]


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

        # Season flow (Hooks S1) — phase reader (Redis) + qualified-after-intro
        # referrals. Both rely on shared Redis / Postgres initialized in _on_startup.
        self.season_phase: PhaseStore = PhaseStore(
            self.store.redis,
            prefix=settings.season_redis_prefix,
        )
        self.season_referrals: SeasonReferralStore | None = None

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
        async def _on_start(message: Message, command: CommandObject) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            user_changed = self._sync_state_user_from_message(st, message)
            if user_changed:
                await self.store.set(st)
            await self._ensure_user_profile(st)

            # Parse referral deep-link payload BEFORE flow routing so the
            # inviter↔invitee link is recorded even if the user abandons
            # mid-intro and only completes onboarding much later.
            inviter_chat_id = parse_start_param(getattr(command, "args", None))
            if inviter_chat_id and self.season_referrals is not None:
                if inviter_chat_id != chat_id:
                    try:
                        await self.season_referrals.register(chat_id, inviter_chat_id)
                    except Exception as exc:
                        log.warning("season_referral_register chat=%s err=%r", chat_id, exc)

            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            flow = await self._resolve_flow(chat_id, st)
            if flow == "existing":
                await self._move_to_wait_audio(chat_id, message)
                return

            await self._season_resume_or_start(chat_id, st, message)

        @self.router.callback_query()
        async def _on_callback(cb: CallbackQuery) -> None:
            if cb.data is None:
                try:
                    await cb.answer()
                except Exception:
                    pass
                return
            if cb.data.startswith("season:"):
                await self._season_handle_callback(cb)
                return
            try:
                await cb.answer()
            except Exception:
                pass

        @self.router.message(Command("bigtest"))
        async def _on_bigtest(message: Message) -> None:
            if not BIGTEST_ENABLED:
                await message.answer("Эта команда недоступна.")
                return
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Генерация в процессе. Дождись завершения и повтори /bigtest.")
                return
            if not str(st.batch_audio_s3_url or "").strip():
                await message.answer(
                    "Для /bigtest нужен трек с предыдущей генерации.\n"
                    "Сделай хотя бы один обычный ролик, а потом запускай bigtest."
                )
                return
            total = len(_BIGTEST_CASES)
            bot_inst = self._require_bot()

            # ── RESUME: continue an interrupted run without re-rendering done cases.
            #   /bigtest resume                       → use the saved resume point
            #   /bigtest resume <source_job_id> <idx> → explicit (e.g. recover a run
            #                                            whose state was already lost)
            _parts = str(message.text or "").split()
            if len(_parts) >= 2 and _parts[1].lower() == "resume":
                if len(_parts) >= 4:
                    resume_source = _parts[2].strip()
                    try:
                        resume_idx = int(_parts[3])
                    except ValueError:
                        await message.answer("Использование: /bigtest resume <source_job_id> <from_index(0-based)>")
                        return
                else:
                    resume_source = str(st.bigtest_resume_source_job or "").strip()
                    resume_idx = int(st.bigtest_resume_index or 0)
                if not resume_source or resume_idx <= 0:
                    await message.answer(
                        "Нет сохранённой точки возобновления. Запусти обычный /bigtest "
                        "или укажи явно: /bigtest resume <source_job_id> <from_index>."
                    )
                    return
                if resume_idx >= total:
                    await message.answer(f"Индекс возобновления {resume_idx} ≥ размера пула {total}. Нечего продолжать.")
                    return
                ok, why, src_mode = await self._bigtest_precheck_reuse_source(resume_source)
                if not ok:
                    await bot_inst.send_message(
                        chat_id,
                        f"⛔ /bigtest resume: источник job={resume_source} непригоден к reuse: {why}.\n"
                        f"Укажи job_id последнего УСПЕШНОГО кейса: /bigtest resume <job_id> <from_index>.",
                    )
                    return
                st.bigtest_mode = True
                st.bigtest_index = resume_idx
                st.bigtest_total = total
                st.bigtest_current_label = ""
                st.bigtest_master_job_id = resume_source
                if src_mode:
                    st.subtitles_mode = src_mode
                # Restore the clip window + drop from the source job's request.
                # A prior halt's _reset_processing_state zeroes user_clip_*; without
                # this, reframe/F4/F5/F2 cases enqueue with user_clip_end_sec=0 and
                # the orchestrator rejects (422: end must be > start).
                try:
                    _sj = await self.orchestrator.get_job(resume_source)
                    _rq = _sj.get("request") if isinstance(_sj.get("request"), dict) else {}
                    _ce = _rq.get("user_clip_end_sec")
                    _cs = _rq.get("user_clip_start_sec")
                    _dt = _rq.get("user_drop_t")
                    if isinstance(_ce, (int, float)) and float(_ce) > 0:
                        st.user_clip_end_sec = float(_ce)
                    if isinstance(_cs, (int, float)) and float(_cs) >= 0:
                        st.user_clip_start_sec = float(_cs)
                    if st.hook_drop_t is None and isinstance(_dt, (int, float)):
                        st.hook_drop_t = float(_dt)
                except Exception as _e:
                    log.warning("bigtest_resume_clip_restore_failed src=%s err=%r", resume_source, _e)
                await bot_inst.send_message(
                    chat_id,
                    f"🔬 Bigtest RESUME: продолжаю с кейса [{resume_idx + 1}/{total}] "
                    f"(пропускаю уже готовые 1–{resume_idx}).\n"
                    f"Источник reuse: job={resume_source} (режим {st.subtitles_mode}, без LLM).",
                )
                await self._bigtest_try_enqueue_from_current(st, bot_inst)
                return

            last_master = str(st.master_job_id or "").strip()
            # The whole bigtest replicates ONE reference generation (the last
            # master) and only varies effects. If that source is present it MUST
            # be a SUCCEEDED job with a valid resume_state, otherwise case-0 would
            # silently reuse a broken/blocks job (proven failure: a FAILED blocks
            # job became master → every case rendered blocks instead of scenes_3rd).
            # Validate it up front; pin case-0's request mode to the source's REAL
            # cached mode so the request matches the seed and reuse holds.
            src_mode = ""
            if last_master:
                ok, why, src_mode = await self._bigtest_precheck_reuse_source(last_master)
                if not ok:
                    await bot_inst.send_message(
                        chat_id,
                        f"⛔ /bigtest не запущен: источник (последняя генерация "
                        f"job={last_master}) непригоден к reuse: {why}.\n"
                        f"Сделай УСПЕШНУЮ генерацию в нужном режиме субтитров "
                        f"(например scenes 3rd) и повтори /bigtest — все 28 кейсов "
                        f"возьмут её субтитры/тайминги/футаж, меняя только эффекты.",
                    )
                    st.bigtest_mode = False
                    await self.store.set(st)
                    return
            st.bigtest_mode = True
            st.bigtest_index = 0
            st.bigtest_total = total
            st.bigtest_current_label = ""
            st.bigtest_master_job_id = last_master
            # Fresh run from case 0 — drop any stale resume point.
            st.bigtest_resume_index = 0
            st.bigtest_resume_source_job = ""
            # Pin subtitles_mode to the source job's REAL rendered mode (ground
            # truth from its resume_state). Without a source, fall back to the last
            # explicit choice, then current value.
            if src_mode:
                st.subtitles_mode = src_mode
            elif str(st.last_subtitles_mode or "").strip():
                st.subtitles_mode = str(st.last_subtitles_mode).strip()
            if last_master:
                reuse_note = (
                    f"Кейс 1 переиспользует resume_state job={last_master} "
                    f"(режим {st.subtitles_mode}, ASR/субтитры/футаж — без LLM). "
                    f"Кейсы 2–{total} переиспользуют кейс 1."
                )
            else:
                reuse_note = (
                    f"⚠️ Нет источника resume_state (master_job_id пуст).\n"
                    f"Кейс 1 прогонит ASR + subtitles полностью (режим {st.subtitles_mode}). "
                    f"Кейсы 2–{total} переиспользуют результат кейса 1 — LLM только 1 раз."
                )
            await bot_inst.send_message(
                chat_id,
                f"🔬 Bigtest: {total} кейсов на том же треке.\n"
                f"Результат каждого будет подписан названием кейса.\n"
                f"{reuse_note}",
            )
            await self._bigtest_try_enqueue_from_current(st, bot_inst)

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

            if st.stage in SEASON_STAGES:
                # Season flow is driven by inline buttons; ignore freeform
                # text and re-show the menu so the user can keep navigating.
                if st.stage == STAGE_SEASON_MENU:
                    await self._season_show_menu(chat_id, st, message)
                else:
                    await self._season_resume_or_start(chat_id, st, message)
                return

            if st.stage in {STAGE_IDLE, ""}:
                flow = await self._resolve_flow(chat_id, st)
                if flow == "season":
                    await self._season_resume_or_start(chat_id, st, message)
                    return
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

            if st.stage == STAGE_WAIT_SUBTITLE_COLOR:
                await self._handle_wait_subtitle_color(message, st)
                return

            if st.stage == STAGE_WAIT_ACCENT_COLOR:
                await self._handle_wait_accent_color(message, st)
                return

            if st.stage == STAGE_WAIT_SUBTITLES_MODE:
                await self._handle_wait_subtitles_mode(message, st)
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

            if st.stage == STAGE_WAIT_EFFECT_EXTRA:
                await self._handle_wait_effect_extra(message, st)
                return

            if st.stage == STAGE_WAIT_EFFECT_EXTEND:
                await self._handle_wait_effect_extend(message, st)
                return

            if st.stage == STAGE_WAIT_F2_SHAPE:
                await self._handle_wait_f2_shape(message, st)
                return

            if st.stage == STAGE_WAIT_F1_SOUND:
                await self._handle_wait_f1_sound(message, st)
                return

            if st.stage == STAGE_WAIT_F1_TEXT:
                await self._handle_wait_f1_text(message, st)
                return

            if st.stage == STAGE_WAIT_BATTERY_SOUND:
                await self._handle_wait_battery_sound(message, st)
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
            self.season_referrals = SeasonReferralStore(self.users.pool)
            log.info("startup: PostgreSQL pool ready, user_store + season active")
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

    # ------------------------------------------------------------------ #
    # Season flow (Hooks S1) — entry-point routing + onboarding + menu
    # ------------------------------------------------------------------ #

    async def _resolve_flow(self, chat_id: int, st: ChatState) -> str:
        """Decide whether this chat belongs to the legacy or season flow."""
        # Kill-switch: when SEASON_FLOW_ENABLED is off (default), always route
        # to the existing product flow so generation works end-to-end while
        # we polish the season UX. Flip the env var to "1" to re-enable.
        if not _season_flow_enabled():
            return "existing"
        if self.users is None:
            # Without a DB we can't know status — default to legacy product so
            # local dev without CREDITS_DB_URL keeps working unchanged.
            return "existing"
        season = await self.users.get_season_state(chat_id)
        if season is None:
            return "season"
        # Mirror DB fields into chat state so renderers don't re-query Postgres.
        st.season_intro_step = season["intro_step"]
        st.season_intro_completed = season["intro_completed"]
        st.season_update_frequency = season["update_frequency"]
        st.season_account_status = season["account_status"]
        st.season_waitlist = season["waitlist"]
        st.season_referrer_tier = season["referrer_tier"]
        st.season_referrals_count = season["referrals_count"]
        await self.store.set(st)
        return determine_flow(season["account_status"], season["paid_until"])

    async def _season_resume_or_start(
        self, chat_id: int, st: ChatState, message: Message,
    ) -> None:
        """Send the right onboarding step on /start in the season flow."""
        if st.season_intro_completed:
            await self._season_show_menu(chat_id, st, message)
            return
        step = max(0, min(3, int(st.season_intro_step or 0)))
        if step >= 3:
            # Steps 1-2 are intro screens; step 3 = consent (per TZ §2 Msg 3).
            await self._season_send_consent(chat_id, st, message)
            return
        # Always (re)start from step 1 to keep the narrative coherent — the
        # tiny cost of replaying intro_1 outweighs landing the user mid-arc.
        await self._season_send_intro(chat_id, st, message, step=1)

    async def _season_send_intro(
        self,
        chat_id: int,
        st: ChatState,
        message_or_cb: Message | CallbackQuery,
        *,
        step: int,
    ) -> None:
        text = {1: INTRO_1, 2: INTRO_2}.get(step)
        if text is None:
            return
        stage = {
            1: STAGE_SEASON_INTRO_1,
            2: STAGE_SEASON_INTRO_2,
        }[step]
        st.stage = stage
        await self.store.set(st)
        if self.users is not None:
            await self.users.set_intro_step(chat_id, step)
        await self._season_send(
            message_or_cb, text, reply_markup=intro_next_kb(),
        )

    async def _season_send_consent(
        self, chat_id: int, st: ChatState, message_or_cb: Message | CallbackQuery,
    ) -> None:
        st.stage = STAGE_SEASON_CONSENT
        await self.store.set(st)
        if self.users is not None:
            await self.users.set_intro_step(chat_id, 3)
        await self._season_send(message_or_cb, CONSENT, reply_markup=consent_kb())

    async def _season_show_menu(
        self, chat_id: int, st: ChatState, message_or_cb: Message | CallbackQuery,
        *, prefix_text: Optional[str] = None,
    ) -> None:
        st.stage = STAGE_SEASON_MENU
        await self.store.set(st)
        text = prefix_text + "\n\n" + MENU_HEADER if prefix_text else MENU_HEADER
        await self._season_send(message_or_cb, text, reply_markup=menu_kb())

    async def _season_send(
        self,
        message_or_cb: Message | CallbackQuery,
        text: str,
        *,
        reply_markup=None,
    ) -> None:
        """Helper that sends a new message regardless of trigger type.

        For callbacks we send a fresh message (instead of editing) so the user
        keeps the full thread of the onboarding visible.
        """
        if isinstance(message_or_cb, CallbackQuery):
            target = message_or_cb.message
        else:
            target = message_or_cb
        if target is None:
            return
        await target.answer(text, reply_markup=reply_markup, parse_mode="HTML")

    async def _season_handle_callback(self, cb: CallbackQuery) -> None:
        if cb.data is None or cb.message is None or cb.message.chat is None:
            return
        chat_id = int(cb.message.chat.id)
        st = await self.store.get(chat_id)
        data = cb.data

        # Re-sync DB → state on every callback so a stale state.cache doesn't
        # let a user proceed past consent without intro_completed actually set.
        if self.users is not None:
            await self._resolve_flow(chat_id, st)

        try:
            if data == CB_INTRO_NEXT:
                await self._on_intro_next(chat_id, st, cb)
            elif data == CB_CONSENT_ALL:
                await self._on_consent(chat_id, st, cb, frequency="all")
            elif data == CB_CONSENT_FINALS:
                await self._on_consent(chat_id, st, cb, frequency="finals_only")
            elif data == CB_MENU_GENERATION:
                await self._on_menu_generation(chat_id, st, cb)
            elif data == CB_MENU_PRICING:
                await self._on_menu_pricing(chat_id, st, cb)
            elif data == CB_MENU_EXAMPLES:
                await self._on_menu_examples(chat_id, st, cb)
            elif data == CB_MENU_ABOUT:
                await self._on_menu_about(chat_id, st, cb)
            elif data == CB_MENU_INVITE:
                await self._on_menu_invite(chat_id, st, cb)
            elif data == CB_MENU_HISTORY:
                await self._on_menu_history(chat_id, st, cb)
            elif data == CB_MENU_BACK:
                await self._season_show_menu(chat_id, st, cb)
            elif data == CB_WAITLIST_JOIN:
                await self._on_waitlist(chat_id, st, cb, joined=True)
            elif data == CB_WAITLIST_SKIP:
                await self._on_waitlist(chat_id, st, cb, joined=False)
            elif data in (CB_NOTIF_ALL, CB_NOTIF_FINALS, CB_NOTIF_OFF):
                choice = {
                    CB_NOTIF_ALL: "all",
                    CB_NOTIF_FINALS: "finals_only",
                    CB_NOTIF_OFF: "off",
                }[data]
                await self._on_notification_pref(chat_id, st, cb, choice=choice)
            else:
                log.info("season_cb_unknown chat=%s data=%s", chat_id, data)
        finally:
            try:
                await cb.answer()
            except Exception:
                pass

    async def _on_intro_next(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        current = {
            STAGE_SEASON_INTRO_1: 1,
            STAGE_SEASON_INTRO_2: 2,
        }.get(st.stage, 0)
        if current < 2:
            await self._season_send_intro(chat_id, st, cb, step=2)
        else:
            await self._season_send_consent(chat_id, st, cb)

    async def _on_consent(
        self, chat_id: int, st: ChatState, cb: CallbackQuery, *, frequency: str,
    ) -> None:
        st.season_intro_completed = True
        st.season_update_frequency = frequency
        await self.store.set(st)
        if self.users is not None:
            await self.users.complete_intro(chat_id, update_frequency=frequency)
        # Qualify the inviter (if any) the moment onboarding completes.
        await self._qualify_inviter_if_any(chat_id)
        snap = await self.season_phase.snapshot()
        await self._season_show_menu(chat_id, st, cb, prefix_text=WELCOME(snap))

    async def _qualify_inviter_if_any(self, invitee_chat_id: int) -> None:
        if self.season_referrals is None or self._bot is None:
            return
        try:
            result = await self.season_referrals.mark_qualified(invitee_chat_id)
        except Exception as exc:
            log.warning("season_qualify err chat=%s err=%r", invitee_chat_id, exc)
            return
        if result is None or not result.qualified_now:
            return
        # Notify the inviter — friend joined; mention tier-up if reached.
        msg = (
            f"👥 Друг пришёл по твоей ссылке. "
            f"Всего: <b>{result.inviter_new_count}/5</b>."
        )
        if result.tier_up:
            msg += f"\n\n🎉 Тир <b>{result.inviter_new_tier}</b> разблокирован."
        try:
            await self._bot.send_message(
                result.inviter_chat_id, msg, parse_mode="HTML",
            )
        except Exception as exc:
            log.info(
                "season_notify_inviter_skipped inviter=%s err=%r",
                result.inviter_chat_id, exc,
            )

    async def _on_menu_generation(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        snap = await self.season_phase.snapshot()
        text = render_generation_screen(snap, account_status=st.season_account_status)
        from core.season_phase import SeasonPhase as _SP  # local to avoid wider import surface
        if snap.phase in (_SP.DEV_EARLY, _SP.DEV_LATE):
            kb = waitlist_kb(already_in=st.season_waitlist)
        else:
            kb = back_to_menu_kb()
        await self._season_send(cb, text, reply_markup=kb)

    async def _on_menu_pricing(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        text = render_pricing_screen()
        await self._season_send(cb, text, reply_markup=back_to_menu_kb())

    async def _on_menu_examples(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        text = render_examples_screen(
            brand_account_link=self.settings.season_brand_account_link,
        )
        await self._season_send(cb, text, reply_markup=back_to_menu_kb())

    async def _on_menu_about(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        snap = await self.season_phase.snapshot()
        text = render_about_season(
            snap,
            tt_link=self.settings.season_tt_link,
            tg_link=self.settings.season_tg_link,
        )
        await self._season_send(cb, text, reply_markup=back_to_menu_kb())

    async def _on_menu_invite(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        link = build_referral_link(self.settings.tg_bot_username, chat_id)
        if self.season_referrals is not None:
            count, tier = await self.season_referrals.stats_for(chat_id)
        else:
            count, tier = (0, 0)
        text = render_invite_screen(
            referral_link=link, referrals_count=count, tier=tier,
        )
        await self._season_send(cb, text, reply_markup=share_kb(link))

    async def _on_menu_history(self, chat_id: int, st: ChatState, cb: CallbackQuery) -> None:
        total = 0
        if self.users is not None:
            season = await self.users.get_season_state(chat_id)
            if season is not None:
                total = season["total_gens"]
        text = render_history_screen(total_gens=total)
        await self._season_send(
            cb, text, reply_markup=notifications_kb(current=st.season_update_frequency),
        )

    async def _on_waitlist(
        self, chat_id: int, st: ChatState, cb: CallbackQuery, *, joined: bool,
    ) -> None:
        st.season_waitlist = joined
        await self.store.set(st)
        if self.users is not None:
            await self.users.set_waitlist(chat_id, joined=joined)
        snap = await self.season_phase.snapshot()
        text = render_generation_screen(snap, account_status=st.season_account_status)
        await self._season_send(cb, text, reply_markup=waitlist_kb(already_in=joined))

    async def _on_notification_pref(
        self, chat_id: int, st: ChatState, cb: CallbackQuery, *, choice: str,
    ) -> None:
        st.season_update_frequency = choice if choice != "off" else "finals_only"
        await self.store.set(st)
        if self.users is not None:
            await self.users.set_notification_pref(chat_id, choice)
        await self._season_send(
            cb, "Готово. Настройки уведомлений обновлены.",
            reply_markup=notifications_kb(current=st.season_update_frequency),
        )

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
            # No focus clip = no hook analysis (would have to analyze the whole
            # track). User can still enable hook later — they will get
            # algorithmic top-1 on the full track as the default candidate.
            await self._ask_lyrics_choice(message, st)
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
        # Kick off the hook analysis in the background — by the time the user
        # finishes lyrics/fragment/bg/footage/subtitles the result is ready.
        await self._trigger_hook_analysis_task(st)
        await self._ask_lyrics_choice(message, st)

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
            # Phase A-UX: bg now comes after fragment (was after timing), so
            # the back step lands on the fragment choice rather than timing.
            await self._ask_fragment_choice(message, st)
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
                [BTN_SUB_MODE_TRENDY],
                [BTN_SUB_MODE_BRAT],
            ),
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
    def _wait_next_kb(can_reuse: bool) -> ReplyKeyboardMarkup:
        if can_reuse:
            return _kb([BTN_NEXT], [BTN_REUSE_INPUT])
        return _kb([BTN_NEXT])

    @staticmethod
    def _apply_bigtest_config(st: ChatState, case: Dict[str, Any]) -> None:
        """Overlay bigtest hook fields onto state. hook_drop_t / hook_analysis_bpm
        are NOT touched — they are reused from whatever was set in prior runs."""
        st.hook_enabled = bool(case.get("hook_enabled", False))
        st.hook_category = str(case.get("hook_category", ""))
        st.hook_device = str(case.get("hook_device", ""))
        st.effect_hook = str(case.get("effect_hook", ""))
        st.effect_transition = str(case.get("effect_transition", ""))
        st.effect_extra = str(case.get("effect_extra", ""))
        st.effect_hook_extend = str(case.get("effect_hook_extend", ""))
        # F2 «Объект» (shape) / F1 «Звук» (sound url). Set from the case when
        # present, otherwise cleared so a prior F2/F1 case does not leak into
        # the next one. The send_audio_s3 callsite gates f2_shape on
        # hook_category == "object" (and f1_sound_url on "sound").
        st.f2_shape = str(case.get("f2_shape", ""))
        st.f1_sound_url = str(case.get("f1_sound_url", ""))
        st.f1_sound_text = str(case.get("f1_sound_text", ""))
        st.subtitle_color_hex = str(case.get("subtitle_color_hex", ""))
        st.accent_color_hex = str(case.get("accent_color_hex", ""))
        # Battery: each case may carry its OWN drop (a later candidate when the
        # format needs more lead, e.g. F4). Set it so each version reframes with
        # the right drop. bigtest cases omit this → drop reused from state.
        if case.get("hook_drop_t") is not None:
            st.hook_drop_t = float(case["hook_drop_t"])

    async def _handle_wait_audio(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_TRACK:
            await message.answer("Жду аудио-файл.")
            return
        if text == BTN_REUSE_INPUT:
            if not self._can_reuse_input(st):
                await message.answer(
                    "Не вижу сохранённого трека. Нажми «Отправить трек» и пришли файл.",
                    reply_markup=_kb([BTN_SEND_TRACK]),
                )
                return
            st.versions_count = 1
            await self._ask_bg_mode(message, st)
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
        size_mb = prep.size_bytes / (1024 * 1024)
        limit_note = "<= лимита" if prep.under_limit else "> лимита (best-effort)"
        await message.answer(
            f"Трек готов: mp3 {prep.bitrate}, {size_mb:.2f}MB ({limit_note})."
        )
        # Phase A-UX: timing comes FIRST after audio (was after fragment), so
        # the bot can launch a background hook-analysis task while the user
        # fills in lyrics / fragment / footage / subtitles.
        await self._ask_timing_choice(message, st)

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
            await self._ask_bg_mode(message, st)
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
        await self._ask_bg_mode(message, st)

    async def _handle_wait_subtitles_mode(self, message: Message, st: ChatState) -> None:
        mode = _parse_subtitles_mode_choice(message.text or "")
        if mode is None:
            await message.answer(
                "Выбери режим кнопкой: «Обычные blocks», «Impulse 2nd», "
                "«Scenes 3rd», «Scenes 3rd Single-Step» или «Template 4th»."
            )
            return
        st.subtitles_mode = mode
        await self._ask_subtitle_color(message, st)

    def _color_kb(self):
        # Palette in rows of 3 + default on its own row.
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
            "Акцентный цвет (фигуры «Объект» + фокус-слово)? Палитра или «По умолчанию».",
            reply_markup=self._color_kb(),
        )

    async def _handle_wait_accent_color(self, message: Message, st: ChatState) -> None:
        choice = _parse_color_choice(message.text or "")
        if choice is None:
            await message.answer("Выбери цвет кнопкой из палитры или «По умолчанию».")
            return
        st.accent_color_hex = choice
        await self.store.set(st)
        await self._ask_hook_choice(message, st)

    # ---------- Phase A-UX helpers (lyrics / fragment factoring) ----------

    async def _ask_lyrics_choice(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_LYRICS_CHOICE
        await self.store.set(st)
        await message.answer(
            "Хочешь прислать текст песни для субтитров?",
            reply_markup=_kb([BTN_SEND_LYRICS, BTN_SKIP_LYRICS]),
        )

    async def _ask_fragment_choice(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_FRAGMENT_CHOICE
        await self.store.set(st)
        await message.answer(
            "Хочешь указать интересующий фрагмент?",
            reply_markup=_kb([BTN_SEND_FRAGMENT, BTN_SKIP_FRAGMENT]),
        )

    # ---------- Phase A-UX: hook flow ----------

    async def _trigger_hook_analysis_task(self, st: ChatState) -> None:
        """
        Fire-and-forget background analysis of the user-picked focus clip.
        Stores top-3 drop candidates in chat state by the time the user reaches
        the hook step. Idempotent: a second call with the same audio path and
        window is a no-op.
        """
        audio_path = str(st.prepared_audio_local_path or "").strip()
        if not audio_path:
            log.info("hook_bg_skip reason=no_audio chat=%s", st.chat_id)
            return
        clip_start = float(st.user_clip_start_sec or 0.0)
        clip_end = float(st.user_clip_end_sec or 0.0)
        if clip_end <= clip_start:
            # No explicit focus clip — skip background analysis. The user can
            # still opt into hook later; we will run the analysis on demand
            # when they reach WAIT_HOOK_CHOICE.
            log.info("hook_bg_skip reason=no_focus_clip chat=%s", st.chat_id)
            return
        # De-dup: if we already analyzed this exact path+window, do nothing.
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
        """Background runner — must never raise into the asyncio loop.

        The bot image is slim (no librosa). It uploads the focus-clip audio to
        S3 and asks the orchestrator (runtime image, has librosa) to run
        analyze_focus_clip, returning {bpm, drop_candidates} for the picker.
        """
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
                # Keep the FULL detected pool (not just the top 3): the battery
                # auto-walks it to find a later drop with enough pre-roll for
                # F4/F5. The user-facing drop picker still shows only cands[:3].
                for c in raw_cands
                if isinstance(c, dict) and c.get("t") is not None
            ]
            bpm = float(result.get("bpm") or 0.0)
            st = await self.store.get(chat_id)
            # Stale audio guard: only persist if the user hasn't changed clip.
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
                log.info(
                    "hook_bg_ok chat=%s bpm=%.2f cands=%d",
                    chat_id, bpm, len(candidates),
                )
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
        # If the background analysis exists, hint at it; otherwise stay neutral.
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
        rows = [[BTN_HOOK_YES, BTN_HOOK_NO]]
        if BATTERY_ENABLED:
            rows.append([BTN_HOOK_BATTERY])
        await message.answer(
            "Сделать хук в ролик? Хук — это короткий FX-акцент на дропе, "
            "помогает удерживать зрителя." + note
            + ("\n\n🎲 Батарея — один трек, по ролику на каждую категорию хука "
               "с рандомными настройками." if BATTERY_ENABLED else ""),
            reply_markup=_kb(*rows),
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
            st.battery_mode = False
            st.battery_cases = []
            await self.store.set(st)
            await self._ask_versions(message, st)
            return
        if text == BTN_HOOK_YES:
            st.hook_enabled = True
            st.battery_mode = False
            await self.store.set(st)
            await self._ask_hook_drop(message, st)
            return
        if BATTERY_ENABLED and text == BTN_HOOK_BATTERY:
            st.hook_enabled = True
            st.battery_mode = True
            await self.store.set(st)
            # Battery reuses the drop picker; after a drop is chosen we branch to
            # the (optional) F1 sound, then enqueue one video per category.
            await self._ask_hook_drop(message, st)
            return
        await message.answer(
            f"Выбери кнопку: «{BTN_HOOK_YES}» или «{BTN_HOOK_NO}».",
        )

    async def _ask_hook_drop(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_HOOK_DROP
        await self.store.set(st)
        # Build button rows from cached candidates.
        cands = list(st.hook_drop_candidates or [])
        primary_rows: List[List[str]] = []
        if cands:
            for idx, c in enumerate(cands[:3]):
                t_label = self._fmt_timing(float(c["t"]))
                conf_label = f"{int(round(float(c['confidence']) * 100))}%"
                tag = "🎯 " if idx == 0 else ""
                primary_rows.append([f"{tag}{t_label} ({conf_label})"])
        primary_rows.append([BTN_HOOK_DROP_NONE])
        primary_rows.append([BTN_HOOK_DROP_MANUAL])
        primary_rows.append([BTN_BACK])
        if not cands:
            if st.hook_analysis_status == "pending":
                hint = (
                    "Анализ ещё не готов — попробуй через несколько секунд, "
                    "или выбери «Ввести вручную» / «В отрывке нет дропа»."
                )
            elif st.hook_analysis_status == "failed":
                hint = (
                    f"Анализ не удался ({st.hook_analysis_error or 'unknown'}). "
                    "Можно ввести тайминг вручную."
                )
            else:
                hint = (
                    "Анализ дропа недоступен (нет focus clip). "
                    "Введи тайминг вручную, либо «В отрывке нет дропа»."
                )
            await message.answer(hint, reply_markup=_kb(*primary_rows))
            return
        await message.answer(
            "Выбери момент дропа. 🎯 — лучший кандидат, остальные — близкие "
            "альтернативы. Если ни один не подходит — «Ввести вручную».\n\n"
            "ℹ️ Хук строится по сценарию: ~3–4с разгон ДО дропа + 10–12с кора "
            "ПОСЛЕ. Выбирай дроп так, чтобы после него в отрывке осталось "
            "~10–12с трека.",
            reply_markup=_kb(*primary_rows),
        )

    async def _handle_wait_hook_drop(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_choice(message, st)
            return
        if text == BTN_HOOK_DROP_NONE:
            if st.battery_mode:
                await message.answer("Для батареи нужен момент дропа — выбери его кнопкой или введи вручную.")
                return
            st.hook_drop_t = None
            await self.store.set(st)
            await self._ask_hook_type(message, st)
            return
        if text == BTN_HOOK_DROP_MANUAL:
            st.stage = STAGE_WAIT_HOOK_DROP_MANUAL
            await self.store.set(st)
            await message.answer(
                "Отправь момент дропа в формате 1:23 или 83 (секунды).",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        # Otherwise: parse a label like "1:23 (87%)" or "🎯 1:23 (87%)".
        chosen = self._parse_hook_drop_label(text, candidates=st.hook_drop_candidates)
        if chosen is None:
            await message.answer(
                "Не распознал выбор — нажми одну из кнопок ниже.",
            )
            return
        if not self._validate_hook_drop_inside_clip(chosen, st):
            await message.answer(
                "Момент дропа должен быть внутри выбранного фрагмента. "
                "Попробуй другой вариант или введи вручную.",
            )
            return
        st.hook_drop_t = float(chosen)
        await self.store.set(st)
        await message.answer(
            f"Дроп зафиксирован на {self._fmt_timing(float(chosen))}."
        )
        await self._after_hook_drop(message, st)

    async def _after_hook_drop(self, message: Message, st: ChatState) -> None:
        """Route after a drop is fixed: battery → F1 sound step; else category."""
        if st.battery_mode:
            await self._ask_battery_sound(message, st)
        else:
            await self._ask_hook_type(message, st)

    # ── Hook battery (team bot): one button → N videos, one per category ──
    async def _ask_battery_sound(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_BATTERY_SOUND
        await self.store.set(st)
        await message.answer(
            "🎲 Батарея: пришли звук для ролика «Звук» (F1) — он заиграет до дропа.\n"
            "Или «Без звука» → сгенерю 4 ролика (Объект/Эффект/Движение/Мысль).",
            reply_markup=_kb([BTN_BATTERY_NO_SOUND], [BTN_BACK]),
        )

    async def _handle_wait_battery_sound(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_BACK:
            await self._ask_hook_drop(message, st)
            return
        if text == BTN_BATTERY_NO_SOUND:
            st.f1_sound_url = ""
            await self.store.set(st)
            await self._start_battery(message, st)
            return
        spec = _extract_audio_spec(message)
        if spec is None:
            await message.answer("Нужен аудио-файл (mp3/m4a/wav) или нажми «Без звука».")
            return
        if message.chat is None:
            return
        chat_id = int(message.chat.id)
        file_id, original_name = spec
        incoming_dir = self.settings.tmp_dir / str(chat_id) / "hook_sound"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        src_path = incoming_dir / f"{_now_tag()}_{uuid.uuid4().hex[:8]}_{_safe_name(original_name)}"
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
        except Exception as e:
            log.exception("battery_sound_upload_failed chat=%s err=%s", chat_id, e)
            await message.answer(f"Не удалось загрузить звук: {e}. Попробуй ещё раз или «Без звука».")
            return
        st.f1_sound_url = str(sound_url)
        await self.store.set(st)
        await self._start_battery(message, st)

    def _build_battery_cases(self, st: ChatState) -> List[Dict[str, Any]]:
        """One random case per hook category (no category repeats within a track).
        Each case carries its OWN hook_drop_t: most use the primary drop, but
        formats with a hard timing requirement (F4 needs drop ≥ lead; F1 needs
        drop−clip_start > 1s) walk the next drop candidates until one fits — and
        are dropped from the battery if none does. Sub-picks are random per press.
        """
        import random
        rng = random.Random()
        clip_start = float(st.user_clip_start_sec or 0.0)
        bpm = float(st.hook_analysis_bpm or 0.0)

        # Ordered, de-duplicated drop candidates: primary first, then the rest.
        drops: List[float] = []
        if st.hook_drop_t is not None:
            drops.append(float(st.hook_drop_t))
        for c in (st.hook_drop_candidates or []):
            try:
                t = float(c.get("t"))
            except (TypeError, ValueError):
                continue
            if all(abs(t - d) > 1e-6 for d in drops):
                drops.append(t)
        primary = drops[0] if drops else None
        clip_end = float(st.user_clip_end_sec or 0.0)

        def _pick(predicate) -> Optional[float]:
            for d in drops:
                if predicate(d):
                    return d
            return None

        def _leaves_enough_clip(reframed_start: float) -> bool:
            # After a reframe, clip = [reframed_start, clip_end]. Reject drops that
            # would leave < MIN_REFRAME_CLIP_SEC (else the build's fast-start
            # validator trips and the post-drop content is tiny). Unknown clip_end
            # → no upper bound.
            if clip_end <= 0.0:
                return True
            return (clip_end - reframed_start) >= MIN_REFRAME_CLIP_SEC

        def _pick_nearest(predicate) -> Optional[float]:
            # Among fitting candidates, choose the one CLOSEST to the user's drop
            # — not the highest-score one, which may sit near the clip end and
            # make the reframe eat almost the whole clip.
            ok = [d for d in drops if predicate(d)]
            if not ok:
                return None
            anchor = primary if primary is not None else ok[0]
            return min(ok, key=lambda d: abs(d - anchor))

        cases: List[Dict[str, Any]] = []

        # F2 «Объект» — primary drop (no extra lead requirement).
        if primary is not None:
            shape = rng.choice(["rhomb", "square", "star1", "star2", "elipse"])
            cases.append({"label": f"Объект: {shape}", "hook_enabled": True,
                          "hook_category": "object", "f2_shape": shape,
                          "hook_drop_t": primary})

        # F3 «Эффект» — primary drop.
        if primary is not None:
            fx_hook = rng.choice(["hook_light", "shutter_effect", "flash_slow_shutter"])
            fx_tr = rng.choice(["snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts"])  # layer_shake excluded (re-tune)
            fx_extra = rng.choice(["", "xerox", "analog_glitch", "neon_extract", "old_camera"])
            cases.append({"label": "Эффект", "hook_enabled": True, "hook_category": "effect",
                          "effect_hook": fx_hook, "effect_transition": fx_tr,
                          "effect_extra": fx_extra, "hook_drop_t": primary})

        # F4 «Движение» — needs bpm AND a drop that (a) has room before it to
        # reframe (drop ≥ lead) and (b) leaves enough clip after the reframe.
        # Pick the candidate NEAREST the user's drop (avoids grabbing a strong but
        # too-late drop that shrinks the clip below the fast-start). Skip if none.
        if bpm > 0.0 and drops:
            dev = rng.choice(["swipe", "tap", "pinch", "holdfinger", "head"])
            lead = self._f4_effective_lead(dev, bpm)
            f4_drop = _pick_nearest(
                lambda d: d - lead >= 0.0 and _leaves_enough_clip(d - lead)
            )
            if f4_drop is not None:
                cases.append({"label": f"Движение: {dev}", "hook_enabled": True,
                              "hook_category": "motion", "hook_device": dev,
                              "hook_drop_t": f4_drop})
            else:
                log.info("battery: F4 skipped — no drop fits lead=%.2f + min-clip=%.1f (bpm=%.1f)",
                         lead, MIN_REFRAME_CLIP_SEC, bpm)

        # F5 «Мысль» — use the user's primary drop directly. F5's lead is adaptive
        # (clip_start = max(0, drop − F5_LEAD_SEC)), so the primary drop always
        # reframes safely and stays musically where the user picked it. We do NOT
        # auto-walk to a later candidate (that risked grabbing a too-late drop and
        # shrinking the clip).
        if primary is not None:
            dev5 = rng.choice(["punchline", "missing_word", "lyric_echo", "question_to_track", "inverse_lyric"])
            cases.append({"label": f"Мысль: {dev5}", "hook_enabled": True,
                          "hook_category": "thought", "hook_device": dev5,
                          "hook_drop_t": primary})

        # F1 «Звук» — only with an uploaded sound; needs drop−clip_start > 1s.
        if str(st.f1_sound_url or "").strip() and drops:
            f1_drop = _pick(lambda d: (d - clip_start) > 1.0)
            if f1_drop is not None:
                cases.append({"label": "Звук", "hook_enabled": True, "hook_category": "sound",
                              "f1_sound_url": str(st.f1_sound_url), "hook_drop_t": f1_drop})
            else:
                log.info("battery: F1 skipped — no drop candidate with drop−clip_start > 1s")

        # Carry the user's picked colors onto every battery case so
        # _apply_bigtest_config doesn't wipe them (cases default colors to "").
        for c in cases:
            c["subtitle_color_hex"] = str(st.subtitle_color_hex or "")
            c["accent_color_hex"] = str(st.accent_color_hex or "")

        return cases[:5]

    async def _start_battery(self, message: Message, st: ChatState) -> None:
        if st.hook_drop_t is None:
            await message.answer("Для батареи нужен дроп — выбери его.")
            await self._ask_hook_drop(message, st)
            return
        cases = self._build_battery_cases(st)
        if not cases:
            await message.answer("Не удалось собрать батарею. Попробуй другой трек.")
            return
        st.battery_cases = cases
        st.versions_count = len(cases)
        st.stage = STAGE_WAIT_CONFIRM
        await self.store.set(st)
        labels = "\n".join(f"{i + 1}. {c['label']}" for i, c in enumerate(cases))
        await message.answer(
            f"🎲 Батарея: {len(cases)} ролика, по одному на категорию:\n{labels}\n\n"
            f"Дроп {self._fmt_timing(float(st.hook_drop_t))}. Запустить?",
            reply_markup=_kb([BTN_LAUNCH]),
        )

    async def _handle_wait_hook_drop_manual(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        parsed = self._parse_single_timing(text)
        if parsed is None:
            await message.answer(
                "Не распознал тайминг. Формат: 1:23 или 83 (секунды). Попробуй ещё раз."
            )
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
        await self._after_hook_drop(message, st)

    async def _send_hook_intro(self, message: Message, key: str) -> None:
        """Send a hook option's intro: video+caption once a clip is set, else
        text. Same upgrade path as the subtitle-mode previews."""
        intro = hook_intro(key)
        if not intro:
            return
        if intro["video"]:
            await message.answer_video(
                video=intro["video"], caption=intro["text"], parse_mode="Markdown"
            )
        else:
            await message.answer(intro["text"], parse_mode="Markdown")

    async def _ask_hook_type(self, message: Message, st: ChatState) -> None:
        """Hook category picker — per-category intro (text now, video later) + 5 buttons."""
        st.stage = STAGE_WAIT_HOOK_TYPE
        await self.store.set(st)
        for _key in HOOK_CATEGORY_ORDER:
            await self._send_hook_intro(message, _key)
        await message.answer(
            "Выбери тип хука:",
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
            await message.answer(
                f"«{text}» пока в разработке — скоро добавим. "
                "Сейчас доступны «Мысль», «Движение», «Эффект», «Объект»."
            )
            return
        if text == BTN_HOOK_CAT_OBJECT:
            # F2 packaged combo needs a drop anchor (pre/post split + hook_light).
            if st.hook_drop_t is None:
                await message.answer(
                    "Для «Объекта» нужен момент дропа — вернись и выбери его."
                )
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "object"
            st.f2_shape = ""
            await self.store.set(st)
            await self._ask_f2_shape(message, st)
            return
        if text == BTN_HOOK_CAT_SOUND:
            # F1 combo needs a drop anchor (audio window [0.5, drop−0.5] + combo).
            if st.hook_drop_t is None:
                await message.answer(
                    "Для «Звука» нужен момент дропа — вернись и выбери его."
                )
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
            # F3 visual FX needs a drop anchor (hook lands on the drop).
            if st.hook_drop_t is None:
                await message.answer(
                    "Для «Эффекта» нужен момент дропа — вернись и выбери его."
                )
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "effect"
            st.effect_hook = ""
            st.effect_transition = ""
            st.effect_extra = ""
            st.effect_hook_extend = ""
            await self.store.set(st)
            await self._ask_effect_hook(message, st)
            return
        if text == BTN_HOOK_CAT_THOUGHT:
            st.hook_category = "thought"
            await self.store.set(st)
            await self._ask_hook_device(message, st)
            return
        if text == BTN_HOOK_CAT_MOTION:
            # F4 motion overlay needs a drop to align against (cover-end == drop).
            if st.hook_drop_t is None:
                await message.answer(
                    "Для «Движения» нужен момент дропа — вернись и выбери его."
                )
                await self._ask_hook_drop(message, st)
                return
            st.hook_category = "motion"
            await self.store.set(st)
            await self._ask_hook_device(message, st)
            return
        await message.answer("Выбери тип хука кнопкой ниже.")

    async def _ask_hook_device(self, message: Message, st: ChatState) -> None:
        """Device sub-picker. Category-aware: F4 «Движение» vs F5 «Мысль»."""
        st.stage = STAGE_WAIT_HOOK_DEVICE
        await self.store.set(st)
        if st.hook_category == "motion":
            await message.answer(
                "Какой приём «Движения»? Рука/голова двигается в такт, "
                "на дропе срабатывает вспышка:\n"
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
            # Variant B: lead scales with bpm exactly like the JSX (refBpm/bpm),
            # so the overlay cover-end lands on the drop at any tempo.
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
            st.hook_type = "standard"  # legacy compat field
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
            await self._ask_versions(message, st)
            return

        device = _HOOK_DEVICE_BY_BUTTON.get(text)
        if device is None:
            await message.answer("Выбери приём кнопкой ниже.")
            return
        st.hook_device = device
        st.hook_type = "standard"  # legacy compat field
        await self.store.set(st)
        await message.answer(f"Ок, «Мысль»: {text}.")
        await self._ask_versions(message, st)

    # ── F3 «Эффект» — 3-step picker (hook -> transition -> extra) + extend ──
    async def _ask_effect_hook(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_HOOK
        await self.store.set(st)
        await message.answer(
            "«Эффект» — шаг 1/3: хук на дропе.\n"
            "• Молния — вспышка-молнии + шейк.\n"
            "• Затвор — нарезка затвора + лого-штамп.\n"
            "• Слоу-шаттер — echo-шлейф + вспышка (можно растянуть).\n"
            "Можно пропустить, если хук не нужен.",
            reply_markup=_kb(
                [BTN_FX_HOOK_LIGHT, BTN_FX_HOOK_SHUTTER],
                [BTN_FX_HOOK_SLOW],
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
            await self._ask_effect_transition(message, st)
            return
        hook = _FX_HOOK_BY_BUTTON.get(text)
        if hook is None:
            await message.answer("Выбери хук кнопкой ниже или «Пропустить».")
            return
        st.effect_hook = hook
        await self.store.set(st)
        await self._ask_effect_transition(message, st)

    async def _ask_effect_transition(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_TRANSITION
        await self.store.set(st)
        await message.answer(
            "Шаг 2/3: переход на склейках футажа.\n"
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
        await message.answer(
            "Шаг 3/3: стилизация футажа до дропа (грейд 00:00 → дроп).\n"
            "Можно пропустить.",
            reply_markup=_kb(
                [BTN_FX_EX_XEROX, BTN_FX_EX_ANALOG],
                [BTN_FX_EX_NEON, BTN_FX_EX_OLDCAM],
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
        await self.store.set(st)
        # at least one of hook/transition/extra is required
        if not (st.effect_hook or st.effect_transition or st.effect_extra):
            await message.answer(
                "Нужно выбрать хотя бы один эффект из трёх. Начнём заново с хука."
            )
            await self._ask_effect_hook(message, st)
            return
        # slow-shutter trail extend sub-step (only for the extendable hook)
        if st.effect_hook == "flash_slow_shutter":
            await self._ask_effect_extend(message, st)
            return
        await self._effect_summary_and_continue(message, st)

    async def _ask_effect_extend(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_EFFECT_EXTEND
        await self.store.set(st)
        await message.answer(
            "Слоу-шаттер: длина echo-шлейфа?\n"
            "• Стандарт — короткий импульс.\n"
            "• До конца ролика — футажи красиво наслаиваются.\n"
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
            await self._ask_effect_extra(message, st)
            return
        if text not in _FX_EXTEND_BY_BUTTON:
            await message.answer("Выбери вариант длины кнопкой ниже.")
            return
        st.effect_hook_extend = _FX_EXTEND_BY_BUTTON[text]
        await self.store.set(st)
        await self._effect_summary_and_continue(message, st)

    async def _effect_summary_and_continue(self, message: Message, st: ChatState) -> None:
        st.hook_type = "standard"  # legacy compat field
        await self.store.set(st)
        parts: List[str] = []
        if st.effect_hook:
            parts.append(f"хук «{st.effect_hook}»")
        if st.effect_transition:
            parts.append(f"переход «{st.effect_transition}»")
        if st.effect_extra:
            parts.append(f"грейд «{st.effect_extra}»")
        if st.effect_hook_extend:
            parts.append(f"растяжка «{st.effect_hook_extend}»")
        await message.answer("Ок, «Эффект»: " + ", ".join(parts) + ".")
        await self._ask_versions(message, st)

    # ── F2 «Объект» — single shape sub-picker (rest of combo is server-side) ──
    async def _ask_f2_shape(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_F2_SHAPE
        await self.store.set(st)
        await message.answer(
            "Какая фигура-переход на склейках до дропа?\n"
            "На дропе сработает молния, после дропа — рандомный визуал-переход.\n"
            "• Ромб — полигон 4 точки.\n"
            "• Квадрат — белый солид.\n"
            "• Звезда-10 — 10 лучей, тонкая обводка.\n"
            "• Звезда-5 — 5 лучей, тонкая обводка.\n"
            "• Эллипс — белый круг.",
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
            # Defensive: category-level check should have caught this, but the
            # state can be re-entered from a back-nav after the drop got cleared.
            await message.answer(
                "Для «Объекта» нужен момент дропа — вернись и выбери его."
            )
            await self._ask_hook_drop(message, st)
            return
        st.f2_shape = shape
        st.hook_type = "standard"  # legacy compat field
        await self.store.set(st)
        await message.answer(
            f"Ок, «Объект»: фигура «{text}». На склейках до дропа — она; "
            f"на дропе — молния; после дропа — рандомные F3-переходы."
        )
        await self._ask_versions(message, st)

    # ── F1 «Звук» — upload a pre-drop sound (no LLM; user provides the file) ──
    async def _ask_f1_sound(self, message: Message, st: ChatState) -> None:
        st.stage = STAGE_WAIT_F1_SOUND
        await self.store.set(st)
        await message.answer(
            "«Звук»: пришли аудио-файл, который заиграет ДО дропа (разгон/риза).\n"
            "Он встанет в окно до хука; на дропе сработает молния, после — "
            "рандомный визуал-переход.\n"
            "Просто отправь аудио сообщением (mp3/m4a/wav).",
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
                "Нужен аудио-файл для «Звука». Пришли mp3/m4a/wav сообщением "
                "или нажми «Назад»."
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
                bot=message.bot,
                file_id=file_id,
                dest=src_path,
                chat_id=chat_id,
                original_name=original_name,
            )
            key = self._build_raw_audio_key(chat_id=chat_id, file_name=f"f1hook_{src_path.name}")
            sound_url = await asyncio.to_thread(
                self.s3.upload_file,
                path=src_path,
                bucket=self.settings.s3_bucket_raw_audio,
                key=key,
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
        st.hook_type = "standard"  # legacy compat field
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
            await self._ask_versions(message, st)
            return
        if not text:
            await message.answer(
                "Пришли текст субтитра сообщением или нажми «Без субтитров»."
            )
            return

        st.f1_sound_text = text
        await self.store.set(st)
        await message.answer("Принял текст субтитра для звука.")
        await self._ask_versions(message, st)

    @staticmethod
    def _f4_effective_lead(device: str, bpm: float) -> float:
        """F4 «Движение» effective lead = LEAD[device] * refBpm/bpm (Variant B).

        Matches the JSX, which reflows internal timings by refBpm/bpm — so the
        clip-window reframe (clip_start = drop - lead_eff) keeps the overlay
        cover-end exactly on the drop at any tempo. Falls back to the unscaled
        lead if bpm is missing (caller is expected to guard bpm > 0).
        """
        from mlcore.hooks.f4_motion.overlay import F4_REF_BPM, LEAD_BY_DEVICE

        lead = float(LEAD_BY_DEVICE[device])
        if bpm and float(bpm) > 0.0:
            return lead * (float(F4_REF_BPM) / float(bpm))
        return lead

    @staticmethod
    def _parse_single_timing(text: str) -> Optional[float]:
        """Parse a single mm:ss or seconds string. Returns None on bad input."""
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
    def _parse_hook_drop_label(
        text: str, *, candidates: List[Dict[str, Any]]
    ) -> Optional[float]:
        """
        Reverse-match a button label like "🎯 1:23 (87%)" back to the candidate
        time. Compares formatted labels so we tolerate copy-paste / lookalike
        characters without parsing the number directly.
        """
        s = str(text or "").strip()
        if not s:
            return None
        s = s.lstrip("🎯 ").strip()
        for c in candidates or []:
            try:
                t = float(c["t"])
                conf = float(c["confidence"])
            except Exception:
                continue
            label = f"{BlastBotApp._fmt_timing(t)} ({int(round(conf * 100))}%)"
            if s == label:
                return t
        return None

    @staticmethod
    def _validate_hook_drop_inside_clip(t_sec: float, st: ChatState) -> bool:
        cs = float(st.user_clip_start_sec or 0.0)
        ce = float(st.user_clip_end_sec or 0.0)
        if ce <= cs:
            # No focus clip → accept any non-negative value.
            return float(t_sec) >= 0.0
        return cs <= float(t_sec) <= ce

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

            # Battery: each version is an INDEPENDENT job with a different hook
            # config, so force the parallel/per-version path (no master reuse).
            if self.settings.bot_enqueue_all_versions_async or st.battery_cases:
                next_version_to_enqueue = int(versions) + 1
                for version_index in range(1, int(versions) + 1):
                    # Overlay this version's hook battery case before enqueue.
                    if st.battery_cases and version_index <= len(st.battery_cases):
                        self._apply_bigtest_config(st, st.battery_cases[version_index - 1])
                        await self.store.set(st)
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
                        # Battery: each version is independent — one bad version
                        # must NOT skip the rest (e.g. an F4 reframe error used to
                        # break the loop and drop F5/F1). Log and continue.
                        if st.battery_cases:
                            log.warning("battery version %d skipped: %s", version_index, e)
                            continue
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

            # Battery done — clear so the next normal run isn't affected.
            st.battery_cases = []
            st.battery_mode = False

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

        if text == BTN_REUSE_INPUT:
            if not self._can_reuse_input(st):
                await message.answer(
                    "Не вижу сохранённого трека. Нажми «Отправить трек» и пришли файл.",
                    reply_markup=_kb([BTN_SEND_TRACK]),
                )
                return
            if message.chat is None:
                return
            # Reset selection fields, preserve audio/lyrics/timing from previous run.
            st.hook_enabled = False
            st.hook_category = ""
            st.hook_device = ""
            st.hook_drop_t = None
            st.effect_hook = ""
            st.effect_transition = ""
            st.effect_extra = ""
            st.effect_hook_extend = ""
            st.footage_genre_key = ""
            st.footage_artist_key = ""
            st.footage_artist_id = ""
            st.versions_count = 1
            st.bigtest_mode = False
            st.bigtest_index = 0
            st.bigtest_total = 0
            st.bigtest_current_label = ""
            await self._ask_bg_mode(message, st)
            return

        if text != BTN_NEXT:
            await message.answer(
                "Если хочешь новый ролик, нажми «Сделать следующий».",
                reply_markup=self._wait_next_kb(self._can_reuse_input(st)),
            )
            return

        if message.chat is None:
            return

        await self._move_to_wait_audio(int(message.chat.id), message)

    async def _bigtest_precheck_reuse_source(self, master_job_id: str) -> Tuple[bool, str, str]:
        """Safety-breaker Layer 1 (precondition): before enqueuing case idx>0,
        confirm the reuse-source job carries a reusable resume_state. Returns
        (ok, reason_if_not_ok, source_subtitles_mode). The third element is the
        source job's cached stage2_subtitles_mode — the deterministic ground
        truth the case must echo so the request mode matches the seed (otherwise
        the orchestrator invalidates the subtitles cache and renders the default
        blocks mode). Structural check only (slim bot has no mlcore models); the
        orchestrator does the full model_validate (+ cjson coercion)."""
        jid = str(master_job_id or "").strip()
        if not jid:
            return False, "master_job_id пуст", ""
        # Retry get_job a few times: a transient orchestrator hiccup must NOT
        # abort the whole batch (it previously caused a silent halt).
        job = None
        last_err: Exception | None = None
        for _attempt in range(3):
            try:
                job = await self.orchestrator.get_job(jid)
                break
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.5)
        if job is None:
            return False, f"get_job не ответил (3 попытки): {last_err!r}", ""
        status = str(job.get("status") or "").upper()
        if status != "SUCCEEDED":
            return False, f"источник не SUCCEEDED (status={status or 'unknown'})", ""
        res = job.get("result") if isinstance(job.get("result"), dict) else {}
        rs = res.get("resume_state") if isinstance(res.get("resume_state"), dict) else {}
        if not rs:
            return False, "resume_state пуст", ""
        asr = rs.get("stage1_asr")
        if not isinstance(asr, dict):
            return False, "нет stage1_asr", ""
        tw = asr.get("transcript_words")
        if not isinstance(tw, list) or not tw:
            return False, "stage1_asr.transcript_words пуст", ""
        if not isinstance(rs.get("stage2_subtitles"), dict):
            return False, "нет stage2_subtitles", ""
        src_mode = str(rs.get("stage2_subtitles_mode") or "").strip()
        if not src_mode:
            return False, "нет stage2_subtitles_mode", ""
        return True, "", src_mode

    async def _bigtest_halt(self, *, st: ChatState, bot: Bot, text: str) -> None:
        """Tear down a /bigtest run (no more cases) and notify, preserving the
        audio so the operator can retry. Used by both safety-breaker layers."""
        # Plain text on purpose: the message interpolates dynamic error reprs
        # which can contain '<', '>', '&'. With parse_mode=HTML those break
        # Telegram parsing, the send fails, and the halt becomes SILENT — the
        # operator sees nothing. Plain text always delivers.
        try:
            await bot.send_message(st.chat_id, text)
        except Exception as e:
            log.warning("bigtest_halt_notify_failed chat=%s err=%r", st.chat_id, e)
        st.bigtest_mode = False
        st.bigtest_index = 0
        st.bigtest_total = 0
        st.bigtest_current_label = ""
        _saved_audio = str(st.batch_audio_s3_url or "")
        self._reset_processing_state(st)
        st.batch_audio_s3_url = _saved_audio
        await self.store.set(st)

    async def _bigtest_emergency_stop(self, *, st: ChatState, bot: Bot, job_id: str, reason: str) -> None:
        """Safety-breaker Layer 2 (runtime abort): a reuse case actually
        re-invoked Stage1 ASR. Kill it and halt the whole batch."""
        idx = int(st.bigtest_index)
        label = str(st.bigtest_current_label or "")
        try:
            await self.orchestrator.kill_job(job_id, reason=f"bigtest_reuse_failed:{reason}")
        except Exception as e:
            log.warning("bigtest_emergency_kill_failed chat=%s job=%s err=%r", st.chat_id, job_id, e)
        log.warning(
            "bigtest_emergency_stop chat=%s idx=%d job=%s reason=%s", st.chat_id, idx, job_id, reason
        )
        await self._bigtest_halt(
            st=st, bot=bot,
            text=(
                f"⛔ Bigtest ПРЕРВАН на кейсе [{idx + 1}/{st.bigtest_total}] «{label}».\n"
                f"Причина: reuse не сработал — кейс заново запустил stage1 ASR через LLM "
                f"({reason}). Убил джобу и остановил батч, чтобы не жечь токены "
                f"на остальных кейсах.\n"
                f"resume_state первого кейса непригоден к переиспользованию — нужен разбор."
            ),
        )

    async def _bigtest_try_enqueue_from_current(self, st: ChatState, bot: Bot) -> None:
        """Outer guard: never let the bigtest enqueue loop die SILENTLY. Any
        uncaught error is surfaced to the operator with a resume hint, and the
        run is stopped cleanly (instead of leaving the user staring at a frozen
        chat with no job and no message)."""
        try:
            await self._bigtest_try_enqueue_from_current_impl(st, bot)
        except Exception as e:
            log.exception(
                "bigtest_enqueue_loop_failed chat=%s idx=%s err=%r",
                st.chat_id, st.bigtest_index, e,
            )
            _idx = int(st.bigtest_index or 0)
            _src = str(st.bigtest_master_job_id or "").strip()
            try:
                hint = (f"\nПродолжить: /bigtest resume {_src} {_idx}" if _src else "")
                await bot.send_message(
                    st.chat_id,
                    f"⛔ Bigtest остановлен из-за внутренней ошибки на кейсе "
                    f"[{_idx + 1}/{st.bigtest_total}]: {_compact_text(str(e), limit=160)}{hint}",
                )
            except Exception:
                pass
            st.bigtest_mode = False
            st.bigtest_index = 0
            st.bigtest_total = 0
            st.bigtest_current_label = ""
            st.stage = STAGE_WAIT_NEXT
            try:
                await self.store.set(st)
            except Exception:
                pass

    async def _bigtest_try_enqueue_from_current_impl(self, st: ChatState, bot: Bot) -> None:
        """Enqueue bigtest_index case. Skips (with message) any cases that
        fail to enqueue (e.g. F4 without drop_t), then tries the next one.
        Calls itself-as-loop until a case succeeds or all remaining are skipped."""
        while st.bigtest_index < st.bigtest_total:
            idx = int(st.bigtest_index)
            # Safety-breaker Layer 1: before any reuse case (idx>0), verify the
            # source job's resume_state is reuse-ready. If not, halt the batch
            # now rather than re-running LLM on every remaining case.
            if idx > 0:
                ok, why, src_mode = await self._bigtest_precheck_reuse_source(st.bigtest_master_job_id)
                if not ok:
                    await self._bigtest_halt(
                        st=st, bot=bot,
                        text=(
                            f"⛔ Bigtest ПРЕРВАН перед кейсом [{idx + 1}/{st.bigtest_total}].\n"
                            f"resume_state источника непригоден к reuse: {why}.\n"
                            f"Остановил, чтобы не гонять LLM заново на остальных кейсах."
                        ),
                    )
                    return
                # Echo the source job's actual subtitles_mode so this case's
                # request matches the seeded resume_state (else the orchestrator
                # invalidates the subtitles cache and renders blocks).
                if src_mode:
                    st.subtitles_mode = src_mode
            case = _BIGTEST_CASES[idx]
            label = str(case["label"])
            st.bigtest_current_label = label
            self._apply_bigtest_config(st, case)

            new_batch_id = f"bigtest-{st.chat_id}-{idx}-{uuid.uuid4().hex[:6]}"
            st.batch_id = new_batch_id
            st.active_job_id = ""
            st.active_job_ids = []
            st.completed_job_ids = []
            st.job_order = []
            st.next_version_to_enqueue = 2
            st.batch_total_versions = 1
            st.versions_count = 1
            st.master_job_id = ""
            st.last_status_msg_at = 0.0
            st.status_message_id = 0
            st.last_status_text = ""
            st.poll_attempts = 0
            st.last_job_stage = ""
            st.last_job_error = ""

            try:
                job_id = await self._enqueue_batch_version(
                    st=st,
                    audio_s3_url=str(st.batch_audio_s3_url or ""),
                    version_index=1,
                    versions_total=1,
                    batch_id=new_batch_id,
                    reuse_text_job_id=str(st.bigtest_master_job_id or ""),
                    # Reuse the footage STYLE from the source whenever one exists —
                    # including case-0, which inherits the validated source's genre
                    # so the footage-style LLM never runs in bigtest at all. Case-0
                    # then picks clips with its own seed and stores it; cases 1-27
                    # pin that seed so every case yields identical clips. Without a
                    # source (master empty) case-0 picks footage fresh.
                    reuse_stage2_footage=bool(str(st.bigtest_master_job_id or "").strip()),
                    stage2_selection_seed_override=(
                        str(st.bigtest_footage_seed) if idx > 0 and st.bigtest_footage_seed else None
                    ),
                )
                st.active_job_id = job_id
                st.active_job_ids = [job_id]
                st.job_order = [job_id]
                st.master_job_id = job_id
                # Store case-0's selection seed so cases 1-27 can pin to it.
                if idx == 0:
                    st.bigtest_footage_seed = f"{new_batch_id}:v1"
                st.active_job_started_at = time.time()
                st.stage = STAGE_PROCESSING
                await bot.send_message(
                    st.chat_id,
                    f"🔬 [{idx + 1}/{st.bigtest_total}] {label}",
                )
                await self.store.set(st)
                return  # waiting for poll loop to call us back on completion
            except Exception as e:
                log.warning("bigtest_skip chat=%s idx=%d label=%s err=%r", st.chat_id, idx, label, e)
                await bot.send_message(
                    st.chat_id,
                    f"⏭ [{idx + 1}/{st.bigtest_total}] {label} — пропущен: {_compact_text(str(e), limit=120)}",
                )
                st.bigtest_index += 1

        # All remaining cases exhausted (all skipped)
        await bot.send_message(
            st.chat_id,
            f"🎬 Bigtest завершён: все оставшиеся кейсы пропущены.\nСделать следующий?",
            reply_markup=self._wait_next_kb(self._can_reuse_input(st)),
        )
        st.bigtest_mode = False
        st.bigtest_index = 0
        st.bigtest_total = 0
        st.bigtest_current_label = ""
        st.stage = STAGE_WAIT_NEXT
        await self.store.set(st)

    def _build_raw_audio_key(self, *, chat_id: int, file_name: str) -> str:
        safe = _safe_name(file_name)
        return f"{self.settings.s3_raw_audio_prefix.strip('/')}/{chat_id}/{_now_tag()}_{uuid.uuid4().hex[:10]}_{safe}"

    async def _resolve_rotation_slot_for_enqueue(
        self, *, st: ChatState, offset: int = 0
    ) -> Tuple[str, str, List[str]]:
        """Return (theme, group, persistent_history_names) for the current user.

        Returns empty ("", "", []) when artist_id has no rotation slots
        (unknown artist or no themes) — callers should then skip override.

        `offset` spreads a multi-version batch across consecutive rotation slots:
        version 0 keeps the persisted cursor (the advance-on-exhaustion base),
        versions 1..N step forward so each battery video lands on a different
        subgroup instead of all sharing one slot.
        """
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
        reuse_stage2_footage: bool = False,
        stage2_selection_seed_override: Optional[str] = None,
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

        # F4 «Движение»: reframe the clip window so the overlay's cover-end lands
        # exactly on the hook. clip_start := drop - LEAD[device]; clip_end stays.
        # The literal user-picked start is intentionally discarded (we may extend
        # backward into earlier track footage). Floored at 0 (else blocked earlier).
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
            # Reframe the clip window so the overlay's cover-end lands exactly on
            # the drop: clip_start := drop - lead_eff (Variant B: lead scales with
            # bpm like the JSX). clip_end is unchanged. The literal user-picked
            # start is intentionally discarded — for a motion hook the rolled clip
            # IS [drop-lead, end], and Stage1 subtitles align to that same window.
            lead = self._f4_effective_lead(f4_device, bpm)
            new_start = float(st.hook_drop_t) - lead
            if new_start < 0.0:
                raise RuntimeError(
                    f"F4 reframe: drop {st.hook_drop_t} - lead {lead:.3f} (bpm={bpm}) < 0 "
                    "(hook too close to track start)"
                )
            user_clip_start_sec = new_start
            if user_clip_end_sec is None or user_clip_end_sec <= new_start:
                user_clip_end_sec = float(end)
            # Send the SAME bpm to the orchestrator so the overlay's t() scaling
            # matches the reframe → cover-end lands exactly on the drop.
            f4_bpm = bpm

        # F5 «Мысль»: reframe the clip toward the drop just like F4 so the TTS
        # voice (~3s) plays in the run-up and lands INTO the drop, with the
        # post-drop focus line right after. Without this the voice plays from
        # clip-second-0 and the user-drop sits wherever the user picked → total
        # desync (worst with a pre-cut mp3 fragment as input). clip_start :=
        # drop − F5_LEAD_SEC; clip_end stays. The post-drop focus line and
        # drop_at_sec are derived orchestrator-side from USER_DROP_T − clip_start,
        # so they follow this reframe automatically.
        if st.hook_enabled and st.hook_category == "thought" and st.hook_device:
            if st.hook_drop_t is None:
                raise RuntimeError("F5 thought hook requires a drop (hook_drop_t)")
            # Adaptive lead: the voice gets up to F5_LEAD_SEC of run-up, but never
            # more than the room actually before the drop. clip_start = max(0,
            # drop − F5_LEAD_SEC) ≥ 0 ALWAYS, so F5 never hard-fails on an early
            # drop — it just gets a shorter run-up (voice may slightly overlap the
            # drop). Unlike F4's fixed cover, the voice tolerates this.
            lead_f5 = min(F5_LEAD_SEC, float(st.hook_drop_t))
            new_start = max(0.0, float(st.hook_drop_t) - lead_f5)
            user_clip_start_sec = new_start
            if user_clip_end_sec is None or user_clip_end_sec <= new_start:
                user_clip_end_sec = float(end)

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
            reuse_stage2_footage=bool(reuse_stage2_footage),
            stage2_selection_seed_override=str(stage2_selection_seed_override).strip() if stage2_selection_seed_override else None,
            exclude_file_names=merged_exclude,
            variant_index=int(version_index),
            variants_total=int(versions_total),
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
            effect_transition=(
                str(st.effect_transition)
                if (st.hook_enabled and st.hook_category == "effect" and st.effect_transition)
                else None
            ),
            effect_extra=(
                str(st.effect_extra)
                if (st.hook_enabled and st.hook_category == "effect" and st.effect_extra)
                else None
            ),
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
            subtitle_color_hex=(str(st.subtitle_color_hex) or None),
            accent_color_hex=(str(st.accent_color_hex) or None),
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
                        reply_markup=self._wait_next_kb(self._can_reuse_input(st)),
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
                reply_markup=self._wait_next_kb(self._can_reuse_input(st)),
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

        # In bigtest mode, replace the generic version label with the case name.
        if st.bigtest_mode and str(st.bigtest_current_label or "").strip():
            ver_label = f"🔬 [{st.bigtest_index + 1}/{st.bigtest_total}] {st.bigtest_current_label}"

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
            # Safety-breaker Layer 2: a reuse case (idx>0) actually re-invoked
            # Stage1 ASR (resume failed despite reuse_text_job_id). Detect via the
            # sticky orchestrator flag (or the dedicated stage) and abort the
            # whole /bigtest batch before more tokens burn.
            if st.bigtest_mode and int(st.bigtest_index) > 0:
                _res = job.get("result") if isinstance(job.get("result"), dict) else {}
                if bool(_res.get("reuse_stage1_miss")) or stage == "llm_stage1a_asr_invoke":
                    await self._bigtest_emergency_stop(
                        st=st, bot=bot, job_id=jid, reason="stage1_asr_reinvoked"
                    )
                    return
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

        # Save input fields that _reset_processing_state would otherwise wipe.
        # These are needed for BTN_REUSE_INPUT and /bigtest on the next run.
        _saved_audio_s3 = str(st.batch_audio_s3_url or "")
        _saved_clip_start = float(st.user_clip_start_sec or 0.0)
        _saved_clip_end = float(st.user_clip_end_sec or 0.0)
        _saved_target_fragment = str(st.target_fragment or "")
        _saved_master_job_id = str(st.master_job_id or "")
        # subtitles_mode is reset to LEGACY_BLOCKS by _reset_processing_state;
        # preserve the user's actual choice so bigtest cases all use the same
        # mode as case-0 (mismatch would invalidate the stage2_subtitles cache).
        _saved_subtitles_mode = str(st.subtitles_mode or "")
        # Remember the mode this generation actually ran with. It survives
        # _reset_processing_state so a later /bigtest can pin it as the request
        # mode — matching the reuse-source job's cached stage2_subtitles_mode and
        # thus keeping the LLM cache valid across all 28 cases.
        if _saved_subtitles_mode:
            st.last_subtitles_mode = _saved_subtitles_mode

        # ── Bigtest: advance to the next case instead of returning to idle ──
        if st.bigtest_mode:
            case_succeeded = succeeded_count > 0
            next_idx = st.bigtest_index + 1
            # Promote the just-completed job as the new reuse source ONLY on
            # success — a failed/partial job must never become the source (it
            # would crash or corrupt every following case). On success also record
            # a resume point so `/bigtest resume` can continue from next_idx using
            # this succeeded case as the reuse source instead of re-rendering.
            if case_succeeded and _saved_master_job_id:
                st.bigtest_master_job_id = _saved_master_job_id
                st.bigtest_resume_index = next_idx
                st.bigtest_resume_source_job = _saved_master_job_id
            if next_idx < st.bigtest_total:
                st.bigtest_index = next_idx
                self._reset_processing_state(st)
                st.batch_audio_s3_url = _saved_audio_s3
                st.user_clip_start_sec = _saved_clip_start
                st.user_clip_end_sec = _saved_clip_end
                st.target_fragment = _saved_target_fragment
                st.subtitles_mode = _saved_subtitles_mode
                await self._bigtest_try_enqueue_from_current(st, bot)
                return
            # All cases done — show final summary.
            await bot.send_message(
                st.chat_id,
                f"🎬 Bigtest завершён: {st.bigtest_total} кейсов. "
                f"Успешно: {succeeded_count}, ошибок: {failed_count}.\n"
                "Сделать следующий?",
                reply_markup=self._wait_next_kb(self._can_reuse_input(st)),
            )
            st.bigtest_mode = False
            st.bigtest_index = 0
            st.bigtest_total = 0
            st.bigtest_current_label = ""
            self._reset_processing_state(st)
            st.batch_audio_s3_url = _saved_audio_s3
            st.user_clip_start_sec = _saved_clip_start
            st.user_clip_end_sec = _saved_clip_end
            st.target_fragment = _saved_target_fragment
            st.master_job_id = _saved_master_job_id
            st.versions_count = 1
            await self.store.set(st)
            return

        # ── Normal completion ─────────────────────────────────────────────────
        await bot.send_message(
            st.chat_id,
            summary + "\nСделать следующий?",
            reply_markup=self._wait_next_kb(self._can_reuse_input(st)),
        )

        # Grant referral bonus to inviter if this was the user's first successful generation.
        if succeeded_count > 0:
            await self._maybe_grant_referral_bonus_after_generation(st)

        self._reset_processing_state(st)
        # Restore input fields for BTN_REUSE_INPUT:
        #   audio S3 URL — needed by /bigtest to re-enqueue on the same file
        #   clip timing — preserved so "same track" means same clip window
        #   target_fragment — part of the user's original request
        #   master_job_id — bigtest uses it as reuse_text_job_id
        # pending_audio_file_id / prepared_audio_local_path / lyrics_text are
        # NOT in _reset_processing_state, so they survive naturally.
        # All of these are fully overwritten when the user starts a fresh run.
        st.batch_audio_s3_url = _saved_audio_s3
        st.user_clip_start_sec = _saved_clip_start
        st.user_clip_end_sec = _saved_clip_end
        st.target_fragment = _saved_target_fragment
        st.master_job_id = _saved_master_job_id
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
