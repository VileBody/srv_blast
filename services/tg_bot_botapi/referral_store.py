"""
Referral store — PostgreSQL backend.

Race-safety: PostgreSQL advisory lock (pg_try_advisory_xact_lock) replaces
the Redis NX lock.  The lock key is derived from invitee_chat_id so two
concurrent activations for the same invitee contend on exactly one lock slot.
Only one transaction wins; the other sees the already-inserted row in
blast_referral_bonuses and exits gracefully.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import asyncpg

from .user_store import UserStore

log = logging.getLogger("tg_bot.referral_store")


class ReferralStore:
    def __init__(
        self,
        user_store: UserStore,
        *,
        referral_bonus_credits: int = 1,
    ) -> None:
        self._users = user_store
        self._bonus = referral_bonus_credits

    @property
    def _pool(self) -> asyncpg.Pool:
        return self._users.pool

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_referral(self, invitee_chat_id: int, inviter_chat_id: int) -> bool:
        """
        Record that invitee was referred by inviter.
        INSERT … ON CONFLICT DO NOTHING — idempotent.
        Returns True if the link was stored for the first time.
        """
        now = time.time()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO blast_referrals (invitee_chat_id, inviter_chat_id, registered_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (invitee_chat_id) DO NOTHING
                """,
                int(invitee_chat_id), int(inviter_chat_id), now,
            )
        stored = result == "INSERT 0 1"
        if stored:
            log.info(
                "referral_registered invitee=%s inviter=%s",
                invitee_chat_id, inviter_chat_id,
            )
        return stored

    async def get_inviter(self, invitee_chat_id: int) -> Optional[int]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT inviter_chat_id FROM blast_referrals WHERE invitee_chat_id = $1",
                int(invitee_chat_id),
            )
        return int(row["inviter_chat_id"]) if row else None

    # ------------------------------------------------------------------
    # Bonus grant (race-safe via advisory lock + bonus table)
    # ------------------------------------------------------------------

    async def maybe_grant_referral_bonus(self, invitee_chat_id: int) -> Optional[int]:
        """
        Called when invitee completes their first successful generation.

        Uses pg_try_advisory_xact_lock(invitee_chat_id) so concurrent calls
        for the same invitee serialize within Postgres — only one wins the lock
        and inserts into blast_referral_bonuses.  The loser sees the existing
        row and returns None.

        Returns inviter_chat_id if bonus was granted, else None.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Advisory lock scoped to this transaction — released on commit/rollback.
                # We use the lower 32 bits of invitee_chat_id as the lock key.
                lock_key = int(invitee_chat_id) & 0x7FFFFFFF
                acquired = await conn.fetchval(
                    "SELECT pg_try_advisory_xact_lock($1)", lock_key
                )
                if not acquired:
                    log.info(
                        "referral_bonus_lock_miss invitee=%s — concurrent process won",
                        invitee_chat_id,
                    )
                    return None

                # Check idempotency: bonus already granted?
                existing = await conn.fetchrow(
                    "SELECT inviter_chat_id FROM blast_referral_bonuses WHERE invitee_chat_id = $1",
                    int(invitee_chat_id),
                )
                if existing:
                    return None

                # Look up inviter.
                ref_row = await conn.fetchrow(
                    "SELECT inviter_chat_id FROM blast_referrals WHERE invitee_chat_id = $1",
                    int(invitee_chat_id),
                )
                if not ref_row:
                    return None

                inviter_chat_id = int(ref_row["inviter_chat_id"])

                # Mark bonus as granted (inside the same transaction — atomic).
                now = time.time()
                await conn.execute(
                    """
                    INSERT INTO blast_referral_bonuses (invitee_chat_id, inviter_chat_id, granted_at)
                    VALUES ($1, $2, $3)
                    """,
                    int(invitee_chat_id), inviter_chat_id, now,
                )

                # Increment inviter's referral counter.
                await conn.execute(
                    """
                    UPDATE blast_users
                    SET referral_activation_count = referral_activation_count + 1
                    WHERE chat_id = $1
                    """,
                    inviter_chat_id,
                )

        # Grant the credit bonus outside the advisory-lock transaction so
        # refund_credit's own transaction doesn't nest inside it.
        try:
            new_bal = await self._users.refund_credit(
                inviter_chat_id,
                ref_id=f"referral:invitee={invitee_chat_id}",
                amount=self._bonus,
                note=f"referral bonus for invitee={invitee_chat_id}",
            )
            log.info(
                "referral_bonus_granted invitee=%s inviter=%s bonus=%d new_balance=%d",
                invitee_chat_id, inviter_chat_id, self._bonus, new_bal,
            )
        except Exception as exc:
            log.error(
                "referral_bonus_credit_error invitee=%s inviter=%s err=%r",
                invitee_chat_id, inviter_chat_id, exc,
            )

        return inviter_chat_id
