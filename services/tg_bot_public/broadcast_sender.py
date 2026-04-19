"""Broadcast + lifecycle sender workers.

Runs as background asyncio tasks inside the public bot process. Reads
scheduled/sending broadcasts from Postgres and delivers Telegram messages with
rate-limiting, retries, and graceful handling of blocked users.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .credits_db import CreditsDB

log = logging.getLogger("broadcast_sender")

# Telegram hard limit is 30 msg/sec for bots across all chats; stay conservative.
DEFAULT_RATE_PER_SEC = 20.0
BATCH_SIZE = 200


def _parse_mode_or_none(value: str) -> Optional[str]:
    v = str(value or "").strip().upper()
    if v in ("HTML", "MARKDOWN", "MARKDOWNV2"):
        return "HTML" if v == "HTML" else ("MarkdownV2" if v == "MARKDOWNV2" else "Markdown")
    return None


def _build_keyboard(buttons: List[Dict[str, str]]) -> Optional[InlineKeyboardMarkup]:
    rows: List[List[InlineKeyboardButton]] = []
    for btn in buttons or []:
        text = str(btn.get("text", "")).strip()
        url = str(btn.get("url", "")).strip()
        if not text or not url:
            continue
        rows.append([InlineKeyboardButton(text=text[:64], url=url)])
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_bot_message(
    bot: Bot,
    tg_id: int,
    *,
    text: str,
    parse_mode: str = "HTML",
    media_type: str = "",
    media_file_id: str = "",
    media_url: str = "",
    buttons: Optional[List[Dict[str, str]]] = None,
) -> None:
    """Send a single message from the bot to a user. Raises on Telegram errors.

    Caller handles TelegramForbiddenError (user blocked) / TelegramRetryAfter / generic.
    """
    pm = _parse_mode_or_none(parse_mode)
    kb = _build_keyboard(buttons or [])
    media = (media_file_id or media_url).strip()
    mt = (media_type or "").strip().lower()

    if mt == "photo" and media:
        await bot.send_photo(tg_id, photo=media, caption=text or None, parse_mode=pm, reply_markup=kb)
    elif mt == "video" and media:
        await bot.send_video(tg_id, video=media, caption=text or None, parse_mode=pm, reply_markup=kb)
    elif mt == "animation" and media:
        await bot.send_animation(tg_id, animation=media, caption=text or None, parse_mode=pm, reply_markup=kb)
    elif mt == "document" and media:
        await bot.send_document(tg_id, document=media, caption=text or None, parse_mode=pm, reply_markup=kb)
    else:
        await bot.send_message(tg_id, text or "", parse_mode=pm, reply_markup=kb, disable_web_page_preview=False)


class RateLimiter:
    """Token-bucket-ish limiter: enforces average messages/second."""

    def __init__(self, rate_per_sec: float) -> None:
        self._min_interval = 1.0 / max(0.5, float(rate_per_sec))
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


async def _send_with_retry(
    bot: Bot,
    tg_id: int,
    *,
    text: str,
    parse_mode: str,
    media_type: str,
    media_file_id: str,
    media_url: str,
    buttons: List[Dict[str, str]],
    limiter: RateLimiter,
    max_retries: int = 3,
) -> tuple[str, str]:
    """Attempt to send. Returns (status, error) where status ∈ sent|blocked|failed."""
    last_err = ""
    for attempt in range(max_retries):
        try:
            await limiter.acquire()
            await send_bot_message(
                bot, tg_id,
                text=text, parse_mode=parse_mode,
                media_type=media_type, media_file_id=media_file_id, media_url=media_url,
                buttons=buttons,
            )
            return ("sent", "")
        except TelegramForbiddenError as e:
            return ("blocked", f"forbidden: {e}")
        except TelegramRetryAfter as e:
            wait = float(getattr(e, "retry_after", 3.0)) + 0.5
            log.warning("broadcast: retry_after=%.1fs tg_id=%s", wait, tg_id)
            await asyncio.sleep(min(wait, 60.0))
            last_err = f"retry_after: {wait:.1f}s"
            continue
        except TelegramBadRequest as e:
            return ("failed", f"bad_request: {e}"[:400])
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"[:400]
            await asyncio.sleep(1.0 + attempt)
    return ("failed", last_err or "exhausted retries")


class BroadcastWorker:
    """Background loop that processes broadcasts one at a time."""

    def __init__(
        self,
        db: CreditsDB,
        bot_ref: List[Optional[Bot]],
        *,
        rate_per_sec: float = DEFAULT_RATE_PER_SEC,
        poll_interval: float = 5.0,
    ) -> None:
        self._db = db
        self._bot_ref = bot_ref
        self._limiter = RateLimiter(rate_per_sec)
        self._poll = poll_interval
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("broadcast worker started")
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                log.exception("broadcast worker tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass
        log.info("broadcast worker stopped")

    async def _tick(self) -> None:
        due = await self._db.find_due_broadcasts()
        for item in due:
            if self._stop.is_set():
                return
            await self._process_one(int(item["id"]))

    async def _process_one(self, bid: int) -> None:
        bc = await self._db.get_broadcast(bid)
        if not bc:
            return
        bot = self._bot_ref[0] if self._bot_ref else None
        if bot is None:
            log.warning("broadcast %s: bot not ready, skipping", bid)
            return

        if bc["status"] == "scheduled":
            # Resolve audience now, seed deliveries, flip to sending.
            audience_ids = await self._db.resolve_audience(bc["audience"])
            await self._db.seed_broadcast_deliveries(bid, audience_ids)
            await self._db.set_broadcast_status(
                bid, "sending", started_at=_now_naive(), audience_size=len(audience_ids),
            )
            log.info("broadcast %s: started (audience=%d)", bid, len(audience_ids))

        # Drain pending deliveries in batches.
        while not self._stop.is_set():
            pending = await self._db.fetch_pending_deliveries(bid, batch=BATCH_SIZE)
            if not pending:
                break
            for tg_id in pending:
                if self._stop.is_set():
                    return
                status, err = await _send_with_retry(
                    bot, tg_id,
                    text=bc["text"],
                    parse_mode=bc["parse_mode"],
                    media_type=bc["media_type"],
                    media_file_id=bc["media_file_id"],
                    media_url=bc["media_url"],
                    buttons=bc["buttons"],
                    limiter=self._limiter,
                )
                await self._db.mark_delivery(bid, tg_id, status, err)
                if status == "blocked":
                    try:
                        await self._db.log_event(tg_id, "bot_blocked", "broadcast_detected")
                    except Exception:
                        pass

        # Nothing left → mark done.
        remaining = await self._db.fetch_pending_deliveries(bid, batch=1)
        if not remaining:
            await self._db.set_broadcast_status(bid, "done", finished_at=_now_naive())
            log.info("broadcast %s: finished", bid)


class LifecycleWorker:
    """Background loop that fires lifecycle rules on a slow cadence (default hourly)."""

    def __init__(
        self,
        db: CreditsDB,
        bot_ref: List[Optional[Bot]],
        *,
        rate_per_sec: float = DEFAULT_RATE_PER_SEC,
        tick_interval: float = 3600.0,
        batch_per_rule: int = 200,
    ) -> None:
        self._db = db
        self._bot_ref = bot_ref
        self._limiter = RateLimiter(rate_per_sec)
        self._tick = tick_interval
        self._batch = batch_per_rule
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("lifecycle worker started (interval=%.0fs)", self._tick)
        # Small initial delay so we don't race with bot startup.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=60.0)
            return
        except asyncio.TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                await self._tick_once()
            except Exception as e:
                log.exception("lifecycle tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
            except asyncio.TimeoutError:
                pass
        log.info("lifecycle worker stopped")

    async def _tick_once(self) -> None:
        bot = self._bot_ref[0] if self._bot_ref else None
        if bot is None:
            return
        rules = await self._db.list_lifecycle_rules()
        for rule in rules:
            if not rule.get("enabled"):
                continue
            try:
                await self._fire_rule(bot, rule)
            except Exception as e:
                log.exception("lifecycle rule %s failed: %s", rule.get("id"), e)

    async def _fire_rule(self, bot: Bot, rule: Dict[str, Any]) -> None:
        rid = int(rule["id"])
        candidates = await self._db.find_lifecycle_candidates(rule, limit=self._batch)
        if not candidates:
            await self._db.touch_lifecycle_rule(rid)
            return
        log.info("lifecycle rule %s: %d candidates", rid, len(candidates))
        for tg_id in candidates:
            if self._stop.is_set():
                return
            status, err = await _send_with_retry(
                bot, tg_id,
                text=rule["message_text"],
                parse_mode=rule.get("parse_mode", "HTML"),
                media_type="", media_file_id="", media_url="",
                buttons=[],
                limiter=self._limiter,
            )
            await self._db.record_lifecycle_fire(rid, tg_id, status, err)
            if status == "blocked":
                try:
                    await self._db.log_event(tg_id, "bot_blocked", "lifecycle_detected")
                except Exception:
                    pass
        await self._db.touch_lifecycle_rule(rid)


def _now_naive():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def start_broadcast_workers(
    db: CreditsDB,
    bot_ref: List[Optional[Bot]],
    *,
    rate_per_sec: float = DEFAULT_RATE_PER_SEC,
    lifecycle_interval: float = 3600.0,
) -> tuple[asyncio.Task, asyncio.Task, Callable[[], None]]:
    """Launch broadcast + lifecycle workers. Returns (tasks..., stop_fn)."""
    bc = BroadcastWorker(db, bot_ref, rate_per_sec=rate_per_sec)
    lc = LifecycleWorker(db, bot_ref, rate_per_sec=rate_per_sec, tick_interval=lifecycle_interval)
    t1 = asyncio.create_task(bc.run(), name="broadcast_worker")
    t2 = asyncio.create_task(lc.run(), name="lifecycle_worker")

    def _stop() -> None:
        bc.stop()
        lc.stop()

    return t1, t2, _stop
