from __future__ import annotations

import json
import logging
import time
from typing import List

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from core.subtitles_mode import SUBTITLES_MODE_IMPULSE_2ND
from .config import Settings


log = logging.getLogger(__name__)


STAGE_IDLE = "IDLE"
STAGE_WAIT_START = "WAIT_START"
STAGE_WAIT_SUBSCRIPTION = "WAIT_SUBSCRIPTION"
STAGE_WAIT_AUDIO = "WAIT_AUDIO"
STAGE_WAIT_LYRICS_CHOICE = "WAIT_LYRICS_CHOICE"
STAGE_WAIT_LYRICS_TEXT = "WAIT_LYRICS_TEXT"
STAGE_WAIT_FRAGMENT_CHOICE = "WAIT_FRAGMENT_CHOICE"
STAGE_WAIT_FRAGMENT_TEXT = "WAIT_FRAGMENT_TEXT"
STAGE_WAIT_CONFIRM_TEXT = "WAIT_CONFIRM_TEXT"
STAGE_WAIT_SUBTITLES_MODE = "WAIT_SUBTITLES_MODE"
STAGE_WAIT_CONFIRM_MODE = "WAIT_CONFIRM_MODE"
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


class ChatState(BaseModel):
    chat_id: int
    stage: str = STAGE_IDLE
    chat_username: str = ""

    pending_audio_file_id: str = ""
    pending_audio_filename: str = ""
    prepared_audio_local_path: str = ""
    lyrics_text: str = ""
    target_fragment: str = ""
    subtitles_mode: str = SUBTITLES_MODE_IMPULSE_2ND
    versions_count: int = 1
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
    reminder_at: float = 0.0

    # Timestamp for state TTL / recovery
    updated_at: float = 0.0


class RedisChatStateStore:
    def __init__(self, settings: Settings):
        self._prefix = settings.tg_state_prefix.rstrip(":")
        self._redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            username=settings.redis_username or None,
            password=settings.redis_password or None,
            db=settings.redis_db,
            decode_responses=True,
        )
        # Secondary indexes: sets of chat_ids for O(k) lookups.
        self._processing_set_key = f"{self._prefix}:__index:processing"
        self._reminders_set_key = f"{self._prefix}:__index:reminders"

    def _key(self, chat_id: int) -> str:
        return f"{self._prefix}:{int(chat_id)}"

    async def get(self, chat_id: int) -> ChatState:
        raw = await self._redis.get(self._key(chat_id))
        if not raw:
            return ChatState(chat_id=int(chat_id))
        try:
            obj = json.loads(raw)
        except Exception as e:
            log.error(
                "chat_state_json_parse_error chat_id=%s err=%r raw_head=%s",
                chat_id, e, repr(raw[:200]) if raw else "",
            )
            return ChatState(chat_id=int(chat_id))

        try:
            return ChatState.model_validate(obj)
        except Exception as e:
            log.error(
                "chat_state_validation_error chat_id=%s err=%r keys=%s",
                chat_id, e, list(obj.keys()) if isinstance(obj, dict) else type(obj).__name__,
            )
            return ChatState(chat_id=int(chat_id))

    async def set(self, state: ChatState) -> None:
        state.updated_at = time.time()
        await self._redis.set(self._key(state.chat_id), state.model_dump_json())
        # Maintain secondary indexes
        await self._update_indexes(state)

    async def _update_indexes(self, state: ChatState) -> None:
        """Keep processing and reminders sets in sync with state transitions."""
        member = str(state.chat_id)
        # Processing index
        has_jobs = bool(state.active_job_ids) or bool(state.active_job_id)
        if state.stage == STAGE_PROCESSING and has_jobs:
            await self._redis.sadd(self._processing_set_key, member)
        else:
            await self._redis.srem(self._processing_set_key, member)
        # Reminders index
        if state.stage == STAGE_KEEP_IN_TOUCH and state.reminder_at > 0:
            await self._redis.sadd(self._reminders_set_key, member)
        else:
            await self._redis.srem(self._reminders_set_key, member)

    async def reset_to_wait_audio(self, chat_id: int) -> ChatState:
        existing = await self.get(chat_id)
        existing.stage = STAGE_WAIT_AUDIO
        # Clear generation-specific fields but keep user context
        existing.prepared_audio_local_path = ""
        existing.active_job_id = ""
        existing.job_order = []
        existing.completed_job_ids = []
        existing.next_version_to_enqueue = 0
        existing.last_status_text = ""
        existing.status_message_id = 0
        existing.last_status_msg_at = 0.0
        existing.target_fragment = ""
        existing.subtitles_mode = ""
        existing.versions_count = 1
        await self.set(existing)
        return existing

    async def set_stage(self, chat_id: int, stage: str) -> ChatState:
        st = await self.get(chat_id)
        st.stage = str(stage)
        await self.set(st)
        return st

    async def list_processing(self) -> List[ChatState]:
        """O(k) where k = number of PROCESSING chats (uses SET index)."""
        members = await self._redis.smembers(self._processing_set_key)
        if not members:
            return []

        out: List[ChatState] = []
        stale: List[str] = []
        for member in members:
            try:
                chat_id = int(member)
            except (ValueError, TypeError):
                stale.append(str(member))
                continue
            st = await self.get(chat_id)
            has_jobs = bool(st.active_job_ids) or bool(st.active_job_id)
            if st.stage == STAGE_PROCESSING and has_jobs:
                out.append(st)
            else:
                stale.append(str(member))

        if stale:
            await self._redis.srem(self._processing_set_key, *stale)
        return out

    async def list_all_states(self) -> List[ChatState]:
        out: List[ChatState] = []
        pattern = f"{self._prefix}:*"
        async for key in self._redis.scan_iter(match=pattern, count=200):
            # Skip index keys
            if ":__index:" in str(key):
                continue
            raw = await self._redis.get(key)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                st = ChatState.model_validate(obj)
            except Exception:
                continue
            out.append(st)
        return out

    # --- Referral helpers ---
    async def set_referral(self, referred_username: str, referrer_chat_id: int) -> None:
        key = f"{_REFERRAL_PREFIX}:{referred_username.lower()}"
        await self._redis.set(key, str(referrer_chat_id), ex=_REFERRAL_TTL_S)

    async def get_referral(self, referred_username: str) -> int | None:
        key = f"{_REFERRAL_PREFIX}:{referred_username.lower()}"
        val = await self._redis.get(key)
        if val:
            try:
                return int(val)
            except Exception:
                return None
        return None

    async def delete_referral(self, referred_username: str) -> None:
        key = f"{_REFERRAL_PREFIX}:{referred_username.lower()}"
        await self._redis.delete(key)

    # --- Reminder scan (SET-indexed) ---
    async def list_pending_reminders(self, now: float) -> List[ChatState]:
        """O(k) where k = number of chats with reminders (uses SET index)."""
        members = await self._redis.smembers(self._reminders_set_key)
        if not members:
            return []

        out: List[ChatState] = []
        stale: List[str] = []
        for member in members:
            try:
                chat_id = int(member)
            except (ValueError, TypeError):
                stale.append(str(member))
                continue
            st = await self.get(chat_id)
            if st.stage == STAGE_KEEP_IN_TOUCH and st.reminder_at > 0 and st.reminder_at <= now:
                out.append(st)
            elif st.stage != STAGE_KEEP_IN_TOUCH or st.reminder_at <= 0:
                # No longer a reminder candidate
                stale.append(str(member))

        if stale:
            await self._redis.srem(self._reminders_set_key, *stale)
        return out

    async def list_processing_stuck(self, *, max_age_s: float = 7200.0) -> List[ChatState]:
        """Find PROCESSING chats stuck for longer than max_age_s."""
        all_processing = await self.list_processing()
        now = time.time()
        stuck: List[ChatState] = []
        for st in all_processing:
            started = st.active_job_started_at or st.updated_at or 0.0
            if started > 0 and (now - started) > max_age_s:
                stuck.append(st)
        return stuck

    async def cleanup_stale_states(self, *, max_idle_age_s: float = 604800.0) -> int:
        """Remove chat states idle for longer than max_idle_age_s (default 7 days). SCAN-based — call sparingly."""
        pattern = f"{self._prefix}:*"
        removed = 0
        now = time.time()
        async for key in self._redis.scan_iter(match=pattern, count=200):
            if ":__index:" in str(key):
                continue
            raw = await self._redis.get(key)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                st = ChatState.model_validate(obj)
            except Exception:
                continue
            if st.stage == STAGE_PROCESSING:
                continue
            updated = st.updated_at or 0.0
            if updated > 0 and (now - updated) > max_idle_age_s:
                await self._redis.delete(key)
                removed += 1
                log.info("stale_state_removed chat_id=%s stage=%s age_days=%.1f",
                         st.chat_id, st.stage, (now - updated) / 86400)
        return removed

    async def close(self) -> None:
        await self._redis.aclose()
