from __future__ import annotations

import json
import logging
from typing import List

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


def _normalize_username(raw: str) -> str:
    u = str(raw or "").strip().lower()
    if not u:
        return ""
    if not u.startswith("@"):
        u = "@" + u
    return u


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
    referral_wait_started_at: float = 0.0
    reminder_at: float = 0.0


class RedisChatStateStore:
    def __init__(self, settings: Settings):
        self._prefix = settings.tg_state_prefix.rstrip(":")
        self._username_index_prefix = f"{self._prefix}:username_index"
        self._chat_username_prefix = f"{self._prefix}:chat_username"
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

    async def get(self, chat_id: int) -> ChatState:
        raw = await self._redis.get(self._key(chat_id))
        if not raw:
            return ChatState(chat_id=int(chat_id))
        try:
            obj = json.loads(raw)
        except Exception as e:
            self._raise_corrupted_state(chat_id=int(chat_id), reason="json", raw=raw, err=e)

        try:
            return ChatState.model_validate(obj)
        except Exception as e:
            self._raise_corrupted_state(chat_id=int(chat_id), reason="validation", raw=raw, err=e)

    async def set(self, state: ChatState) -> None:
        chat_id = int(state.chat_id)
        key = self._key(chat_id)
        state_raw = state.model_dump_json()
        new_username = _normalize_username(state.chat_username)
        old_username = _normalize_username(await self._redis.get(self._chat_username_key(chat_id)))

        await self._redis.set(key, state_raw)

        if old_username and old_username != new_username:
            old_map_key = self._username_key(old_username)
            old_owner = await self._redis.get(old_map_key)
            if str(old_owner or "").strip() == str(chat_id):
                await self._redis.delete(old_map_key)

        if new_username:
            await self._redis.set(self._username_key(new_username), str(chat_id))
            await self._redis.set(self._chat_username_key(chat_id), new_username)
        else:
            await self._redis.delete(self._chat_username_key(chat_id))

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
        existing.referral_tag = ""
        existing.referral_wait_started_at = 0.0
        await self.set(existing)
        return existing

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
            st = self._parse_state_or_none(key=key, raw=raw)
            if st is None:
                continue
            has_jobs = bool(st.active_job_ids) or bool(st.active_job_id)
            if st.stage == STAGE_PROCESSING and has_jobs:
                out.append(st)
        return out

    async def list_all_states(self) -> List[ChatState]:
        out: List[ChatState] = []
        pattern = f"{self._prefix}:*"
        async for key in self._redis.scan_iter(match=pattern, count=200):
            raw = await self._redis.get(key)
            if not raw:
                continue
            st = self._parse_state_or_none(key=key, raw=raw)
            if st is None:
                continue
            out.append(st)
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
    async def list_pending_reminders(self, now: float) -> List[ChatState]:
        out: List[ChatState] = []
        pattern = f"{self._prefix}:*"
        async for key in self._redis.scan_iter(match=pattern, count=200):
            raw = await self._redis.get(key)
            if not raw:
                continue
            st = self._parse_state_or_none(key=key, raw=raw)
            if st is None:
                continue
            if st.stage == STAGE_KEEP_IN_TOUCH and st.reminder_at > 0 and st.reminder_at <= now:
                out.append(st)
        return out

    async def close(self) -> None:
        await self._redis.aclose()
