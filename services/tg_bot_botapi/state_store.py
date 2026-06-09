from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS
from .config import Settings

log = logging.getLogger("tg_bot_botapi.state_store")


# Per-user footage rotation + history persistence (survives audio re-uploads).
_ROTATION_TTL_S = 2592000  # 30 days
_ROTATION_HISTORY_MAX = 150


STAGE_IDLE = "IDLE"
STAGE_WAIT_AUDIO = "WAIT_AUDIO"
STAGE_WAIT_LYRICS_CHOICE = "WAIT_LYRICS_CHOICE"
STAGE_WAIT_LYRICS_TEXT = "WAIT_LYRICS_TEXT"
STAGE_WAIT_FRAGMENT_CHOICE = "WAIT_FRAGMENT_CHOICE"
STAGE_WAIT_FRAGMENT_TEXT = "WAIT_FRAGMENT_TEXT"
STAGE_WAIT_BG_MODE = "WAIT_BG_MODE"
STAGE_WAIT_BG_COLOR = "WAIT_BG_COLOR"
STAGE_WAIT_FOOTAGE_GENRE = "WAIT_FOOTAGE_GENRE"
STAGE_WAIT_FOOTAGE_ARTIST = "WAIT_FOOTAGE_ARTIST"
STAGE_WAIT_TIMING_CHOICE = "WAIT_TIMING_CHOICE"
STAGE_WAIT_TIMING_INPUT = "WAIT_TIMING_INPUT"
STAGE_WAIT_SUBTITLES_MODE = "WAIT_SUBTITLES_MODE"
# Hook feature (Phase A-UX). Inserted after subtitles, before versions.
STAGE_WAIT_HOOK_CHOICE = "WAIT_HOOK_CHOICE"        # yes/no
STAGE_WAIT_HOOK_DROP = "WAIT_HOOK_DROP"            # 4-button drop_t picker
STAGE_WAIT_HOOK_DROP_MANUAL = "WAIT_HOOK_DROP_MANUAL"  # text input
STAGE_WAIT_HOOK_TYPE = "WAIT_HOOK_TYPE"            # 5 hook categories (Звук/Объект/Эффект/Движение/Мысль)
STAGE_WAIT_HOOK_DEVICE = "WAIT_HOOK_DEVICE"        # F5 («Мысль») device sub-picker (5 devices)
# F3 «Эффект» — 3-step picker (hook -> transition -> extra) + slow-shutter extend.
STAGE_WAIT_EFFECT_HOOK = "WAIT_EFFECT_HOOK"
STAGE_WAIT_EFFECT_TRANSITION = "WAIT_EFFECT_TRANSITION"
STAGE_WAIT_EFFECT_EXTRA = "WAIT_EFFECT_EXTRA"
STAGE_WAIT_EFFECT_EXTEND = "WAIT_EFFECT_EXTEND"
# F2 «Объект» — single sub-picker (5 shape buttons).
STAGE_WAIT_F2_SHAPE = "WAIT_F2_SHAPE"
# F1 «Звук» — wait for the user to upload a sound file for the pre-drop window.
STAGE_WAIT_F1_SOUND = "WAIT_F1_SOUND"
STAGE_WAIT_VERSIONS = "WAIT_VERSIONS"
STAGE_WAIT_CONFIRM = "WAIT_CONFIRM"
STAGE_PROCESSING = "PROCESSING"
STAGE_WAIT_NEXT = "WAIT_NEXT"
# User waiting for a referral friend to activate their first video.
STAGE_WAITING_REFERRAL = "WAITING_REFERRAL"
# User account exists but has no credits (not yet paid).
STAGE_LOCKED = "LOCKED"

# Season flow (Hooks S1) — parallel onboarding + info menu for free users.
# Two intro screens + consent (third TZ message IS the consent prompt).
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
    bg_mode: str = "footage"
    # Solid background color key when bg_mode == "solid": "white" | "green".
    bg_solid_color: str = ""
    user_clip_start_sec: float = 0.0
    user_clip_end_sec: float = 0.0
    subtitles_mode: str = SUBTITLES_MODE_LEGACY_BLOCKS
    # Hook feature (Phase A-UX) — analysis is computed by the bot as a
    # background asyncio task right after the user confirms the focus clip
    # timing. By the time the user reaches WAIT_HOOK_CHOICE the result is
    # ready in hook_analysis_status="ready" with drop_candidates pre-loaded.
    hook_enabled: bool = False
    hook_drop_t: Optional[float] = None          # None = user picked "no drop"
    hook_type: str = "standard"                  # legacy compat; superseded by category/device
    # Hook category chosen on the 5-button picker:
    # "" | "sound" | "object" | "effect" | "motion" | "thought".
    # Only "thought" (Мысль) is implemented (=> F5 Cognition); the other four
    # are not-yet-available stubs.
    hook_category: str = ""
    # F5 («Мысль») device when hook_category == "thought":
    # "" | "punchline" | "missing_word" | "lyric_echo"
    # | "question_to_track" | "inverse_lyric".
    hook_device: str = ""
    # F3 «Эффект» selection when hook_category == "effect" (3 steps + extend).
    # Each step is optional, but at least one of hook/transition/extra is required.
    effect_hook: str = ""        # "" | hook_light | shutter_effect | flash_slow_shutter
    effect_transition: str = ""  # "" | snap_wipe | minimax | invert_flash | extract_flash | flash_on_cuts | layer_shake
    effect_extra: str = ""       # "" | xerox | analog_glitch | neon_extract | old_camera | pixel_grain | warm_map
    effect_hook_extend: str = "" # "" | to_end | after_drop:N (only for extendable hooks, e.g. flash_slow_shutter)
    # F2 «Объект» selection when hook_category == "object". Single shape pick
    # — the rest of the combo (hook_light at drop + seeded-random F3 transition
    # on post-drop cuts) is forced server-side. None/"" => no F2.
    f2_shape: str = ""           # "" | rhomb | square | star1 | star2 | elipse
    # F1 «Звук» selection when hook_category == "object"... no — == "sound".
    # S3/HTTP URL of the user-uploaded pre-drop sound. The rest of the combo
    # (hook_light at drop + seeded-random F3 transition post-drop) is forced
    # server-side. "" => no F1.
    f1_sound_url: str = ""
    # "" | "pending" | "ready" | "failed"
    hook_analysis_status: str = ""
    # Source audio path used to compute the analysis — if it ever doesn't
    # match the current prepared_audio_local_path (e.g. user re-uploaded
    # audio), the cached candidates are stale and must be recomputed.
    hook_analysis_audio_path: str = ""
    hook_analysis_clip_start: float = 0.0
    hook_analysis_clip_end: float = 0.0
    # Top 3 drop candidates as small dicts (t, confidence, source). Kept
    # compact because the full HookAnalysis JSON is ~10KB; we only need a
    # handful of numbers for the bot UI.
    hook_drop_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    # Measured BPM from the focus-clip analysis. Used by the F4 «Движение»
    # reframe to scale the per-device lead (lead_eff = lead * refBpm/bpm) so the
    # overlay cover-end lands exactly on the drop at any tempo. 0.0 = not yet
    # analyzed.
    hook_analysis_bpm: float = 0.0
    hook_analysis_error: str = ""
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

    # Timestamp for state TTL / recovery
    updated_at: float = 0.0

    # Credit reservation: ref_id of the deduction held while enqueue is in-flight.
    # Non-empty means a credit was deducted and not yet confirmed as consumed.
    pending_deduction_ref_id: str = ""

    # Referral: chat_id of the user who referred this user (0 = none).
    referrer_chat_id: int = 0
    # Timestamp when we entered WAITING_REFERRAL so recovery can unstick us.
    waiting_referral_since: float = 0.0

    # Season flow (Hooks S1) — onboarding/menu state mirrored from blast_users.
    season_intro_step: int = 0
    season_intro_completed: bool = False
    season_update_frequency: str = "finals_only"  # all | finals_only
    season_account_status: str = "new_free"       # new_free | exhausted_free | paid_active | paid_churned
    season_waitlist: bool = False
    season_referrer_tier: int = 0
    season_referrals_count: int = 0

    # Bigtest mode — mass-test all hook configs on a single input.
    # Gated by BIGTEST_ENABLED in app.py (True on team bot, False on public bot).
    bigtest_mode: bool = False
    bigtest_index: int = 0           # index of currently-running case (0-based)
    bigtest_total: int = 0           # total cases in this run
    bigtest_current_label: str = ""  # label string shown in result caption
    bigtest_master_job_id: str = ""  # job_id to reuse ASR/subtitles from
    bigtest_footage_seed: str = ""   # STAGE2_SELECTION_SEED of case-0 (reused by cases 1-27)
    # subtitles_mode of the last COMPLETED generation; survives _reset_processing_state
    # (which resets subtitles_mode to LEGACY_BLOCKS). /bigtest pins this so every case
    # uses the same mode as the reuse-source job — otherwise the seeded resume_state's
    # stage2_subtitles_mode mismatches the request and the LLM cache is invalidated.
    last_subtitles_mode: str = ""


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
        # Legacy key kept for backward compatibility with old deployments.
        self._processing_set_key = f"{self._prefix}:__index:processing"

    @property
    def redis(self) -> Redis:
        """Shared Redis client — exposed for cross-module reuse (season phase store)."""
        return self._redis

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
        processing_keys = (self._processing_ids_key, self._processing_set_key)

        await self._redis.sadd(self._all_ids_key, chat_token)
        await self._redis.zadd(self._updated_at_zset_key, {chat_token: float(time.time())})

        if stage == STAGE_PROCESSING:
            for key in processing_keys:
                await self._redis.sadd(key, chat_token)
        else:
            for key in processing_keys:
                await self._redis.srem(key, chat_token)

    async def _purge_indexes_only(self, chat_id: int) -> None:
        chat_token = str(int(chat_id))
        await self._redis.srem(self._all_ids_key, chat_token)
        await self._redis.srem(self._processing_ids_key, chat_token)
        await self._redis.srem(self._processing_set_key, chat_token)
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
        """
        O(k) where k = number of PROCESSING chats (typically small),
        instead of O(n) full SCAN of all chat states.
        """
        members = set(await self._redis.smembers(self._processing_ids_key) or set())
        # Compatibility path for deployments that still have stale legacy index.
        members.update(await self._redis.smembers(self._processing_set_key) or set())
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
            await self._redis.srem(self._processing_ids_key, *stale)
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
            # Skip index keys.
            if key in {
                self._all_ids_key,
                self._processing_ids_key,
                self._processing_set_key,
                self._updated_at_zset_key,
            }:
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
        for token in (await self._redis.smembers(self._all_ids_key) or set()):
            cid = self._parse_chat_id_token(token)
            if cid is None:
                continue
            st = await self._load_state_from_key(cid)
            if st is None:
                continue
            if st.stage == STAGE_WAITING_REFERRAL:
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
