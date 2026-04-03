from __future__ import annotations

import json
import logging
import time
from typing import List

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS
from .config import Settings

log = logging.getLogger("tg_bot_botapi.state_store")


STAGE_IDLE = "IDLE"
STAGE_WAIT_AUDIO = "WAIT_AUDIO"
STAGE_WAIT_LYRICS_CHOICE = "WAIT_LYRICS_CHOICE"
STAGE_WAIT_LYRICS_TEXT = "WAIT_LYRICS_TEXT"
STAGE_WAIT_FRAGMENT_CHOICE = "WAIT_FRAGMENT_CHOICE"
STAGE_WAIT_FRAGMENT_TEXT = "WAIT_FRAGMENT_TEXT"
STAGE_WAIT_FOOTAGE_GENRE = "WAIT_FOOTAGE_GENRE"
STAGE_WAIT_FOOTAGE_ARTIST = "WAIT_FOOTAGE_ARTIST"
STAGE_WAIT_TIMING_CHOICE = "WAIT_TIMING_CHOICE"
STAGE_WAIT_TIMING_INPUT = "WAIT_TIMING_INPUT"
STAGE_WAIT_SUBTITLES_MODE = "WAIT_SUBTITLES_MODE"
STAGE_WAIT_VERSIONS = "WAIT_VERSIONS"
STAGE_WAIT_CONFIRM = "WAIT_CONFIRM"
STAGE_PROCESSING = "PROCESSING"
STAGE_WAIT_NEXT = "WAIT_NEXT"


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
    user_clip_start_sec: float = 0.0
    user_clip_end_sec: float = 0.0
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
    pending_deduction_ref_id: str = ""


class RedisChatStateStore:
    def __init__(self, settings: Settings):
        self._prefix = settings.tg_state_prefix.rstrip(":")
        self._all_ids_key = f"{self._prefix}:idx:all"
        self._processing_ids_key = f"{self._prefix}:idx:processing"
        self._updated_at_zset_key = f"{self._prefix}:idx:updated_at"
        self._state_ttl_s = max(3600, int(float(settings.tg_state_ttl_h) * 3600.0))

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

    @staticmethod
    def _parse_chat_id_token(token: str) -> int | None:
        try:
            return int(str(token).strip())
        except Exception:
            return None

    async def _sync_indexes_for_state(self, state: ChatState) -> None:
        chat_id = int(state.chat_id)
        chat_token = str(chat_id)
        stage = str(state.stage or "").strip()

        await self._redis.sadd(self._all_ids_key, chat_token)
        await self._redis.zadd(self._updated_at_zset_key, {chat_token: float(time.time())})

        if stage == STAGE_PROCESSING:
            await self._redis.sadd(self._processing_ids_key, chat_token)
        else:
            await self._redis.srem(self._processing_ids_key, chat_token)

    async def _purge_indexes_only(self, chat_id: int) -> None:
        chat_token = str(int(chat_id))
        await self._redis.srem(self._all_ids_key, chat_token)
        await self._redis.srem(self._processing_ids_key, chat_token)
        await self._redis.zrem(self._updated_at_zset_key, chat_token)

    async def _load_state_from_key(self, chat_id: int) -> ChatState | None:
        raw = await self._redis.get(self._key(chat_id))
        if not raw:
            return None
        try:
            obj = json.loads(raw)
            return ChatState.model_validate(obj)
        except Exception:
            log.warning("state_parse_failed chat=%s", chat_id)
            await self.delete_state(chat_id)
            return None

    async def get(self, chat_id: int) -> ChatState:
        st = await self._load_state_from_key(int(chat_id))
        if st is None:
            return ChatState(chat_id=int(chat_id))
        return st

    async def set(self, state: ChatState) -> None:
        await self._redis.set(self._key(state.chat_id), state.model_dump_json(), ex=self._state_ttl_s)
        await self._sync_indexes_for_state(state)

    async def delete_state(self, chat_id: int) -> None:
        cid = int(chat_id)
        await self._purge_indexes_only(cid)
        await self._redis.delete(self._key(cid))

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
        for token in (await self._redis.smembers(self._processing_ids_key) or set()):
            cid = self._parse_chat_id_token(token)
            if cid is None:
                continue
            st = await self._load_state_from_key(cid)
            if st is None:
                continue
            has_jobs = bool(st.active_job_ids) or bool(st.active_job_id)
            if st.stage == STAGE_PROCESSING and has_jobs:
                out.append(st)
        return out

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
        max_items = max(1, int(limit))
        to_check: List[int] = []

        for token in await self._redis.zrange(self._updated_at_zset_key, 0, max_items - 1):
            cid = self._parse_chat_id_token(token)
            if cid is not None:
                to_check.append(cid)

        if len(to_check) < max_items:
            for token in list(await self._redis.smembers(self._all_ids_key) or set()):
                if len(to_check) >= max_items:
                    break
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

    async def close(self) -> None:
        await self._redis.aclose()
