from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel
from redis.asyncio import Redis

from .config import Settings


STAGE_IDLE = "IDLE"
STAGE_WAIT_AUDIO = "WAIT_AUDIO"
STAGE_WAIT_LYRICS_CHOICE = "WAIT_LYRICS_CHOICE"
STAGE_WAIT_LYRICS_TEXT = "WAIT_LYRICS_TEXT"
STAGE_WAIT_CONFIRM = "WAIT_CONFIRM"
STAGE_PROCESSING = "PROCESSING"
STAGE_WAIT_NEXT = "WAIT_NEXT"


class ChatState(BaseModel):
    chat_id: int
    stage: str = STAGE_IDLE

    pending_audio_file_id: str = ""
    pending_audio_filename: str = ""
    prepared_audio_local_path: str = ""
    lyrics_text: str = ""

    active_job_id: str = ""
    active_job_started_at: float = 0.0
    last_status_msg_at: float = 0.0
    poll_attempts: int = 0
    last_job_stage: str = ""
    last_job_error: str = ""

    # Sticky result source for fallback links if file send fails repeatedly.
    last_result_url: str = ""


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
        except Exception:
            return ChatState(chat_id=int(chat_id))

        try:
            return ChatState.model_validate(obj)
        except Exception:
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
            except Exception:
                continue
            if st.stage == STAGE_PROCESSING and st.active_job_id:
                out.append(st)
        return out

    async def close(self) -> None:
        await self._redis.aclose()
