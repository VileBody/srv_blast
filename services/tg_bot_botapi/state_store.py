from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS
from .config import Settings

log = logging.getLogger("tg_bot.state_store")

log = logging.getLogger(__name__)

STAGE_IDLE = "IDLE"
STAGE_WAIT_AUDIO = "WAIT_AUDIO"
STAGE_WAIT_LYRICS_CHOICE = "WAIT_LYRICS_CHOICE"
STAGE_WAIT_LYRICS_TEXT = "WAIT_LYRICS_TEXT"
STAGE_WAIT_FRAGMENT_CHOICE = "WAIT_FRAGMENT_CHOICE"
STAGE_WAIT_FRAGMENT_TEXT = "WAIT_FRAGMENT_TEXT"
STAGE_WAIT_SUBTITLES_MODE = "WAIT_SUBTITLES_MODE"
STAGE_WAIT_VERSIONS = "WAIT_VERSIONS"
STAGE_WAIT_CONFIRM = "WAIT_CONFIRM"
STAGE_PROCESSING = "PROCESSING"
STAGE_WAIT_NEXT = "WAIT_NEXT"
# User waiting for a referral friend to activate their first video.
STAGE_WAITING_REFERRAL = "WAITING_REFERRAL"
# User account exists but has no credits (not yet paid).
STAGE_LOCKED = "LOCKED"


class ChatState(BaseModel):
    chat_id: int
    stage: str = STAGE_IDLE
    chat_username: str = ""

    pending_audio_file_id: str = ""
    pending_audio_filename: str = ""
    prepared_audio_local_path: str = ""
    lyrics_text: str = ""
    target_fragment: str = ""
    subtitles_mode: str = SUBTITLES_MODE_LEGACY_BLOCKS
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

    # Timestamp for state TTL / recovery
    updated_at: float = 0.0

    # Credit reservation: ref_id of the deduction held while enqueue is in-flight.
    # Non-empty means a credit was deducted and not yet confirmed as consumed.
    pending_deduction_ref_id: str = ""

    # Referral: chat_id of the user who referred this user (0 = none).
    referrer_chat_id: int = 0
    # Timestamp when we entered WAITING_REFERRAL so recovery can unstick us.
    waiting_referral_since: float = 0.0


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
        # Secondary index: set of chat_ids currently in PROCESSING stage.
        self._processing_set_key = f"{self._prefix}:__index:processing"

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
                "chat_state_json_parse_error chat_id=%s err=%r raw_head=%s — resetting to blank state",
                chat_id, e, repr(raw[:200]) if raw else "",
            )
            return ChatState(chat_id=int(chat_id))

        try:
            return ChatState.model_validate(obj)
        except Exception as e:
            log.error(
                "chat_state_validation_error chat_id=%s err=%r keys=%s — resetting to blank state",
                chat_id, e, list(obj.keys()) if isinstance(obj, dict) else type(obj).__name__,
            )
            return ChatState(chat_id=int(chat_id))

    async def set(self, state: ChatState) -> None:
        state.updated_at = time.time()
        await self._redis.set(self._key(state.chat_id), state.model_dump_json())
        # Maintain processing index
        await self._update_processing_index(state)

    async def _update_processing_index(self, state: ChatState) -> None:
        """Keep the processing set in sync with state transitions."""
        member = str(state.chat_id)
        has_jobs = bool(state.active_job_ids) or bool(state.active_job_id)
        if state.stage == STAGE_PROCESSING and has_jobs:
            await self._redis.sadd(self._processing_set_key, member)
        else:
            await self._redis.srem(self._processing_set_key, member)

    async def reset_to_wait_audio(self, chat_id: int) -> ChatState:
        st = ChatState(chat_id=int(chat_id), stage=STAGE_WAIT_AUDIO)
        await self.set(st)
        return st

    async def set_stage(self, chat_id: int, stage: str) -> ChatState:
        st = await self.get(chat_id)
        st.stage = str(stage)
        await self.set(st)
        return st

    async def list_processing(self) -> List[ChatState]:
        """
        O(k) where k = number of PROCESSING chats (typically small),
        instead of O(n) full SCAN of all chat states.
        """
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
                # Stale index entry — state no longer PROCESSING
                stale.append(str(member))

        if stale:
            await self._redis.srem(self._processing_set_key, *stale)
        return out

    async def list_processing_stuck(self, *, max_age_s: float = 7200.0) -> List[ChatState]:
        """
        Find PROCESSING chats that have been stuck for longer than max_age_s.
        Used for recovery policy.
        """
        all_processing = await self.list_processing()
        now = time.time()
        stuck: List[ChatState] = []
        for st in all_processing:
            started = st.active_job_started_at or st.updated_at or 0.0
            if started > 0 and (now - started) > max_age_s:
                stuck.append(st)
        return stuck

    async def cleanup_stale_states(self, *, max_idle_age_s: float = 604800.0) -> int:
        """
        Bounded retention: remove chat states that have been idle (not PROCESSING)
        for longer than max_idle_age_s (default 7 days).

        This is a SCAN-based operation — call sparingly (e.g., once per hour).
        Returns count of removed states.
        """
        pattern = f"{self._prefix}:*"
        removed = 0
        now = time.time()
        async for key in self._redis.scan_iter(match=pattern, count=200):
            # Skip the index key
            if key == self._processing_set_key:
                continue
            raw = await self._redis.get(key)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                st = ChatState.model_validate(obj)
            except Exception as exc:
                log.error(
                    "state_store.list_processing: failed to parse key=%s err=%r — skipping",
                    key,
                    exc,
                )
                continue
            # Don't remove active states
            if st.stage == STAGE_PROCESSING:
                continue
            updated = st.updated_at or 0.0
            if updated > 0 and (now - updated) > max_idle_age_s:
                await self._redis.delete(key)
                removed += 1
                log.info("stale_state_removed chat_id=%s stage=%s age_days=%.1f",
                         st.chat_id, st.stage, (now - updated) / 86400)
        return removed

    async def list_waiting_referral(self) -> List[ChatState]:
        """Return all chats stuck in WAITING_REFERRAL for recovery."""
        out: List[ChatState] = []
        pattern = f"{self._prefix}:*"
        async for key in self._redis.scan_iter(match=pattern, count=200):
            raw = await self._redis.get(key)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                st = ChatState.model_validate(obj)
            except Exception as exc:
                log.error(
                    "state_store.list_waiting_referral: failed to parse key=%s err=%r — skipping",
                    key,
                    exc,
                )
                continue
            if st.stage == STAGE_WAITING_REFERRAL:
                out.append(st)
        return out

    async def close(self) -> None:
        await self._redis.aclose()
