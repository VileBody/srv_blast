"""Season referral store — qualified-after-intro tracking + tier math.

Distinct from the legacy `blast_referrals` table, which qualifies invitees
only after their first successful generation. The season cycle needs a
softer trigger (completing onboarding) so inviters see progress before
generations even open.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import asyncpg

log = logging.getLogger("tg_bot_botapi.season.referral")


TIER_THRESHOLDS = (1, 3, 5)


def tier_for(count: int) -> int:
    """Return the highest tier reached for a given qualified-friends count."""
    achieved = 0
    for idx, threshold in enumerate(TIER_THRESHOLDS, start=1):
        if count >= threshold:
            achieved = idx
    return achieved


@dataclass(frozen=True)
class QualificationResult:
    qualified_now: bool          # True if this call flipped qualified false→true
    inviter_chat_id: int
    inviter_new_count: int
    inviter_old_tier: int
    inviter_new_tier: int

    @property
    def tier_up(self) -> bool:
        return self.inviter_new_tier > self.inviter_old_tier


class SeasonReferralStore:
    """Persistence for season referrals (separate from legacy blast_referrals)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def register(self, invitee_chat_id: int, inviter_chat_id: int) -> bool:
        """Record the inviter→invitee link. Idempotent.

        Returns True if a new row was created, False if the invitee was
        already linked (we keep the first inviter and ignore later attempts).
        """
        if invitee_chat_id == inviter_chat_id:
            return False
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO season_referrals
                    (invitee_chat_id, inviter_chat_id, registered_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (invitee_chat_id) DO NOTHING
                """,
                int(invitee_chat_id), int(inviter_chat_id), time.time(),
            )
        stored = result == "INSERT 0 1"
        if stored:
            log.info(
                "season_referral_registered invitee=%s inviter=%s",
                invitee_chat_id, inviter_chat_id,
            )
        return stored

    async def mark_qualified(self, invitee_chat_id: int) -> Optional[QualificationResult]:
        """Flip qualified=TRUE for an invitee and bump the inviter's stats.

        Returns None if the invitee has no referrer recorded. Returns a
        QualificationResult either way when a referrer exists, with
        `qualified_now` distinguishing first-time qualification from a
        re-trigger.

        Race-safe via a row-level lock on the season_referrals row and an
        advisory lock on the inviter chat_id.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT inviter_chat_id, qualified
                    FROM season_referrals
                    WHERE invitee_chat_id = $1
                    FOR UPDATE
                    """,
                    int(invitee_chat_id),
                )
                if not row:
                    return None
                inviter_chat_id = int(row["inviter_chat_id"])
                was_qualified = bool(row["qualified"])

                # Lock the inviter so concurrent qualifications don't both
                # increment referrals_count from the same baseline.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    inviter_chat_id,
                )

                old_count = await _read_inviter_count(conn, inviter_chat_id)
                old_tier = tier_for(old_count)

                if was_qualified:
                    new_tier = tier_for(old_count)
                    return QualificationResult(
                        qualified_now=False,
                        inviter_chat_id=inviter_chat_id,
                        inviter_new_count=old_count,
                        inviter_old_tier=old_tier,
                        inviter_new_tier=new_tier,
                    )

                await conn.execute(
                    """
                    UPDATE season_referrals
                    SET qualified = TRUE, qualified_at = $2
                    WHERE invitee_chat_id = $1
                    """,
                    int(invitee_chat_id), time.time(),
                )
                new_count = old_count + 1
                new_tier = tier_for(new_count)
                await conn.execute(
                    """
                    UPDATE blast_users
                    SET referrals_count = $2,
                        referrer_tier   = $3
                    WHERE chat_id = $1
                    """,
                    inviter_chat_id, new_count, new_tier,
                )

                log.info(
                    "season_referral_qualified invitee=%s inviter=%s "
                    "new_count=%s tier=%s→%s",
                    invitee_chat_id, inviter_chat_id,
                    new_count, old_tier, new_tier,
                )
                return QualificationResult(
                    qualified_now=True,
                    inviter_chat_id=inviter_chat_id,
                    inviter_new_count=new_count,
                    inviter_old_tier=old_tier,
                    inviter_new_tier=new_tier,
                )

    async def stats_for(self, chat_id: int) -> tuple[int, int]:
        """Return (referrals_count, referrer_tier) for the given user."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT referrals_count, referrer_tier
                FROM blast_users
                WHERE chat_id = $1
                """,
                int(chat_id),
            )
        if not row:
            return (0, 0)
        return (int(row["referrals_count"] or 0), int(row["referrer_tier"] or 0))


async def _read_inviter_count(conn: asyncpg.Connection, inviter_chat_id: int) -> int:
    """Source of truth: count of qualified invitees in season_referrals.

    We re-derive the count under the advisory lock instead of trusting the
    blast_users.referrals_count counter, which could be stale if a prior
    update was rolled back.
    """
    row = await conn.fetchrow(
        """
        SELECT COUNT(*)::int AS n
        FROM season_referrals
        WHERE inviter_chat_id = $1 AND qualified = TRUE
        """,
        int(inviter_chat_id),
    )
    return int(row["n"]) if row else 0
