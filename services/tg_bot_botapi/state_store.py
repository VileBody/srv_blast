from __future__ import annotations

import json
import logging
from typing import List

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS
from .config import Settings

log = logging.getLogger("tg_bot.state_store")

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

    def _key(self, chat_id: int) -> str:
        return f"{self._prefix}:{int(chat_id)}"

    async def get(self, chat_id: int) -> ChatState:
        raw = await self._redis.get(self._key(chat_id))
        if not raw:
            return ChatState(chat_id=int(chat_id))
        try:
            obj = json.loads(raw)
        except Exception as exc:
            log.error(
                "state_store.get: JSON parse failed chat_id=%s err=%r raw_prefix=%r — resetting to blank state",
                chat_id,
                exc,
                raw[:200] if raw else "",
            )
            return ChatState(chat_id=int(chat_id))

        try:
            return ChatState.model_validate(obj)
        except Exception as exc:
            log.error(
                "state_store.get: Pydantic validation failed chat_id=%s err=%r — resetting to blank state",
                chat_id,
                exc,
            )
            return ChatState(chat_id=int(chat_id))

    async def set(self, state: ChatState) -> None:
        await self._redis.set(self._key(state.chat_id), state.model_dump_json())

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
                    "state_store.list_processing: failed to parse key=%s err=%r — skipping",
                    key,
                    exc,
                )
                continue
            has_jobs = bool(st.active_job_ids) or bool(st.active_job_id)
            if st.stage == STAGE_PROCESSING and has_jobs:
                out.append(st)
        return out

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
