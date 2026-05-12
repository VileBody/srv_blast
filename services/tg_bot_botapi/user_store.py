"""
User profile store — PostgreSQL backend (asyncpg).

Connection is managed internally via CreditsDB-style init():
  store = UserStore(db_url)
  await store.init()   # creates pool, applies schema + migrations

Config key: CREDITS_DB_URL (or POSTGRES_HOST/USER/PASSWORD/DB/SSLMODE fallback).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import List, Optional

import asyncpg
from pydantic import BaseModel, Field

log = logging.getLogger("tg_bot.user_store")


# ---------------------------------------------------------------------------
# Schema (applied idempotently in init)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blast_users (
    chat_id                   BIGINT          PRIMARY KEY,
    username                  TEXT            NOT NULL DEFAULT '',
    credits                   INTEGER         NOT NULL DEFAULT 0,
    is_activated              BOOLEAN         NOT NULL DEFAULT FALSE,
    activated_at              DOUBLE PRECISION NOT NULL DEFAULT 0,
    referrer_chat_id          BIGINT          NOT NULL DEFAULT 0,
    referral_activation_count INTEGER         NOT NULL DEFAULT 0,
    created_at                DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blast_users_username
    ON blast_users (lower(username))
    WHERE username != '';

CREATE TABLE IF NOT EXISTS blast_ledger (
    tx_id          TEXT             PRIMARY KEY,
    chat_id        BIGINT           NOT NULL REFERENCES blast_users(chat_id),
    tx_type        TEXT             NOT NULL,
    amount         INTEGER          NOT NULL,
    balance_before INTEGER          NOT NULL,
    balance_after  INTEGER          NOT NULL,
    ref_id         TEXT             NOT NULL DEFAULT '',
    ts             DOUBLE PRECISION NOT NULL,
    note           TEXT             NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_blast_ledger_chat_ts
    ON blast_ledger (chat_id, ts DESC);

CREATE TABLE IF NOT EXISTS blast_orders (
    order_id     TEXT             PRIMARY KEY,
    chat_id      BIGINT           NOT NULL,
    credits      INTEGER          NOT NULL,
    status       TEXT             NOT NULL DEFAULT 'pending',
    created_at   DOUBLE PRECISION NOT NULL,
    confirmed_at DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_blast_orders_chat ON blast_orders (chat_id);

CREATE TABLE IF NOT EXISTS blast_referrals (
    invitee_chat_id BIGINT           PRIMARY KEY,
    inviter_chat_id BIGINT           NOT NULL,
    registered_at   DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE TABLE IF NOT EXISTS blast_referral_bonuses (
    invitee_chat_id BIGINT           PRIMARY KEY,
    inviter_chat_id BIGINT           NOT NULL,
    granted_at      DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);
"""


# Season flow (Hooks S1) — mirrored from migrations/002_season.sql.
_SCHEMA_SEASON = """
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS intro_step        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS intro_completed   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS updates_enabled   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS update_frequency  TEXT    NOT NULL DEFAULT 'finals_only';
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS account_status    TEXT    NOT NULL DEFAULT 'new_free';
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS waitlist          BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS referrer_tier     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS referrals_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS total_gens        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS paid_until        DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS last_active       DOUBLE PRECISION NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS season_referrals (
    invitee_chat_id  BIGINT           PRIMARY KEY,
    inviter_chat_id  BIGINT           NOT NULL,
    qualified        BOOLEAN          NOT NULL DEFAULT FALSE,
    qualified_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    registered_at    DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE INDEX IF NOT EXISTS idx_season_referrals_inviter
    ON season_referrals (inviter_chat_id);

CREATE TABLE IF NOT EXISTS season_generations (
    id            BIGSERIAL        PRIMARY KEY,
    chat_id       BIGINT           NOT NULL,
    metadata_hash TEXT             NOT NULL DEFAULT '',
    status        TEXT             NOT NULL DEFAULT 'pending',
    created_at    DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE INDEX IF NOT EXISTS idx_season_generations_chat_ts
    ON season_generations (chat_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_season_generations_meta
    ON season_generations (metadata_hash)
    WHERE metadata_hash != '';

CREATE TABLE IF NOT EXISTS content_events (
    id          BIGSERIAL        PRIMARY KEY,
    event_type  TEXT             NOT NULL,
    payload     JSONB            NOT NULL DEFAULT '{}'::jsonb,
    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
    delivered   BOOLEAN          NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS season_broadcasts_log (
    id           BIGSERIAL        PRIMARY KEY,
    chat_id      BIGINT           NOT NULL,
    event_id     BIGINT           NOT NULL REFERENCES content_events(id),
    delivered_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
    status       TEXT             NOT NULL DEFAULT 'sent'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_season_broadcasts_log_chat_event
    ON season_broadcasts_log (chat_id, event_id);
"""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    chat_id: int
    username: str = ""
    credits: int = 0
    is_activated: bool = False
    activated_at: float = 0.0
    referrer_chat_id: int = 0
    referral_activation_count: int = 0
    created_at: float = Field(default_factory=time.time)


class LedgerEntry(BaseModel):
    tx_id: str
    tx_type: str
    amount: int
    balance_before: int
    balance_after: int
    ref_id: str
    ts: float
    note: str = ""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class UserStore:
    def __init__(self, db_url: str) -> None:
        self._db_url = str(db_url or "").strip()
        self._pool: Optional[asyncpg.Pool] = None

    async def init(self) -> None:
        if not self._db_url:
            raise RuntimeError("UserStore: empty db_url — set CREDITS_DB_URL or POSTGRES_* env vars")
        self._pool = await asyncpg.create_pool(dsn=self._db_url, min_size=1, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute("SET TIME ZONE 'UTC'")
            await conn.execute(_SCHEMA)
            await conn.execute(_SCHEMA_SEASON)
        log.info("user_store: initialized postgres pool")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("UserStore not initialized — call await store.init() first")
        return self._pool

    @property
    def pool(self) -> asyncpg.Pool:
        return self._require_pool()

    # ------------------------------------------------------------------
    # Profile CRUD
    # ------------------------------------------------------------------

    async def get_profile(self, chat_id: int) -> Optional[UserProfile]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM blast_users WHERE chat_id = $1", int(chat_id)
            )
        return _row_to_profile(row) if row else None

    async def ensure_profile(self, chat_id: int, username: str = "") -> UserProfile:
        """Return existing profile or create blank one. Keeps username current."""
        pool = self._require_pool()
        now = time.time()
        uname = str(username or "").strip()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO blast_users (chat_id, username, created_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id) DO UPDATE
                    SET username = CASE
                        WHEN EXCLUDED.username != '' THEN EXCLUDED.username
                        ELSE blast_users.username
                    END
                RETURNING *
                """,
                int(chat_id), uname, now,
            )
        return _row_to_profile(row)

    async def is_paid_user(self, chat_id: int) -> bool:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_activated, credits FROM blast_users WHERE chat_id = $1",
                int(chat_id),
            )
        if row is None:
            return False
        return bool(row["is_activated"]) or int(row["credits"]) > 0

    # ------------------------------------------------------------------
    # Username index — O(1) via unique index
    # ------------------------------------------------------------------

    async def lookup_chat_id_by_username(self, username: str) -> Optional[int]:
        uname = str(username or "").strip().lstrip("@").lower()
        if not uname:
            return None
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT chat_id FROM blast_users WHERE lower(username) = $1", uname
            )
        return int(row["chat_id"]) if row else None

    # ------------------------------------------------------------------
    # Atomic payment confirmation (one-time per order_id)
    # ------------------------------------------------------------------

    async def confirm_payment(
        self,
        *,
        order_id: str,
        chat_id: int,
        credits: int,
        note: str = "",
    ) -> tuple[bool, bool, int]:
        """
        Returns (ok, already_done, new_balance).
        Idempotent: duplicate calls with same order_id → already_done=True, no double-credit.
        """
        pool = self._require_pool()
        now = time.time()
        tx_id = uuid.uuid4().hex
        full_note = note or f"payment order={order_id}"

        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT status FROM blast_orders WHERE order_id = $1 FOR UPDATE",
                    order_id,
                )
                if existing and existing["status"] == "confirmed":
                    bal = await _fetch_balance(conn, chat_id)
                    return True, True, bal

                # Upsert user then lock row.
                await conn.execute(
                    """
                    INSERT INTO blast_users (chat_id, created_at)
                    VALUES ($1, $2)
                    ON CONFLICT (chat_id) DO NOTHING
                    """,
                    int(chat_id), now,
                )
                user_row = await conn.fetchrow(
                    "SELECT credits FROM blast_users WHERE chat_id = $1 FOR UPDATE",
                    int(chat_id),
                )
                balance_before = int(user_row["credits"])
                new_balance = balance_before + credits

                await conn.execute(
                    """
                    UPDATE blast_users
                    SET credits = $1,
                        is_activated = TRUE,
                        activated_at = CASE WHEN activated_at = 0 THEN $2 ELSE activated_at END
                    WHERE chat_id = $3
                    """,
                    new_balance, now, int(chat_id),
                )
                await conn.execute(
                    """
                    INSERT INTO blast_orders
                        (order_id, chat_id, credits, status, created_at, confirmed_at)
                    VALUES ($1, $2, $3, 'confirmed', $4, $4)
                    ON CONFLICT (order_id) DO UPDATE
                        SET status = 'confirmed', confirmed_at = $4
                    """,
                    order_id, int(chat_id), credits, now,
                )
                await _insert_ledger(
                    conn, tx_id=tx_id, chat_id=chat_id,
                    tx_type="payment", amount=credits,
                    balance_before=balance_before, balance_after=new_balance,
                    ref_id=order_id, ts=now, note=full_note,
                )

        log.info(
            "payment_confirmed order=%s chat=%s credits=%d new_balance=%d",
            order_id, chat_id, credits, new_balance,
        )
        return True, False, new_balance

    # ------------------------------------------------------------------
    # Manual activation — same guarantee as payment
    # ------------------------------------------------------------------

    async def manual_activate(
        self,
        *,
        activation_id: str,
        chat_id: int,
        credits: int,
        note: str = "",
    ) -> tuple[bool, bool, int]:
        full_note = note or f"manual_activation id={activation_id}"
        return await self.confirm_payment(
            order_id=activation_id,
            chat_id=chat_id,
            credits=credits,
            note=full_note,
        )

    # ------------------------------------------------------------------
    # Atomic credit deduction
    # ------------------------------------------------------------------

    async def deduct_credit(
        self,
        chat_id: int,
        *,
        ref_id: str,
        amount: int = 1,
        note: str = "",
    ) -> tuple[bool, int]:
        """Returns (ok, new_balance). ok=False → insufficient balance, nothing changed."""
        pool = self._require_pool()
        now = time.time()
        tx_id = uuid.uuid4().hex

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT credits FROM blast_users WHERE chat_id = $1 FOR UPDATE",
                    int(chat_id),
                )
                if row is None:
                    log.warning("deduct_credit: user not found chat=%s", chat_id)
                    return False, 0
                balance = int(row["credits"])
                if balance < amount:
                    log.warning(
                        "deduct_credit_insufficient chat=%s balance=%d need=%d",
                        chat_id, balance, amount,
                    )
                    return False, balance

                new_balance = balance - amount
                await conn.execute(
                    "UPDATE blast_users SET credits = $1 WHERE chat_id = $2",
                    new_balance, int(chat_id),
                )
                await _insert_ledger(
                    conn, tx_id=tx_id, chat_id=chat_id,
                    tx_type="deduction", amount=-amount,
                    balance_before=balance, balance_after=new_balance,
                    ref_id=ref_id, ts=now,
                    note=note or f"deduction ref={ref_id}",
                )

        log.info(
            "credit_deducted chat=%s ref=%s amount=%d new_balance=%d",
            chat_id, ref_id, amount, new_balance,
        )
        return True, new_balance

    # ------------------------------------------------------------------
    # Atomic refund
    # ------------------------------------------------------------------

    async def refund_credit(
        self,
        chat_id: int,
        *,
        ref_id: str,
        amount: int = 1,
        note: str = "",
    ) -> int:
        """Add `amount` credits back. Returns new_balance (0 on error)."""
        pool = self._require_pool()
        now = time.time()
        tx_id = uuid.uuid4().hex

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT credits FROM blast_users WHERE chat_id = $1 FOR UPDATE",
                        int(chat_id),
                    )
                    if row is None:
                        log.warning("refund_credit: user not found chat=%s", chat_id)
                        return 0
                    balance = int(row["credits"])
                    new_balance = balance + amount
                    await conn.execute(
                        "UPDATE blast_users SET credits = $1 WHERE chat_id = $2",
                        new_balance, int(chat_id),
                    )
                    await _insert_ledger(
                        conn, tx_id=tx_id, chat_id=chat_id,
                        tx_type="refund", amount=amount,
                        balance_before=balance, balance_after=new_balance,
                        ref_id=ref_id, ts=now,
                        note=note or f"refund ref={ref_id}",
                    )
            log.info(
                "credit_refunded chat=%s ref=%s amount=%d new_balance=%d",
                chat_id, ref_id, amount, new_balance,
            )
            return new_balance
        except Exception as exc:
            log.error("refund_credit_error chat=%s ref=%s err=%r", chat_id, ref_id, exc)
            return 0

    # ------------------------------------------------------------------
    # Admin adjustment (signed delta, clamped at 0)
    # ------------------------------------------------------------------

    async def admin_adjust(
        self,
        chat_id: int,
        *,
        delta: int,
        admin_ref: str,
        note: str = "",
    ) -> tuple[bool, int, int]:
        """
        Apply signed delta. Negative clamped so balance never goes below 0.
        Ledger records the *actual* applied delta — no discrepancy possible.
        Returns (ok, balance_before, balance_after).
        """
        pool = self._require_pool()
        now = time.time()
        tx_id = uuid.uuid4().hex

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT credits FROM blast_users WHERE chat_id = $1 FOR UPDATE",
                        int(chat_id),
                    )
                    if row is None:
                        log.warning("admin_adjust: user not found chat=%s", chat_id)
                        return False, 0, 0
                    balance = int(row["credits"])
                    new_balance = max(0, balance + delta)
                    actual_delta = new_balance - balance
                    await conn.execute(
                        "UPDATE blast_users SET credits = $1 WHERE chat_id = $2",
                        new_balance, int(chat_id),
                    )
                    await _insert_ledger(
                        conn, tx_id=tx_id, chat_id=chat_id,
                        tx_type="admin_adjustment", amount=actual_delta,
                        balance_before=balance, balance_after=new_balance,
                        ref_id=admin_ref, ts=now,
                        note=note or f"admin_adjust delta={delta} by={admin_ref}",
                    )
            log.info(
                "admin_adjusted chat=%s requested=%d actual=%d before=%d after=%d by=%s",
                chat_id, delta, actual_delta, balance, new_balance, admin_ref,
            )
            return True, balance, new_balance
        except Exception as exc:
            log.error("admin_adjust_error chat=%s delta=%d err=%r", chat_id, delta, exc)
            return False, 0, 0

    # ------------------------------------------------------------------
    # Ledger read
    # ------------------------------------------------------------------

    async def get_ledger(self, chat_id: int, *, limit: int = 50) -> List[LedgerEntry]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM blast_ledger
                WHERE chat_id = $1
                ORDER BY ts DESC
                LIMIT $2
                """,
                int(chat_id), limit,
            )
        return [_row_to_ledger(r) for r in rows]

    # ------------------------------------------------------------------
    # Season flow (Hooks S1) — onboarding state + status flags
    # ------------------------------------------------------------------

    async def get_season_state(self, chat_id: int) -> Optional[dict]:
        """Return season-flow fields for a user, or None if user is unknown."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT intro_step, intro_completed, updates_enabled,
                       update_frequency, account_status, waitlist,
                       referrer_tier, referrals_count, total_gens, paid_until
                FROM blast_users
                WHERE chat_id = $1
                """,
                int(chat_id),
            )
        if row is None:
            return None
        return {
            "intro_step": int(row["intro_step"] or 0),
            "intro_completed": bool(row["intro_completed"]),
            "updates_enabled": bool(row["updates_enabled"]),
            "update_frequency": str(row["update_frequency"] or "finals_only"),
            "account_status": str(row["account_status"] or "new_free"),
            "waitlist": bool(row["waitlist"]),
            "referrer_tier": int(row["referrer_tier"] or 0),
            "referrals_count": int(row["referrals_count"] or 0),
            "total_gens": int(row["total_gens"] or 0),
            "paid_until": float(row["paid_until"] or 0.0),
        }

    async def set_intro_step(self, chat_id: int, step: int) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE blast_users SET intro_step = $2 WHERE chat_id = $1",
                int(chat_id), int(step),
            )

    async def complete_intro(
        self,
        chat_id: int,
        *,
        update_frequency: str,
    ) -> None:
        """Flip intro_completed=TRUE and store the user's notification choice."""
        freq = update_frequency if update_frequency in ("all", "finals_only") else "finals_only"
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE blast_users
                SET intro_completed  = TRUE,
                    intro_step       = 3,
                    updates_enabled  = TRUE,
                    update_frequency = $2,
                    last_active      = $3
                WHERE chat_id = $1
                """,
                int(chat_id), freq, time.time(),
            )

    async def set_notification_pref(self, chat_id: int, choice: str) -> None:
        """Update updates_enabled + update_frequency from a single user choice.

        choice ∈ {'all', 'finals_only', 'off'}.
        """
        if choice not in ("all", "finals_only", "off"):
            raise ValueError(f"invalid notification choice: {choice!r}")
        enabled = choice != "off"
        freq = choice if choice != "off" else "finals_only"
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE blast_users
                SET updates_enabled  = $2,
                    update_frequency = $3
                WHERE chat_id = $1
                """,
                int(chat_id), enabled, freq,
            )

    async def set_waitlist(self, chat_id: int, *, joined: bool) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE blast_users SET waitlist = $2 WHERE chat_id = $1",
                int(chat_id), bool(joined),
            )

    async def set_account_status(self, chat_id: int, status: str) -> None:
        if status not in ("new_free", "exhausted_free", "paid_active", "paid_churned"):
            raise ValueError(f"invalid account_status: {status!r}")
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE blast_users SET account_status = $2 WHERE chat_id = $1",
                int(chat_id), status,
            )

    async def touch_last_active(self, chat_id: int) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE blast_users SET last_active = $2 WHERE chat_id = $1",
                int(chat_id), time.time(),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_profile(row: asyncpg.Record) -> UserProfile:
    return UserProfile(
        chat_id=int(row["chat_id"]),
        username=str(row["username"] or ""),
        credits=int(row["credits"]),
        is_activated=bool(row["is_activated"]),
        activated_at=float(row["activated_at"] or 0),
        referrer_chat_id=int(row["referrer_chat_id"] or 0),
        referral_activation_count=int(row["referral_activation_count"] or 0),
        created_at=float(row["created_at"] or 0),
    )


def _row_to_ledger(row: asyncpg.Record) -> LedgerEntry:
    return LedgerEntry(
        tx_id=str(row["tx_id"]),
        tx_type=str(row["tx_type"]),
        amount=int(row["amount"]),
        balance_before=int(row["balance_before"]),
        balance_after=int(row["balance_after"]),
        ref_id=str(row["ref_id"] or ""),
        ts=float(row["ts"]),
        note=str(row["note"] or ""),
    )


async def _fetch_balance(conn: asyncpg.Connection, chat_id: int) -> int:
    row = await conn.fetchrow(
        "SELECT credits FROM blast_users WHERE chat_id = $1", int(chat_id)
    )
    return int(row["credits"]) if row else 0


async def _insert_ledger(
    conn: asyncpg.Connection,
    *,
    tx_id: str,
    chat_id: int,
    tx_type: str,
    amount: int,
    balance_before: int,
    balance_after: int,
    ref_id: str,
    ts: float,
    note: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO blast_ledger
            (tx_id, chat_id, tx_type, amount, balance_before, balance_after, ref_id, ts, note)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        tx_id, int(chat_id), tx_type, amount,
        balance_before, balance_after, ref_id, ts, note,
    )
