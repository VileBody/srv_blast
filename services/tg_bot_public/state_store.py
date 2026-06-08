from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from core.subtitles_mode import SUBTITLES_MODE_IMPULSE_2ND
from .config import Settings

log = logging.getLogger("tg_bot_public.state_store")


STAGE_IDLE = "IDLE"
STAGE_WAIT_START = "WAIT_START"
STAGE_WAIT_SUBSCRIPTION = "WAIT_SUBSCRIPTION"
STAGE_WAIT_AUDIO = "WAIT_AUDIO"
STAGE_WAIT_LYRICS_CHOICE = "WAIT_LYRICS_CHOICE"
STAGE_WAIT_LYRICS_TEXT = "WAIT_LYRICS_TEXT"
STAGE_WAIT_FRAGMENT_CHOICE = "WAIT_FRAGMENT_CHOICE"
STAGE_WAIT_FRAGMENT_TEXT = "WAIT_FRAGMENT_TEXT"
STAGE_WAIT_TIMING_CHOICE = "WAIT_TIMING_CHOICE"
STAGE_WAIT_TIMING_INPUT = "WAIT_TIMING_INPUT"
STAGE_WAIT_BG_MODE = "WAIT_BG_MODE"
STAGE_WAIT_BG_COLOR = "WAIT_BG_COLOR"
STAGE_WAIT_FOOTAGE_GENRE = "WAIT_FOOTAGE_GENRE"
STAGE_WAIT_FOOTAGE_ARTIST = "WAIT_FOOTAGE_ARTIST"
STAGE_WAIT_CONFIRM_TEXT = "WAIT_CONFIRM_TEXT"
STAGE_WAIT_SUBTITLES_MODE = "WAIT_SUBTITLES_MODE"
STAGE_WAIT_CONFIRM_MODE = "WAIT_CONFIRM_MODE"
# Hook feature (Phase A-UX) — parity-mirrored from tg_bot_botapi. The actual
# handlers are not wired into the public user flow; HOOK_FLOW_ENABLED in
# app.py gates entry. This commit just lands the schema so the CI parity
# gate is satisfied and the public bot can deserialize chat states that
# include hook_* fields (e.g. after a state migration test).
STAGE_WAIT_HOOK_CHOICE = "WAIT_HOOK_CHOICE"
STAGE_WAIT_HOOK_DROP = "WAIT_HOOK_DROP"
STAGE_WAIT_HOOK_DROP_MANUAL = "WAIT_HOOK_DROP_MANUAL"
STAGE_WAIT_HOOK_TYPE = "WAIT_HOOK_TYPE"
STAGE_WAIT_HOOK_DEVICE = "WAIT_HOOK_DEVICE"
# F3 «Эффект» — 3-step picker (mirror of tg_bot_botapi; UI gated by HOOK_FLOW_ENABLED).
STAGE_WAIT_EFFECT_HOOK = "WAIT_EFFECT_HOOK"
STAGE_WAIT_EFFECT_TRANSITION = "WAIT_EFFECT_TRANSITION"
STAGE_WAIT_EFFECT_EXTRA = "WAIT_EFFECT_EXTRA"
STAGE_WAIT_EFFECT_EXTEND = "WAIT_EFFECT_EXTEND"
STAGE_WAIT_VERSIONS = "WAIT_VERSIONS"
STAGE_WAIT_CONFIRM = "WAIT_CONFIRM"
STAGE_PROCESSING = "PROCESSING"
STAGE_WAIT_NEXT = "WAIT_NEXT"

# Post-generation flow stages
STAGE_RATE_VIDEO = "RATE_VIDEO"
STAGE_FEEDBACK_LOW = "FEEDBACK_LOW"
STAGE_SALES_PITCH = "SALES_PITCH"
STAGE_PACKAGES_OFFER = "PACKAGES_OFFER"
STAGE_PACKAGE_DETAILS = "PACKAGE_DETAILS"
STAGE_ALL_PACKAGES = "ALL_PACKAGES"
STAGE_PACKAGE_INFO = "PACKAGE_INFO"
STAGE_PURCHASE_CHOICE = "PURCHASE_CHOICE"
STAGE_SUBSCRIPTION_CONFIRM = "SUBSCRIPTION_CONFIRM"
STAGE_WAIT_PAYMENT = "WAIT_PAYMENT"
STAGE_IMPROVEMENT_FEEDBACK = "IMPROVEMENT_FEEDBACK"
STAGE_IMPROVEMENT_OTHER_TEXT = "IMPROVEMENT_OTHER_TEXT"
STAGE_WHY_NOT = "WHY_NOT"
STAGE_NOT_ACTUAL_REASON = "NOT_ACTUAL_REASON"
STAGE_CASES_TECH = "CASES_TECH"
STAGE_TRY_FULL = "TRY_FULL"
STAGE_REFERRAL_ASK = "REFERRAL_ASK"
STAGE_WAIT_REFERRAL_TAG = "WAIT_REFERRAL_TAG"
STAGE_WAITING_REFERRAL = "WAITING_REFERRAL"
STAGE_RATE_VIDEO_2 = "RATE_VIDEO_2"
STAGE_FEEDBACK_LOW_2 = "FEEDBACK_LOW_2"
STAGE_LAST_STEP_FORM = "LAST_STEP_FORM"
STAGE_POST_SURVEY = "POST_SURVEY"
STAGE_KEEP_IN_TOUCH = "KEEP_IN_TOUCH"
STAGE_REMIND_RELEASE = "REMIND_RELEASE"
STAGE_NO_FRIENDS_FORM = "NO_FRIENDS_FORM"

_REFERRAL_PREFIX = "blast:tg:public:referral"
_REFERRAL_TTL_S = 2592000  # 30 days

# Per-user footage rotation + history persistence (survives audio re-uploads).
_ROTATION_TTL_S = 2592000  # 30 days
_ROTATION_HISTORY_MAX = 150


def _normalize_username(raw: str) -> str:
    u = str(raw or "").strip().lower()
    if not u:
        return ""
    if not u.startswith("@"):
        u = "@" + u
    return u


def _parse_bool_text(raw: str, *, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if value in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return bool(default)


# Season flow (Hooks S1) — mirrored from tg_bot_botapi per parity gate.
# In public the season flow is gated by SEASON_FLOW_ENABLED env flag (default
# off); these constants exist so state_store/import-time wiring stays in sync.
STAGE_SEASON_INTRO_1 = "SEASON_INTRO_1"
STAGE_SEASON_INTRO_2 = "SEASON_INTRO_2"
STAGE_SEASON_CONSENT = "SEASON_CONSENT"
STAGE_SEASON_MENU = "SEASON_MENU"


SEASON_STAGES = frozenset({
    STAGE_SEASON_INTRO_1,
    STAGE_SEASON_INTRO_2,
    STAGE_SEASON_CONSENT,
    STAGE_SEASON_MENU,
})


class ChatState(BaseModel):
    chat_id: int
    stage: str = STAGE_IDLE
    chat_username: str = ""

    pending_audio_file_id: str = ""
    pending_audio_filename: str = ""
    prepared_audio_local_path: str = ""
    lyrics_text: str = ""
    target_fragment: str = ""
    footage_genre_key: str = ""
    footage_artist_key: str = ""
    footage_artist_id: str = ""
    # Background mode: "footage" (default — pick footage stack via genre/artist)
    # or "solid" (skip footage selection, render solid color under text/audio).
    # Public bot UX is parity-mirrored from tg_bot_botapi but the entry point
    # for the bg-mode picker is intentionally NOT wired in user flow yet.
    bg_mode: str = "footage"
    # Solid background color key when bg_mode == "solid": "white" | "green".
    bg_solid_color: str = ""
    user_clip_start_sec: float = 0.0
    user_clip_end_sec: float = 0.0
    subtitles_mode: str = SUBTITLES_MODE_IMPULSE_2ND
    # Hook feature mirror (Phase A-UX). Defaults exactly match tg_bot_botapi
    # so a chat state copied across bots round-trips cleanly. Entry into the
    # hook handlers is gated by HOOK_FLOW_ENABLED in public app.py — disabled
    # by default until the test bot validates the flow on real users.
    hook_enabled: bool = False
    hook_drop_t: Optional[float] = None
    hook_type: str = "standard"
    # Hook category ("" | sound | object | effect | motion | thought) and the
    # F5 («Мысль») device ("" | punchline | missing_word | lyric_echo
    # | question_to_track | inverse_lyric). Mirror of tg_bot_botapi fields.
    hook_category: str = ""
    hook_device: str = ""
    # F3 «Эффект» selection when hook_category == "effect" (mirror of tg_bot_botapi).
    effect_hook: str = ""        # "" | hook_light | shutter_effect | flash_slow_shutter
    effect_transition: str = ""  # "" | snap_wipe | minimax | invert_flash | extract_flash | flash_on_cuts | layer_shake
    effect_extra: str = ""       # "" | xerox | analog_glitch | neon_extract | old_camera | pixel_grain | warm_map
    effect_hook_extend: str = "" # "" | to_end | after_drop:N
    hook_analysis_status: str = ""
    hook_analysis_audio_path: str = ""
    hook_analysis_clip_start: float = 0.0
    hook_analysis_clip_end: float = 0.0
    hook_drop_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    # Mirror of tg_bot_botapi: measured BPM from focus-clip analysis, used by
    # the F4 «Движение» reframe (lead_eff = lead * refBpm/bpm). 0.0 = none.
    hook_analysis_bpm: float = 0.0
    hook_analysis_error: str = ""
    versions_count: int = 1
    generation_run_id: str = ""
    # Batch metadata for sequential multi-version generation.
    batch_id: str = ""
    batch_audio_s3_url: str = ""
    batch_total_versions: int = 1
    next_version_to_enqueue: int = 1
    master_job_id: str = ""
    job_order: List[str] = Field(default_factory=list)
    used_footage_file_names: List[str] = Field(default_factory=list)

    # legacy single-job fields (kept for backward compatibility)
    active_job_id: str = ""
    # current multi-job fields
    active_job_ids: List[str] = Field(default_factory=list)
    completed_job_ids: List[str] = Field(default_factory=list)
    active_job_started_at: float = 0.0
    last_status_msg_at: float = 0.0
    status_message_id: int = 0
    last_status_text: str = ""
    last_backpressure_notice: str = ""
    poll_attempts: int = 0
    last_job_stage: str = ""
    last_job_error: str = ""

    # Sticky result source for fallback links if file send fails repeatedly.
    last_result_url: str = ""

    # Post-generation flow fields
    video_round: int = 1  # 1=first, 2=referral, 3=reminder
    last_rating: str = ""  # "low" / "mid" / "high"
    selected_package: str = ""  # "trial" / "blast" / "glow" / "impulse"
    referral_tag: str = ""
    referral_wait_started_at: float = 0.0
    reminder_at: float = 0.0

    # Season flow (Hooks S1) — mirrored from tg_bot_botapi.ChatState.
    # Populated only when SEASON_FLOW_ENABLED is on; defaults match botapi.
    season_intro_step: int = 0
    season_intro_completed: bool = False
    season_update_frequency: str = "finals_only"  # all | finals_only
    season_account_status: str = "new_free"       # new_free | exhausted_free | paid_active | paid_churned
    season_waitlist: bool = False
    season_referrer_tier: int = 0
    season_referrals_count: int = 0

    # Bigtest mode — parity with tg_bot_botapi. Gated by BIGTEST_ENABLED in
    # app.py (False here; True only on the team bot).
    bigtest_mode: bool = False
    bigtest_index: int = 0
    bigtest_total: int = 0
    bigtest_current_label: str = ""
    bigtest_master_job_id: str = ""
    bigtest_footage_seed: str = ""  # parity with tg_bot_botapi; unused here (BIGTEST_ENABLED=False)


class RedisChatStateStore:
    def __init__(self, settings: Settings):
        self._prefix = settings.tg_state_prefix.rstrip(":")
        self._username_index_prefix = f"{self._prefix}:username_index"
        self._chat_username_prefix = f"{self._prefix}:chat_username"

        self._all_ids_key = f"{self._prefix}:idx:all"
        self._processing_ids_key = f"{self._prefix}:idx:processing"
        # Legacy key kept for compatibility with older deployments.
        self._processing_set_key = f"{self._prefix}:__index:processing"
        self._waiting_referral_ids_key = f"{self._prefix}:idx:waiting_referral"
        self._reminder_zset_key = f"{self._prefix}:idx:reminder_at"
        self._updated_at_zset_key = f"{self._prefix}:idx:updated_at"
        self._stage_counts_key = f"{self._prefix}:idx:stage_counts"
        self._stage_by_chat_key = f"{self._prefix}:idx:stage_by_chat"
        self._processing_lock_prefix = f"{self._prefix}:locks:processing"

        self._state_ttl_s = max(3600, int(float(settings.tg_state_ttl_h) * 3600.0))

        self._redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            username=settings.redis_username or None,
            password=settings.redis_password or None,
            db=settings.redis_db,
            decode_responses=True,
        )

    @property
    def redis(self) -> Redis:
        """Shared Redis client — exposed for cross-module reuse (season phase store)."""
        return self._redis

    def _key(self, chat_id: int) -> str:
        return f"{self._prefix}:{int(chat_id)}"

    async def get_runtime_bool(self, key: str, *, default: bool = False) -> bool:
        redis_key = str(key or "").strip()
        if not redis_key:
            return bool(default)
        try:
            raw = await self._redis.get(redis_key)
        except Exception as e:
            log.warning("runtime_bool_read_failed key=%s err=%s", redis_key, str(e))
            return bool(default)
        return _parse_bool_text(str(raw or ""), default=default)

    def _username_key(self, username: str) -> str:
        normalized = _normalize_username(username).lstrip("@")
        return f"{self._username_index_prefix}:{normalized}"

    def _chat_username_key(self, chat_id: int) -> str:
        return f"{self._chat_username_prefix}:{int(chat_id)}"

    @staticmethod
    def _compact_raw_state(raw: str, *, limit: int = 200) -> str:
        txt = str(raw or "").strip().replace("\n", " ")
        if len(txt) <= limit:
            return txt
        return txt[: limit - 3] + "..."

    @staticmethod
    def _parse_chat_id_token(token: str) -> int | None:
        try:
            return int(str(token).strip())
        except Exception:
            return None

    @staticmethod
    def _raise_corrupted_state(*, chat_id: int, reason: str, raw: str, err: Exception | None = None) -> None:
        payload = RedisChatStateStore._compact_raw_state(raw)
        if err is None:
            log.error("chat_state_corrupted chat_id=%s reason=%s raw=%s", chat_id, reason, payload)
        else:
            log.error(
                "chat_state_corrupted chat_id=%s reason=%s err=%s raw=%s",
                chat_id,
                reason,
                str(err),
                payload,
            )
        raise RuntimeError(f"Corrupted chat state for chat_id={int(chat_id)}: {reason}")

    def _parse_state_or_none(self, *, key: str, raw: str) -> ChatState | None:
        try:
            obj = json.loads(raw)
        except Exception as e:
            log.error(
                "chat_state_scan_parse_failed key=%s reason=json err=%s raw=%s",
                key,
                str(e),
                self._compact_raw_state(raw),
            )
            return None
        try:
            return ChatState.model_validate(obj)
        except Exception as e:
            log.error(
                "chat_state_scan_parse_failed key=%s reason=validation err=%s raw=%s",
                key,
                str(e),
                self._compact_raw_state(raw),
            )
            return None

    async def _adjust_stage_count(self, stage: str, delta: int) -> None:
        stage_name = str(stage or "").strip()
        if not stage_name or delta == 0:
            return
        new_val = await self._redis.hincrby(self._stage_counts_key, stage_name, int(delta))
        if int(new_val) <= 0:
            await self._redis.hdel(self._stage_counts_key, stage_name)

    async def _sync_stage_counters(self, *, chat_id: int, new_stage: str) -> None:
        chat_token = str(int(chat_id))
        old_stage = str(await self._redis.hget(self._stage_by_chat_key, chat_token) or "").strip()
        next_stage = str(new_stage or "").strip()
        if old_stage == next_stage:
            return
        if old_stage:
            await self._adjust_stage_count(old_stage, -1)
        if next_stage:
            await self._adjust_stage_count(next_stage, 1)
            await self._redis.hset(self._stage_by_chat_key, chat_token, next_stage)
        else:
            await self._redis.hdel(self._stage_by_chat_key, chat_token)

    async def _sync_indexes_for_state(self, state: ChatState) -> None:
        chat_id = int(state.chat_id)
        chat_token = str(chat_id)
        stage = str(state.stage or "").strip()

        await self._redis.sadd(self._all_ids_key, chat_token)
        await self._redis.zadd(self._updated_at_zset_key, {chat_token: float(time.time())})

        if stage == STAGE_PROCESSING:
            await self._redis.sadd(self._processing_ids_key, chat_token)
            await self._redis.sadd(self._processing_set_key, chat_token)
        else:
            await self._redis.srem(self._processing_ids_key, chat_token)
            await self._redis.srem(self._processing_set_key, chat_token)

        if stage == STAGE_WAITING_REFERRAL:
            await self._redis.sadd(self._waiting_referral_ids_key, chat_token)
        else:
            await self._redis.srem(self._waiting_referral_ids_key, chat_token)

        if stage == STAGE_KEEP_IN_TOUCH and float(state.reminder_at or 0.0) > 0.0:
            await self._redis.zadd(self._reminder_zset_key, {chat_token: float(state.reminder_at)})
        else:
            await self._redis.zrem(self._reminder_zset_key, chat_token)

    async def _purge_indexes_only(self, *, chat_id: int, stage_hint: str = "") -> None:
        chat_token = str(int(chat_id))

        await self._redis.srem(self._all_ids_key, chat_token)
        await self._redis.srem(self._processing_ids_key, chat_token)
        await self._redis.srem(self._processing_set_key, chat_token)
        await self._redis.srem(self._waiting_referral_ids_key, chat_token)
        await self._redis.zrem(self._reminder_zset_key, chat_token)
        await self._redis.zrem(self._updated_at_zset_key, chat_token)

        old_stage = str(stage_hint or await self._redis.hget(self._stage_by_chat_key, chat_token) or "").strip()
        if old_stage:
            await self._adjust_stage_count(old_stage, -1)
        await self._redis.hdel(self._stage_by_chat_key, chat_token)

    async def _load_state_from_key(self, *, chat_id: int, strict: bool) -> ChatState | None:
        key = self._key(chat_id)
        raw = await self._redis.get(key)
        if not raw:
            return None

        if strict:
            try:
                obj = json.loads(raw)
            except Exception as e:
                self._raise_corrupted_state(chat_id=int(chat_id), reason="json", raw=raw, err=e)
            try:
                return ChatState.model_validate(obj)
            except Exception as e:
                self._raise_corrupted_state(chat_id=int(chat_id), reason="validation", raw=raw, err=e)

        parsed = self._parse_state_or_none(key=key, raw=raw)
        if parsed is None:
            await self.delete_state(int(chat_id))
            return None
        return parsed

    async def _list_states_by_chat_ids(self, chat_ids: Iterable[int]) -> List[ChatState]:
        out: List[ChatState] = []
        for chat_id in chat_ids:
            st = await self._load_state_from_key(chat_id=int(chat_id), strict=False)
            if st is None:
                continue
            out.append(st)
        return out

    async def _scan_set_chat_ids(self, index_key: str, *, limit: int) -> List[int]:
        out: List[int] = []
        cursor = 0
        remaining = max(1, int(limit))
        while True:
            cursor, members = await self._redis.sscan(index_key, cursor=cursor, count=min(200, remaining))
            for token in members or []:
                cid = self._parse_chat_id_token(token)
                if cid is None:
                    continue
                out.append(cid)
                remaining -= 1
                if remaining <= 0:
                    return out
            if int(cursor) == 0:
                return out

    async def get(self, chat_id: int) -> ChatState:
        st = await self._load_state_from_key(chat_id=int(chat_id), strict=True)
        if st is None:
            return ChatState(chat_id=int(chat_id))
        return st

    async def set(self, state: ChatState) -> None:
        chat_id = int(state.chat_id)
        key = self._key(chat_id)
        state_raw = state.model_dump_json()
        new_username = _normalize_username(state.chat_username)
        old_username = _normalize_username(await self._redis.get(self._chat_username_key(chat_id)))

        await self._redis.set(key, state_raw, ex=self._state_ttl_s)
        await self._sync_indexes_for_state(state)
        await self._sync_stage_counters(chat_id=chat_id, new_stage=str(state.stage or ""))

        if old_username and old_username != new_username:
            old_map_key = self._username_key(old_username)
            old_owner = await self._redis.get(old_map_key)
            if str(old_owner or "").strip() == str(chat_id):
                await self._redis.delete(old_map_key)

        if new_username:
            await self._redis.set(self._username_key(new_username), str(chat_id))
            await self._redis.set(self._chat_username_key(chat_id), new_username, ex=self._state_ttl_s)
        else:
            await self._redis.delete(self._chat_username_key(chat_id))

    async def delete_state(self, chat_id: int) -> None:
        cid = int(chat_id)
        old_username = _normalize_username(await self._redis.get(self._chat_username_key(cid)))
        old_stage = str(await self._redis.hget(self._stage_by_chat_key, str(cid)) or "")

        await self._purge_indexes_only(chat_id=cid, stage_hint=old_stage)
        await self._redis.delete(self._key(cid), self._chat_username_key(cid))

        if old_username:
            username_key = self._username_key(old_username)
            old_owner = await self._redis.get(username_key)
            if str(old_owner or "").strip() == str(cid):
                await self._redis.delete(username_key)

    async def reset_to_wait_audio(self, chat_id: int) -> ChatState:
        existing = await self.get(chat_id)
        existing.stage = STAGE_WAIT_AUDIO
        # Clear generation-specific fields but keep user context
        existing.prepared_audio_local_path = ""
        existing.active_job_id = ""
        existing.active_job_ids = []
        existing.job_order = []
        existing.completed_job_ids = []
        existing.batch_id = ""
        existing.batch_audio_s3_url = ""
        existing.batch_total_versions = 1
        existing.next_version_to_enqueue = 1
        existing.master_job_id = ""
        existing.used_footage_file_names = []
        existing.active_job_started_at = 0.0
        existing.last_status_text = ""
        existing.status_message_id = 0
        existing.last_status_msg_at = 0.0
        existing.poll_attempts = 0
        existing.last_job_stage = ""
        existing.last_job_error = ""
        existing.last_result_url = ""
        existing.target_fragment = ""
        existing.footage_genre_key = ""
        existing.footage_artist_key = ""
        existing.footage_artist_id = ""
        existing.user_clip_start_sec = 0.0
        existing.user_clip_end_sec = 0.0
        existing.subtitles_mode = ""
        existing.versions_count = 1
        existing.referral_tag = ""
        existing.referral_wait_started_at = 0.0
        await self.set(existing)
        return existing

    async def set_stage(self, chat_id: int, stage: str) -> ChatState:
        st = await self.get(chat_id)
        st.stage = str(stage)
        await self.set(st)
        return st

    async def list_processing_candidates(self) -> List[ChatState]:
        chat_ids: List[int] = []
        members = set(await self._redis.smembers(self._processing_ids_key) or set())
        members.update(await self._redis.smembers(self._processing_set_key) or set())
        for token in members:
            cid = self._parse_chat_id_token(token)
            if cid is not None:
                chat_ids.append(cid)
        return await self._list_states_by_chat_ids(chat_ids)

    async def list_processing(self) -> List[ChatState]:
        out: List[ChatState] = []
        for st in await self.list_processing_candidates():
            has_jobs = bool(st.active_job_ids) or bool(st.active_job_id)
            if st.stage == STAGE_PROCESSING and has_jobs:
                out.append(st)
        return out

    def _processing_lock_key(self, chat_id: int) -> str:
        return f"{self._processing_lock_prefix}:{int(chat_id)}"

    async def acquire_processing_lock(self, *, chat_id: int, owner_id: str, ttl_s: int) -> bool:
        owner = str(owner_id or "").strip()
        if not owner:
            raise RuntimeError("owner_id is required for processing lock")
        ttl = max(5, int(ttl_s))
        key = self._processing_lock_key(int(chat_id))
        created = await self._redis.set(key, owner, ex=ttl, nx=True)
        return bool(created)

    async def refresh_processing_lock(self, *, chat_id: int, owner_id: str, ttl_s: int) -> bool:
        owner = str(owner_id or "").strip()
        if not owner:
            return False
        key = self._processing_lock_key(int(chat_id))
        current = str(await self._redis.get(key) or "").strip()
        if current != owner:
            return False
        ttl = max(5, int(ttl_s))
        await self._redis.expire(key, ttl)
        return True

    async def release_processing_lock(self, *, chat_id: int, owner_id: str) -> bool:
        owner = str(owner_id or "").strip()
        if not owner:
            return False
        key = self._processing_lock_key(int(chat_id))
        current = str(await self._redis.get(key) or "").strip()
        if current != owner:
            return False
        await self._redis.delete(key)
        return True

    async def list_waiting_referral(self) -> List[ChatState]:
        chat_ids: List[int] = []
        for token in (await self._redis.smembers(self._waiting_referral_ids_key) or set()):
            cid = self._parse_chat_id_token(token)
            if cid is not None:
                chat_ids.append(cid)
        out: List[ChatState] = []
        for st in await self._list_states_by_chat_ids(chat_ids):
            if st.stage == STAGE_WAITING_REFERRAL:
                out.append(st)
        return out

    async def list_all_states(self) -> List[ChatState]:
        chat_ids: List[int] = []
        for token in (await self._redis.smembers(self._all_ids_key) or set()):
            cid = self._parse_chat_id_token(token)
            if cid is not None:
                chat_ids.append(cid)
        return await self._list_states_by_chat_ids(chat_ids)

    async def list_stage_counts(self) -> dict[str, int]:
        raw = await self._redis.hgetall(self._stage_counts_key)
        out: dict[str, int] = {}
        for stage, cnt in (raw or {}).items():
            try:
                val = int(cnt)
            except Exception:
                continue
            if val > 0:
                out[str(stage)] = val
        return out

    async def get_stages_for_chat_ids(self, chat_ids: List[int]) -> dict[int, str]:
        if not chat_ids:
            return {}
        fields = [str(int(cid)) for cid in chat_ids]
        values = await self._redis.hmget(self._stage_by_chat_key, fields)
        out: dict[int, str] = {}
        for field, stage in zip(fields, values or []):
            cid = self._parse_chat_id_token(field)
            if cid is None:
                continue
            stage_name = str(stage or "").strip()
            if not stage_name:
                continue
            out[cid] = stage_name
        return out

    async def find_chat_id_by_username(self, username: str) -> int | None:
        normalized = _normalize_username(username)
        if not normalized:
            return None
        val = await self._redis.get(self._username_key(normalized))
        if not val:
            return None
        try:
            return int(val)
        except Exception:
            log.error(
                "username_index_corrupted username=%s value=%s",
                normalized,
                str(val),
            )
            return None

    async def list_stale_chat_ids(self, older_than_ts: float, *, limit: int) -> List[int]:
        tokens = await self._redis.zrangebyscore(
            self._updated_at_zset_key,
            min=float("-inf"),
            max=float(older_than_ts),
            start=0,
            num=max(1, int(limit)),
        )
        out: List[int] = []
        for token in tokens or []:
            cid = self._parse_chat_id_token(token)
            if cid is not None:
                out.append(cid)
        return out

    async def cleanup_index_members(self, *, limit: int = 500) -> int:
        to_check: List[int] = []
        max_items = max(1, int(limit))

        for key in (self._all_ids_key, self._processing_ids_key, self._waiting_referral_ids_key):
            if len(to_check) >= max_items:
                break
            want = max_items - len(to_check)
            to_check.extend(await self._scan_set_chat_ids(key, limit=want))

        if len(to_check) < max_items:
            remainder = max_items - len(to_check)
            for token in await self._redis.zrange(self._updated_at_zset_key, 0, remainder - 1):
                cid = self._parse_chat_id_token(token)
                if cid is not None:
                    to_check.append(cid)
            if len(to_check) < max_items:
                remainder = max_items - len(to_check)
                for token in await self._redis.zrange(self._reminder_zset_key, 0, remainder - 1):
                    cid = self._parse_chat_id_token(token)
                    if cid is not None:
                        to_check.append(cid)

        seen: set[int] = set()
        removed = 0
        for cid in to_check:
            if cid in seen:
                continue
            seen.add(cid)
            raw = await self._redis.get(self._key(cid))
            if raw:
                continue
            await self.delete_state(cid)
            removed += 1
        return removed

    # --- Referral helpers ---
    async def set_referral(self, referred_username: str, referrer_chat_id: int) -> None:
        key = f"{_REFERRAL_PREFIX}:{_normalize_username(referred_username)}"
        await self._redis.set(key, str(referrer_chat_id), ex=_REFERRAL_TTL_S)

    async def get_referral(self, referred_username: str) -> int | None:
        key = f"{_REFERRAL_PREFIX}:{_normalize_username(referred_username)}"
        val = await self._redis.get(key)
        if val:
            try:
                return int(val)
            except Exception:
                return None
        return None

    async def delete_referral(self, referred_username: str) -> None:
        key = f"{_REFERRAL_PREFIX}:{_normalize_username(referred_username)}"
        await self._redis.delete(key)

    # --- Reminder scan ---
    async def list_pending_reminders(self, now: float, *, limit: int = 500) -> List[ChatState]:
        out: List[ChatState] = []
        tokens = await self._redis.zrangebyscore(
            self._reminder_zset_key,
            min=float("-inf"),
            max=float(now),
            start=0,
            num=max(1, int(limit)),
        )
        chat_ids: List[int] = []
        for token in tokens or []:
            cid = self._parse_chat_id_token(token)
            if cid is not None:
                chat_ids.append(cid)

        for st in await self._list_states_by_chat_ids(chat_ids):
            if st.stage == STAGE_KEEP_IN_TOUCH and float(st.reminder_at or 0.0) > 0 and float(st.reminder_at) <= float(now):
                out.append(st)
            else:
                await self._redis.zrem(self._reminder_zset_key, str(int(st.chat_id)))
        return out

    async def set_runtime_bool(self, key: str, value: bool, *, ttl_s: int = 0) -> None:
        runtime_key = str(key or "").strip()
        if not runtime_key:
            return
        payload = "1" if bool(value) else "0"
        ttl = int(ttl_s or 0)
        if ttl > 0:
            await self._redis.set(runtime_key, payload, ex=ttl)
        else:
            await self._redis.set(runtime_key, payload)

    async def get_runtime_bool(self, key: str, *, default: bool = False) -> bool:
        runtime_key = str(key or "").strip()
        if not runtime_key:
            return bool(default)
        raw = str(await self._redis.get(runtime_key) or "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    async def mark_webhook_update_seen(self, *, update_id: int, ttl_s: int) -> bool:
        uid = int(update_id)
        if uid <= 0:
            return True
        ttl = max(1, int(ttl_s))
        key = f"{self._prefix}:webhook:update:{uid}"
        created = await self._redis.set(key, "1", ex=ttl, nx=True)
        return bool(created)

    # --- Per-user footage rotation helpers ---
    # Cursor and history are keyed by (chat_id, artist_id). History persists
    # across audio uploads and batch boundaries, which is why it lives outside
    # of ChatState.used_footage_file_names (that field is intra-batch only).
    def _rotation_cursor_key(self, chat_id: int, artist_id: str) -> str:
        aid = str(artist_id or "").strip() or "_unknown_"
        return f"{self._prefix}:rotation:cursor:{int(chat_id)}:{aid}"

    def _rotation_history_key(self, chat_id: int, artist_id: str) -> str:
        aid = str(artist_id or "").strip() or "_unknown_"
        return f"{self._prefix}:rotation:history:{int(chat_id)}:{aid}"

    async def get_rotation_cursor(self, chat_id: int, artist_id: str) -> int:
        raw = await self._redis.get(self._rotation_cursor_key(int(chat_id), artist_id))
        if not raw:
            return 0
        try:
            return int(raw)
        except Exception:
            return 0

    async def set_rotation_cursor(self, chat_id: int, artist_id: str, value: int) -> None:
        key = self._rotation_cursor_key(int(chat_id), artist_id)
        await self._redis.set(key, str(int(value)), ex=_ROTATION_TTL_S)

    async def advance_rotation_cursor(self, chat_id: int, artist_id: str) -> tuple[int, int]:
        """Increment the cursor by 1 and return (old_value, new_value)."""
        key = self._rotation_cursor_key(int(chat_id), artist_id)
        new_val = int(await self._redis.incr(key))
        await self._redis.expire(key, _ROTATION_TTL_S)
        return new_val - 1, new_val

    async def get_rotation_history(self, chat_id: int, artist_id: str) -> List[str]:
        key = self._rotation_history_key(int(chat_id), artist_id)
        items = await self._redis.lrange(key, 0, _ROTATION_HISTORY_MAX - 1)
        out: List[str] = []
        seen: set[str] = set()
        for it in items or []:
            name = str(it or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
        return out

    async def add_rotation_history(
        self,
        chat_id: int,
        artist_id: str,
        file_names: List[str],
    ) -> None:
        clean = [str(n).strip() for n in (file_names or []) if str(n).strip()]
        if not clean:
            return
        key = self._rotation_history_key(int(chat_id), artist_id)
        # LPUSH newest first, then cap to _ROTATION_HISTORY_MAX.
        await self._redis.lpush(key, *clean)
        await self._redis.ltrim(key, 0, _ROTATION_HISTORY_MAX - 1)
        await self._redis.expire(key, _ROTATION_TTL_S)

    async def close(self) -> None:
        await self._redis.aclose()
