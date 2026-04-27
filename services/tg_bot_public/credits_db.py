"""PostgreSQL-backed credits & user tracking for the public Telegram bot."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg

log = logging.getLogger("credits_db")

_UTM_KEYS = ("source", "medium", "campaign", "content", "term")
_PAID_REASONS = ("payment", "admin_activate", "manual_activation")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id               BIGINT PRIMARY KEY,
    username            TEXT      NOT NULL DEFAULT '',
    credits             INTEGER   NOT NULL DEFAULT 0,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    source              TEXT      NOT NULL DEFAULT '',

    first_utm_source    TEXT      NOT NULL DEFAULT '',
    first_utm_medium    TEXT      NOT NULL DEFAULT '',
    first_utm_campaign  TEXT      NOT NULL DEFAULT '',
    first_utm_content   TEXT      NOT NULL DEFAULT '',
    first_utm_term      TEXT      NOT NULL DEFAULT '',
    first_utm_payload   TEXT      NOT NULL DEFAULT '',
    first_utm_at        TIMESTAMP,

    last_utm_source     TEXT      NOT NULL DEFAULT '',
    last_utm_medium     TEXT      NOT NULL DEFAULT '',
    last_utm_campaign   TEXT      NOT NULL DEFAULT '',
    last_utm_content    TEXT      NOT NULL DEFAULT '',
    last_utm_term       TEXT      NOT NULL DEFAULT '',
    last_utm_payload    TEXT      NOT NULL DEFAULT '',
    last_utm_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id          BIGSERIAL PRIMARY KEY,
    tg_id       BIGINT NOT NULL,
    amount      INTEGER NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    admin_note  TEXT    NOT NULL DEFAULT '',
    actor       TEXT    NOT NULL DEFAULT '',
    context_order_id TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tx_tg_id      ON transactions(tg_id);
CREATE INDEX IF NOT EXISTS idx_tx_created_at ON transactions(created_at);

CREATE TABLE IF NOT EXISTS admins (
    tg_id       BIGINT PRIMARY KEY,
    username    TEXT      NOT NULL DEFAULT '',
    added_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          BIGSERIAL PRIMARY KEY,
    tg_id       BIGINT NOT NULL,
    event       TEXT      NOT NULL,
    detail      TEXT      NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_act_tg_id      ON activity_log(tg_id);
CREATE INDEX IF NOT EXISTS idx_act_created_at ON activity_log(created_at);
CREATE INDEX IF NOT EXISTS idx_act_event      ON activity_log(event);

CREATE TABLE IF NOT EXISTS payments (
    id            BIGSERIAL PRIMARY KEY,
    order_id      TEXT      NOT NULL UNIQUE,
    tg_id         BIGINT    NOT NULL,
    amount_rub    INTEGER   NOT NULL,
    package       TEXT      NOT NULL DEFAULT '',
    status        TEXT      NOT NULL DEFAULT 'NEW',
    payment_id    TEXT      NOT NULL DEFAULT '',

    utm_source    TEXT      NOT NULL DEFAULT '',
    utm_medium    TEXT      NOT NULL DEFAULT '',
    utm_campaign  TEXT      NOT NULL DEFAULT '',
    utm_content   TEXT      NOT NULL DEFAULT '',
    utm_term      TEXT      NOT NULL DEFAULT '',
    utm_payload   TEXT      NOT NULL DEFAULT '',

    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pay_tg_id       ON payments(tg_id);
CREATE INDEX IF NOT EXISTS idx_pay_status      ON payments(status);
CREATE INDEX IF NOT EXISTS idx_pay_payment_id  ON payments(payment_id);

CREATE TABLE IF NOT EXISTS utm_touches (
    id             BIGSERIAL PRIMARY KEY,
    tg_id          BIGINT    NOT NULL,
    touch_type     TEXT      NOT NULL DEFAULT 'start',
    source         TEXT      NOT NULL DEFAULT '',
    medium         TEXT      NOT NULL DEFAULT '',
    campaign       TEXT      NOT NULL DEFAULT '',
    content        TEXT      NOT NULL DEFAULT '',
    term           TEXT      NOT NULL DEFAULT '',
    payload        TEXT      NOT NULL DEFAULT '',
    raw_start_arg  TEXT      NOT NULL DEFAULT '',
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_utm_touches_tg_id      ON utm_touches(tg_id);
CREATE INDEX IF NOT EXISTS idx_utm_touches_created_at ON utm_touches(created_at);
CREATE INDEX IF NOT EXISTS idx_utm_touches_source     ON utm_touches(source);
CREATE INDEX IF NOT EXISTS idx_utm_touches_campaign   ON utm_touches(campaign);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    tg_id           BIGINT    NOT NULL,
    package         TEXT      NOT NULL DEFAULT '',
    rebill_id       TEXT      NOT NULL DEFAULT '',
    amount_rub      INTEGER   NOT NULL DEFAULT 0,
    status          TEXT      NOT NULL DEFAULT 'active',
    next_charge_at  TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '30 days'),
    charge_retries  INTEGER   NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    cancelled_at    TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sub_tg_id       ON subscriptions(tg_id);
CREATE INDEX IF NOT EXISTS idx_sub_status      ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_sub_next_charge ON subscriptions(next_charge_at);
"""


def _fmt_ts(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _rowcount_from_tag(tag: str) -> int:
    parts = str(tag or "").split()
    if not parts:
        return 0
    last = parts[-1]
    return int(last) if last.isdigit() else 0


def _norm_text(value: Any, *, max_len: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = " ".join(text.split())
    return compact[:max_len]


def _client_product_where(tg_id_col: str, product: str) -> str:
    """SQL fragment for filtering clients by purchased product.

    Returns "" when no filter applies. The fragment references `tg_id_col`
    (e.g. 'u.tg_id') for the user-side join and produces a fully-qualified
    EXISTS / NOT EXISTS expression — no parameters, only literals.
    """
    if not product:
        return ""
    if product == "trial":
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} "
            f"AND p.status = 'CONFIRMED' AND p.package = '5')"
        )
    if product == "blast":
        # one-off Blast purchase, no active subscription
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} "
            f"AND p.status = 'CONFIRMED' AND p.package = '15') "
            f"AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = {tg_id_col} "
            f"AND s.status = 'active' AND s.package = '15')"
        )
    if product == "blast_subscription":
        return (
            f"EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = {tg_id_col} "
            f"AND s.status = 'active' AND s.package = '15')"
        )
    if product == "glow":
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} "
            f"AND p.status = 'CONFIRMED' AND p.package = '30')"
        )
    if product == "impulse":
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} "
            f"AND p.status = 'CONFIRMED' AND p.package = '50')"
        )
    if product == "any":
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} AND p.status = 'CONFIRMED')"
        )
    if product == "none":
        return (
            f"NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} AND p.status = 'CONFIRMED') "
            f"AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = {tg_id_col} AND s.status = 'active')"
        )
    return ""


def _clean_utm(utm: Optional[Dict[str, str]]) -> Dict[str, str]:
    src = dict(utm or {})
    payload = _norm_text(src.get("payload", ""), max_len=512)
    out = {
        "source": _norm_text(src.get("source", "")),
        "medium": _norm_text(src.get("medium", "")),
        "campaign": _norm_text(src.get("campaign", "")),
        "content": _norm_text(src.get("content", "")),
        "term": _norm_text(src.get("term", "")),
        "payload": payload,
    }
    return out


class CreditsDB:
    def __init__(self, db_url: str) -> None:
        self._db_url = str(db_url or "").strip()
        self._pool: Optional[asyncpg.Pool] = None

    async def init(self) -> None:
        if not self._db_url:
            raise RuntimeError("credits_db: empty CREDITS_DB_URL / POSTGRES_* config")
        self._pool = await asyncpg.create_pool(dsn=self._db_url, min_size=1, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute("SET TIME ZONE 'UTC'")
            await conn.execute(_SCHEMA)
            await self._ensure_migrations(conn)
        log.info("credits_db: initialized postgres")

    async def _ensure_migrations(self, conn: asyncpg.Connection) -> None:
        # Keep old databases compatible when tables were created before UTM columns existed.
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_source TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_medium TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_campaign TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_content TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_term TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_payload TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_utm_at TIMESTAMP")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_source TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_medium TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_campaign TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_content TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_term TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_payload TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_utm_at TIMESTAMP")

        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS utm_source TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS utm_medium TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS utm_campaign TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS utm_content TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS utm_term TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS utm_payload TEXT NOT NULL DEFAULT ''")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_utm_source ON payments(utm_source)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_utm_campaign ON payments(utm_campaign)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        await conn.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS actor TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS context_order_id TEXT NOT NULL DEFAULT ''")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_order_id ON transactions(context_order_id)")
        # Recurrent payments support
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS rebill_id TEXT NOT NULL DEFAULT ''")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS is_recurrent BOOLEAN NOT NULL DEFAULT FALSE")

        await conn.execute("CREATE TABLE IF NOT EXISTS utm_touches ("
                           "id BIGSERIAL PRIMARY KEY,"
                           "tg_id BIGINT NOT NULL,"
                           "touch_type TEXT NOT NULL DEFAULT 'start',"
                           "source TEXT NOT NULL DEFAULT '',"
                           "medium TEXT NOT NULL DEFAULT '',"
                           "campaign TEXT NOT NULL DEFAULT '',"
                           "content TEXT NOT NULL DEFAULT '',"
                           "term TEXT NOT NULL DEFAULT '',"
                           "payload TEXT NOT NULL DEFAULT '',"
                           "raw_start_arg TEXT NOT NULL DEFAULT '',"
                           "created_at TIMESTAMP NOT NULL DEFAULT NOW()"
                           ")")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_touches_tg_id ON utm_touches(tg_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_touches_created_at ON utm_touches(created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_touches_source ON utm_touches(source)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_touches_campaign ON utm_touches(campaign)")

        # Broadcasts
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS broadcasts ("
            "id BIGSERIAL PRIMARY KEY,"
            "title TEXT NOT NULL DEFAULT '',"
            "text TEXT NOT NULL DEFAULT '',"
            "parse_mode TEXT NOT NULL DEFAULT 'HTML',"
            "media_type TEXT NOT NULL DEFAULT '',"
            "media_file_id TEXT NOT NULL DEFAULT '',"
            "media_url TEXT NOT NULL DEFAULT '',"
            "buttons_json TEXT NOT NULL DEFAULT '[]',"
            "audience_json TEXT NOT NULL DEFAULT '{}',"
            "audience_size INTEGER NOT NULL DEFAULT 0,"
            "schedule_at TIMESTAMP,"
            "status TEXT NOT NULL DEFAULT 'draft',"
            "sent_count INTEGER NOT NULL DEFAULT 0,"
            "failed_count INTEGER NOT NULL DEFAULT 0,"
            "blocked_count INTEGER NOT NULL DEFAULT 0,"
            "created_by TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW(),"
            "started_at TIMESTAMP,"
            "finished_at TIMESTAMP"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bc_status ON broadcasts(status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bc_schedule ON broadcasts(schedule_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bc_created_at ON broadcasts(created_at)")

        await conn.execute(
            "CREATE TABLE IF NOT EXISTS broadcast_deliveries ("
            "id BIGSERIAL PRIMARY KEY,"
            "broadcast_id BIGINT NOT NULL,"
            "tg_id BIGINT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'pending',"
            "error TEXT NOT NULL DEFAULT '',"
            "attempts INTEGER NOT NULL DEFAULT 0,"
            "sent_at TIMESTAMP,"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW(),"
            "UNIQUE (broadcast_id, tg_id)"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bcd_broadcast ON broadcast_deliveries(broadcast_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bcd_status ON broadcast_deliveries(status)")

        # CRM: tags and notes
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS user_tags ("
            "id BIGSERIAL PRIMARY KEY,"
            "tg_id BIGINT NOT NULL,"
            "tag TEXT NOT NULL,"
            "created_by TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW(),"
            "UNIQUE (tg_id, tag)"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ut_tg_id ON user_tags(tg_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ut_tag ON user_tags(tag)")

        await conn.execute(
            "CREATE TABLE IF NOT EXISTS user_notes ("
            "id BIGSERIAL PRIMARY KEY,"
            "tg_id BIGINT NOT NULL,"
            "note TEXT NOT NULL DEFAULT '',"
            "created_by TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW()"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_un_tg_id ON user_notes(tg_id)")

        # Admin audit log
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS admin_audit_log ("
            "id BIGSERIAL PRIMARY KEY,"
            "admin_user TEXT NOT NULL DEFAULT '',"
            "action TEXT NOT NULL,"
            "target TEXT NOT NULL DEFAULT '',"
            "details TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW()"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON admin_audit_log(created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_admin ON admin_audit_log(admin_user)")

        # Lifecycle rules — automated triggers that send messages
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS lifecycle_rules ("
            "id BIGSERIAL PRIMARY KEY,"
            "name TEXT NOT NULL DEFAULT '',"
            "trigger_type TEXT NOT NULL,"
            "trigger_json TEXT NOT NULL DEFAULT '{}',"
            "message_text TEXT NOT NULL DEFAULT '',"
            "parse_mode TEXT NOT NULL DEFAULT 'HTML',"
            "cooldown_days INTEGER NOT NULL DEFAULT 7,"
            "enabled BOOLEAN NOT NULL DEFAULT TRUE,"
            "last_run_at TIMESTAMP,"
            "fired_count INTEGER NOT NULL DEFAULT 0,"
            "created_by TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW(),"
            "updated_at TIMESTAMP NOT NULL DEFAULT NOW()"
            ")"
        )
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS lifecycle_fires ("
            "id BIGSERIAL PRIMARY KEY,"
            "rule_id BIGINT NOT NULL,"
            "tg_id BIGINT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'sent',"
            "error TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW()"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_lcf_rule ON lifecycle_fires(rule_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_lcf_tg ON lifecycle_fires(tg_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_lcf_created ON lifecycle_fires(created_at)")

        # Manual payments — revenue from outside the bot (cash, external invoice, etc.)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS manual_payments ("
            "id BIGSERIAL PRIMARY KEY,"
            "tg_id BIGINT NOT NULL,"
            "amount_rub BIGINT NOT NULL,"
            "note TEXT NOT NULL DEFAULT '',"
            "created_by TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW()"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_payments_tg_id ON manual_payments(tg_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_payments_created ON manual_payments(created_at)")

        # Tier outreach — manual contact log for S-tier users worked by managers.
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS tier_outreach ("
            "id BIGSERIAL PRIMARY KEY,"
            "tg_id BIGINT NOT NULL,"
            "tier TEXT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'todo',"
            "assigned_to TEXT NOT NULL DEFAULT '',"
            "note TEXT NOT NULL DEFAULT '',"
            "contacted_at TIMESTAMP,"
            "updated_at TIMESTAMP NOT NULL DEFAULT NOW(),"
            "UNIQUE (tg_id, tier)"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tier_outreach_status ON tier_outreach(status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tier_outreach_tier ON tier_outreach(tier)")

        # Seed core team into admins table by username — runs on every startup,
        # idempotent. Anyone in this list is filtered out of CRM/clients views.
        await conn.execute(
            "INSERT INTO admins (tg_id, username) "
            "SELECT u.tg_id, LOWER(u.username) FROM users u "
            "WHERE LOWER(u.username) = ANY($1::TEXT[]) "
            "ON CONFLICT (tg_id) DO NOTHING",
            ["whoistvoidiller", "vilebody", "nikitaimpulse"],
        )

        # Tier system: aggregator view + tier classifier view.
        # Recreate on every startup so refining the CASE WHEN in this file is enough — no separate migration.
        await conn.execute("DROP VIEW IF EXISTS user_tiers CASCADE")
        await conn.execute("DROP VIEW IF EXISTS user_signals CASCADE")
        await conn.execute(
            """
            CREATE VIEW user_signals AS
            SELECT
              u.tg_id,
              u.username,
              u.credits,
              u.created_at,
              u.updated_at,
              COALESCE(NULLIF(u.source, ''), NULLIF(u.first_utm_source, ''), '(direct)') AS cohort,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'subscription_ok') AS subscribed,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'audio_uploaded') AS audio_uploaded,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'generation_started') AS generation_started,
              (SELECT COUNT(*) FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'generation_done')::INT AS gens_done,
              (SELECT a.detail FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'rate_video' ORDER BY a.created_at DESC LIMIT 1) AS last_rating,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'sales_pitch') AS viewed_pitch,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'view_packages') AS viewed_packages_list,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'select_package') AS viewed_package_details,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event IN ('purchase_intent', 'purchase_intent_recurrent')) AS purchase_intent,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') AS has_purchase,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '5') AS bought_trial,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '15') AS bought_blast,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '30') AS bought_glow,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '50') AS bought_impulse,
              (SELECT s.package FROM subscriptions s WHERE s.tg_id = u.tg_id AND s.status = 'active' ORDER BY s.id DESC LIMIT 1) AS active_subscription_pkg,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'survey_opened') AS feedback_form_clicked,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'survey_done') AS feedback_form_filled,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'referral_sent') AS referral_made,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'admin_dm') AS manager_contacted,
              (SELECT MAX(a.created_at) FROM activity_log a WHERE a.tg_id = u.tg_id) AS last_active_at,
              (SELECT COALESCE(SUM(amount_rub), 0) FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') AS revenue_bot,
              (SELECT COALESCE(SUM(amount_rub), 0) FROM manual_payments mp WHERE mp.tg_id = u.tg_id) AS revenue_manual
            FROM users u
            WHERE u.tg_id NOT IN (SELECT tg_id FROM admins)
            """
        )
        await conn.execute(
            """
            CREATE VIEW user_tiers AS
            SELECT us.*,
              CASE
                -- S tier: hot, manual outreach (highest priority)
                WHEN us.gens_done >= 2 AND us.last_rating = 'high' AND us.feedback_form_filled THEN 'S1'
                WHEN us.feedback_form_filled AND us.last_rating = 'high' AND NOT us.has_purchase THEN 'S3'
                WHEN us.viewed_package_details AND NOT us.has_purchase THEN 'S2'
                -- P tier: special segments (paid + intent), checked before A/B
                WHEN us.active_subscription_pkg = '15' AND NOT us.bought_glow AND NOT us.bought_impulse THEN 'P11'
                WHEN us.bought_trial AND NOT us.bought_glow AND NOT us.bought_impulse THEN 'P10'
                WHEN us.referral_made AND NOT us.has_purchase THEN 'P12'
                WHEN us.purchase_intent AND NOT us.has_purchase THEN 'P14'
                -- A tier: warm, auto with personalization
                WHEN us.gens_done >= 3 AND us.last_rating = 'high' AND NOT us.has_purchase THEN 'A1'
                WHEN us.gens_done = 1 AND us.last_rating = 'high' THEN 'A2'
                -- D tier: old cohort (>30d since signup)
                WHEN us.created_at < NOW() - INTERVAL '30 days' AND us.last_rating = 'high' AND NOT us.has_purchase THEN 'D1'
                WHEN us.created_at < NOW() - INTERVAL '30 days' AND us.viewed_package_details AND NOT us.has_purchase THEN 'D2'
                -- B tier: medium, segmented mass
                WHEN us.last_rating = 'mid_low' AND NOT us.has_purchase THEN 'B2'
                WHEN us.last_rating = 'low' THEN 'B3'
                WHEN us.gens_done >= 1 AND us.last_rating IS NULL THEN 'B1'
                -- C tier: cold, minimum effort
                WHEN us.audio_uploaded AND NOT us.generation_started THEN 'C2'
                WHEN NOT us.subscribed THEN 'C1'
                ELSE NULL
              END AS tier
            FROM user_signals us
            """
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def _pool_or_fail(self) -> asyncpg.Pool:
        assert self._pool is not None, "CreditsDB not initialized"
        return self._pool

    # Users

    async def ensure_user(self, tg_id: int, username: str = "") -> bool:
        clean = username.lstrip("@").lower()
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO users (tg_id, username) VALUES ($1, $2) "
                "ON CONFLICT (tg_id) DO NOTHING RETURNING tg_id",
                int(tg_id),
                clean,
            )
            is_new = row is not None
            if clean:
                await conn.execute(
                    "UPDATE users SET username = $1, updated_at = NOW() WHERE tg_id = $2",
                    clean,
                    int(tg_id),
                )
            return bool(is_new)

    async def has_paid(self, tg_id: int) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM transactions WHERE tg_id = $1 AND reason = ANY($2::TEXT[]) LIMIT 1",
                int(tg_id),
                list(_PAID_REASONS),
            )
            return row is not None

    async def has_initial_grant(self, tg_id: int) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM transactions WHERE tg_id = $1 AND reason = 'initial_grant' LIMIT 1",
                int(tg_id),
            )
            return row is not None

    async def get_balance(self, tg_id: int) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            bal = await conn.fetchval("SELECT credits FROM users WHERE tg_id = $1", int(tg_id))
            return int(bal) if bal is not None else 0

    async def add_credits(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        admin_note: str = "",
        *,
        actor: str = "",
        order_id: str = "",
    ) -> int:
        pool = self._pool_or_fail()
        clean_actor = _norm_text(actor, max_len=64)
        clean_order_id = _norm_text(order_id, max_len=128)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO users (tg_id, username) VALUES ($1, '') ON CONFLICT (tg_id) DO NOTHING",
                    int(tg_id),
                )
                balance_before = await conn.fetchval(
                    "SELECT credits FROM users WHERE tg_id = $1 FOR UPDATE",
                    int(tg_id),
                )
                before = int(balance_before or 0)
                requested_delta = int(amount)
                after = max(0, before + requested_delta)
                applied_delta = after - before
                await conn.execute(
                    "UPDATE users SET credits = $1, updated_at = NOW() WHERE tg_id = $2",
                    int(after),
                    int(tg_id),
                )
                await conn.execute(
                    "INSERT INTO transactions (tg_id, amount, reason, admin_note, actor, context_order_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    int(tg_id),
                    int(applied_delta),
                    str(reason or ""),
                    str(admin_note or ""),
                    clean_actor,
                    clean_order_id,
                )
                return int(after)

    async def deduct_credit(self, tg_id: int) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "UPDATE users SET credits = credits - 1, updated_at = NOW() "
                    "WHERE tg_id = $1 AND credits >= 1 RETURNING tg_id",
                    int(tg_id),
                )
                if row is None:
                    return False
                await conn.execute(
                    "INSERT INTO transactions (tg_id, amount, reason) VALUES ($1, -1, 'generation')",
                    int(tg_id),
                )
                return True

    # UTM

    async def record_utm_touch(self, tg_id: int, *, raw_start_arg: str = "", utm: Optional[Dict[str, str]] = None) -> None:
        clean = _clean_utm(utm)
        has_any = any(clean[k] for k in _UTM_KEYS)
        raw_arg = _norm_text(raw_start_arg, max_len=512)
        payload = clean.get("payload") or raw_arg

        if not has_any and not raw_arg:
            return

        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO users (tg_id, username) VALUES ($1, '') ON CONFLICT (tg_id) DO NOTHING",
                    int(tg_id),
                )

                await conn.execute(
                    "INSERT INTO utm_touches (tg_id, touch_type, source, medium, campaign, content, term, payload, raw_start_arg) "
                    "VALUES ($1, 'start', $2, $3, $4, $5, $6, $7, $8)",
                    int(tg_id),
                    clean["source"],
                    clean["medium"],
                    clean["campaign"],
                    clean["content"],
                    clean["term"],
                    payload,
                    raw_arg,
                )

                if has_any:
                    await conn.execute(
                        "UPDATE users SET "
                        "last_utm_source = $1, last_utm_medium = $2, last_utm_campaign = $3, "
                        "last_utm_content = $4, last_utm_term = $5, last_utm_payload = $6, "
                        "last_utm_at = NOW(), updated_at = NOW(), "
                        "first_utm_source = CASE WHEN first_utm_at IS NULL THEN $1 ELSE first_utm_source END, "
                        "first_utm_medium = CASE WHEN first_utm_at IS NULL THEN $2 ELSE first_utm_medium END, "
                        "first_utm_campaign = CASE WHEN first_utm_at IS NULL THEN $3 ELSE first_utm_campaign END, "
                        "first_utm_content = CASE WHEN first_utm_at IS NULL THEN $4 ELSE first_utm_content END, "
                        "first_utm_term = CASE WHEN first_utm_at IS NULL THEN $5 ELSE first_utm_term END, "
                        "first_utm_payload = CASE WHEN first_utm_at IS NULL THEN $6 ELSE first_utm_payload END, "
                        "first_utm_at = COALESCE(first_utm_at, NOW()) "
                        "WHERE tg_id = $7",
                        clean["source"],
                        clean["medium"],
                        clean["campaign"],
                        clean["content"],
                        clean["term"],
                        payload,
                        int(tg_id),
                    )

    async def get_last_utm(self, tg_id: int) -> Dict[str, str]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_utm_source, last_utm_medium, last_utm_campaign, last_utm_content, last_utm_term, last_utm_payload "
                "FROM users WHERE tg_id = $1",
                int(tg_id),
            )
        if not row:
            return {"source": "", "medium": "", "campaign": "", "content": "", "term": "", "payload": ""}
        return {
            "source": str(row["last_utm_source"] or ""),
            "medium": str(row["last_utm_medium"] or ""),
            "campaign": str(row["last_utm_campaign"] or ""),
            "content": str(row["last_utm_content"] or ""),
            "term": str(row["last_utm_term"] or ""),
            "payload": str(row["last_utm_payload"] or ""),
        }

    async def get_utm_summary(self, limit: int = 100) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "WITH starts AS ("
                "  SELECT "
                "    COALESCE(NULLIF(source, ''), '(none)') AS source,"
                "    COALESCE(NULLIF(medium, ''), '(none)') AS medium,"
                "    COALESCE(NULLIF(campaign, ''), '(none)') AS campaign,"
                "    COALESCE(NULLIF(content, ''), '') AS content,"
                "    COALESCE(NULLIF(term, ''), '') AS term,"
                "    COUNT(*)::BIGINT AS starts_count "
                "  FROM utm_touches GROUP BY 1,2,3,4,5"
                "),"
                "paid AS ("
                "  SELECT "
                "    COALESCE(NULLIF(utm_source, ''), '(none)') AS source,"
                "    COALESCE(NULLIF(utm_medium, ''), '(none)') AS medium,"
                "    COALESCE(NULLIF(utm_campaign, ''), '(none)') AS campaign,"
                "    COALESCE(NULLIF(utm_content, ''), '') AS content,"
                "    COALESCE(NULLIF(utm_term, ''), '') AS term,"
                "    COUNT(*)::BIGINT AS paid_orders,"
                "    COALESCE(SUM(amount_rub), 0)::BIGINT AS revenue_rub "
                "  FROM payments WHERE status = 'CONFIRMED' GROUP BY 1,2,3,4,5"
                ") "
                "SELECT "
                "  COALESCE(s.source, p.source) AS source,"
                "  COALESCE(s.medium, p.medium) AS medium,"
                "  COALESCE(s.campaign, p.campaign) AS campaign,"
                "  COALESCE(s.content, p.content) AS content,"
                "  COALESCE(s.term, p.term) AS term,"
                "  COALESCE(s.starts_count, 0)::BIGINT AS starts_count,"
                "  COALESCE(p.paid_orders, 0)::BIGINT AS paid_orders,"
                "  COALESCE(p.revenue_rub, 0)::BIGINT AS revenue_rub "
                "FROM starts s FULL OUTER JOIN paid p USING (source, medium, campaign, content, term) "
                "ORDER BY starts_count DESC, paid_orders DESC, revenue_rub DESC "
                "LIMIT $1",
                int(limit),
            )
        return [
            {
                "source": str(r["source"] or ""),
                "medium": str(r["medium"] or ""),
                "campaign": str(r["campaign"] or ""),
                "content": str(r["content"] or ""),
                "term": str(r["term"] or ""),
                "starts_count": int(r["starts_count"]),
                "paid_orders": int(r["paid_orders"]),
                "revenue_rub": int(r["revenue_rub"]),
            }
            for r in rows
        ]

    # Queries for admin panel

    async def list_users(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tg_id, username, credits, created_at, updated_at, "
                "source, first_utm_source, first_utm_campaign, last_utm_source, last_utm_campaign "
                "FROM users ORDER BY updated_at DESC, tg_id DESC LIMIT $1 OFFSET $2",
                int(limit),
                int(offset),
            )
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "credits": int(r["credits"]),
                "created_at": _fmt_ts(r["created_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
                "source": str(r["source"] or ""),
                "first_utm_source": str(r["first_utm_source"] or ""),
                "first_utm_campaign": str(r["first_utm_campaign"] or ""),
                "last_utm_source": str(r["last_utm_source"] or ""),
                "last_utm_campaign": str(r["last_utm_campaign"] or ""),
            }
            for r in rows
        ]

    async def count_users(self) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT COUNT(*) FROM users")
            return int(row or 0)

    async def count_activity(self) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM activity_log") or 0)

    async def count_transactions(self) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM transactions") or 0)

    async def count_payments(self) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM payments") or 0)

    async def confirmed_payments_summary(self) -> Dict[str, int]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "COALESCE(COUNT(*), 0)::BIGINT AS orders_count, "
                "COALESCE(SUM(amount_rub), 0)::BIGINT AS revenue_rub "
                "FROM payments WHERE status = 'CONFIRMED'"
            )
        if row is None:
            return {"orders_count": 0, "revenue_rub": 0}
        return {
            "orders_count": int(row["orders_count"] or 0),
            "revenue_rub": int(row["revenue_rub"] or 0),
        }

    async def payments_status_summary(self) -> Dict[str, int]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "COALESCE(COUNT(*) FILTER (WHERE status = 'CONFIRMED'), 0)::BIGINT AS confirmed_orders, "
                "COALESCE(SUM(amount_rub) FILTER (WHERE status = 'CONFIRMED'), 0)::BIGINT AS confirmed_revenue_rub, "
                "COALESCE(COUNT(*) FILTER (WHERE status = 'AUTHORIZED'), 0)::BIGINT AS authorized_orders, "
                "COALESCE(SUM(amount_rub) FILTER (WHERE status = 'AUTHORIZED'), 0)::BIGINT AS authorized_revenue_rub "
                "FROM payments"
            )
        if row is None:
            return {
                "confirmed_orders": 0,
                "confirmed_revenue_rub": 0,
                "authorized_orders": 0,
                "authorized_revenue_rub": 0,
                "visible_orders": 0,
                "visible_revenue_rub": 0,
            }
        confirmed_orders = int(row["confirmed_orders"] or 0)
        confirmed_revenue_rub = int(row["confirmed_revenue_rub"] or 0)
        authorized_orders = int(row["authorized_orders"] or 0)
        authorized_revenue_rub = int(row["authorized_revenue_rub"] or 0)
        return {
            "confirmed_orders": confirmed_orders,
            "confirmed_revenue_rub": confirmed_revenue_rub,
            "authorized_orders": authorized_orders,
            "authorized_revenue_rub": authorized_revenue_rub,
            "visible_orders": confirmed_orders + authorized_orders,
            "visible_revenue_rub": confirmed_revenue_rub + authorized_revenue_rub,
        }

    async def period_stats(self, days: int) -> Dict[str, int]:
        period_days = max(1, min(int(days), 3650))
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "WITH cutoff AS (SELECT NOW() - ($1::INT * INTERVAL '1 day') AS ts) "
                "SELECT "
                "  (SELECT COALESCE(COUNT(*), 0)::BIGINT FROM users u, cutoff c WHERE u.created_at >= c.ts) AS users_new, "
                "  (SELECT COALESCE(COUNT(DISTINCT a.tg_id), 0)::BIGINT FROM activity_log a, cutoff c WHERE a.event = 'start' AND a.created_at >= c.ts) AS starts_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT a.tg_id), 0)::BIGINT FROM activity_log a, cutoff c WHERE a.event = 'generation_started' AND a.created_at >= c.ts) AS generation_started_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT a.tg_id), 0)::BIGINT FROM activity_log a, cutoff c WHERE a.event = 'generation_done' AND a.created_at >= c.ts) AS generation_done_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT a.tg_id), 0)::BIGINT FROM activity_log a, cutoff c WHERE a.event = 'generation_failed' AND a.created_at >= c.ts) AS generation_failed_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT a.tg_id), 0)::BIGINT FROM activity_log a, cutoff c WHERE a.event = 'purchase_intent' AND a.created_at >= c.ts) AS purchase_intent_users, "
                "  (SELECT COALESCE(COUNT(*), 0)::BIGINT FROM payments p, cutoff c WHERE p.status = 'CONFIRMED' AND p.created_at >= c.ts) AS paid_orders, "
                "  (SELECT COALESCE(SUM(p.amount_rub), 0)::BIGINT FROM payments p, cutoff c WHERE p.status = 'CONFIRMED' AND p.created_at >= c.ts) AS revenue_rub, "
                "  (SELECT COALESCE(COUNT(DISTINCT a.tg_id), 0)::BIGINT FROM activity_log a, cutoff c WHERE a.event = 'bot_blocked' AND a.created_at >= c.ts) AS bot_blocked_users",
                period_days,
            )
        if row is None:
            return {
                "days": period_days,
                "users_new": 0,
                "starts_users": 0,
                "generation_started_users": 0,
                "generation_done_users": 0,
                "generation_failed_users": 0,
                "purchase_intent_users": 0,
                "paid_orders": 0,
                "revenue_rub": 0,
                "bot_blocked_users": 0,
            }
        return {
            "days": period_days,
            "users_new": int(row["users_new"] or 0),
            "starts_users": int(row["starts_users"] or 0),
            "generation_started_users": int(row["generation_started_users"] or 0),
            "generation_done_users": int(row["generation_done_users"] or 0),
            "generation_failed_users": int(row["generation_failed_users"] or 0),
            "purchase_intent_users": int(row["purchase_intent_users"] or 0),
            "paid_orders": int(row["paid_orders"] or 0),
            "revenue_rub": int(row["revenue_rub"] or 0),
            "bot_blocked_users": int(row["bot_blocked_users"] or 0),
        }

    async def period_stats_range(self, date_from: datetime, date_to: datetime) -> Dict[str, int]:
        # Strip tzinfo — DB columns are naive TIMESTAMP
        df = date_from.replace(tzinfo=None) if date_from.tzinfo else date_from
        dt = date_to.replace(tzinfo=None) if date_to.tzinfo else date_to
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "  (SELECT COALESCE(COUNT(*), 0)::BIGINT FROM users WHERE created_at >= $1 AND created_at < $2) AS users_new, "
                "  (SELECT COALESCE(COUNT(DISTINCT tg_id), 0)::BIGINT FROM activity_log WHERE event = 'start' AND created_at >= $1 AND created_at < $2) AS starts_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT tg_id), 0)::BIGINT FROM activity_log WHERE event = 'generation_started' AND created_at >= $1 AND created_at < $2) AS generation_started_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT tg_id), 0)::BIGINT FROM activity_log WHERE event = 'generation_done' AND created_at >= $1 AND created_at < $2) AS generation_done_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT tg_id), 0)::BIGINT FROM activity_log WHERE event = 'generation_failed' AND created_at >= $1 AND created_at < $2) AS generation_failed_users, "
                "  (SELECT COALESCE(COUNT(DISTINCT tg_id), 0)::BIGINT FROM activity_log WHERE event = 'purchase_intent' AND created_at >= $1 AND created_at < $2) AS purchase_intent_users, "
                "  (SELECT COALESCE(COUNT(*), 0)::BIGINT FROM payments WHERE status = 'CONFIRMED' AND created_at >= $1 AND created_at < $2) AS paid_orders, "
                "  (SELECT COALESCE(SUM(amount_rub), 0)::BIGINT FROM payments WHERE status = 'CONFIRMED' AND created_at >= $1 AND created_at < $2) AS revenue_rub, "
                "  (SELECT COALESCE(COUNT(DISTINCT tg_id), 0)::BIGINT FROM activity_log WHERE event = 'bot_blocked' AND created_at >= $1 AND created_at < $2) AS bot_blocked_users",
                df,
                dt,
            )
        empty = {
            "users_new": 0, "starts_users": 0,
            "generation_started_users": 0, "generation_done_users": 0,
            "generation_failed_users": 0, "purchase_intent_users": 0,
            "paid_orders": 0, "revenue_rub": 0,
            "bot_blocked_users": 0,
        }
        if row is None:
            return empty
        return {k: int(row[k] or 0) for k in empty}

    async def get_user(self, tg_id: int) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT tg_id, username, credits, created_at, updated_at, "
                "source, "
                "first_utm_source, first_utm_medium, first_utm_campaign, first_utm_content, first_utm_term, first_utm_payload, first_utm_at, "
                "last_utm_source, last_utm_medium, last_utm_campaign, last_utm_content, last_utm_term, last_utm_payload, last_utm_at "
                "FROM users WHERE tg_id = $1",
                int(tg_id),
            )
        if not r:
            return None
        return {
            "tg_id": int(r["tg_id"]),
            "username": str(r["username"] or ""),
            "credits": int(r["credits"]),
            "created_at": _fmt_ts(r["created_at"]),
            "updated_at": _fmt_ts(r["updated_at"]),
            "source": str(r["source"] or ""),
            "first_utm_source": str(r["first_utm_source"] or ""),
            "first_utm_medium": str(r["first_utm_medium"] or ""),
            "first_utm_campaign": str(r["first_utm_campaign"] or ""),
            "first_utm_content": str(r["first_utm_content"] or ""),
            "first_utm_term": str(r["first_utm_term"] or ""),
            "first_utm_payload": str(r["first_utm_payload"] or ""),
            "first_utm_at": _fmt_ts(r["first_utm_at"]),
            "last_utm_source": str(r["last_utm_source"] or ""),
            "last_utm_medium": str(r["last_utm_medium"] or ""),
            "last_utm_campaign": str(r["last_utm_campaign"] or ""),
            "last_utm_content": str(r["last_utm_content"] or ""),
            "last_utm_term": str(r["last_utm_term"] or ""),
            "last_utm_payload": str(r["last_utm_payload"] or ""),
            "last_utm_at": _fmt_ts(r["last_utm_at"]),
        }

    async def get_transactions(self, tg_id: int = 0, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            if tg_id:
                rows = await conn.fetch(
                    "SELECT id, tg_id, amount, reason, admin_note, actor, context_order_id, created_at "
                    "FROM transactions WHERE tg_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3",
                    int(tg_id),
                    int(limit),
                    int(offset),
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, tg_id, amount, reason, admin_note, actor, context_order_id, created_at "
                    "FROM transactions ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2",
                    int(limit),
                    int(offset),
                )
        return [
            {
                "id": int(r["id"]),
                "tg_id": int(r["tg_id"]),
                "amount": int(r["amount"]),
                "reason": str(r["reason"] or ""),
                "admin_note": str(r["admin_note"] or ""),
                "actor": str(r["actor"] or ""),
                "order_id": str(r["context_order_id"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    # Admin management

    async def is_admin(self, tg_id: int) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT 1 FROM admins WHERE tg_id = $1", int(tg_id))
            return row is not None

    async def add_admin(self, tg_id: int, username: str = "") -> None:
        pool = self._pool_or_fail()
        clean = username.lstrip("@").lower()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO admins (tg_id, username) VALUES ($1, $2) ON CONFLICT (tg_id) DO NOTHING",
                int(tg_id),
                clean,
            )

    async def remove_admin(self, tg_id: int) -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM admins WHERE tg_id = $1", int(tg_id))

    async def list_admins(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT tg_id, username, added_at FROM admins ORDER BY added_at")
        return [
            {"tg_id": int(r["tg_id"]), "username": str(r["username"] or ""), "added_at": _fmt_ts(r["added_at"])}
            for r in rows
        ]

    # Activity log

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO activity_log (tg_id, event, detail) VALUES ($1, $2, $3)",
                int(tg_id),
                str(event or ""),
                str(detail or ""),
            )

    async def get_activity(self, tg_id: int = 0, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            if tg_id:
                rows = await conn.fetch(
                    "SELECT id, tg_id, event, detail, created_at "
                    "FROM activity_log WHERE tg_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3",
                    int(tg_id),
                    int(limit),
                    int(offset),
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, tg_id, event, detail, created_at "
                    "FROM activity_log ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2",
                    int(limit),
                    int(offset),
                )
        return [
            {
                "id": int(r["id"]),
                "tg_id": int(r["tg_id"]),
                "event": str(r["event"] or ""),
                "detail": str(r["detail"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    # Payments

    async def create_payment(
        self,
        order_id: str,
        tg_id: int,
        amount_rub: int,
        package: str,
        utm: Optional[Dict[str, str]] = None,
    ) -> None:
        clean = _clean_utm(utm)
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO payments (order_id, tg_id, amount_rub, package, utm_source, utm_medium, utm_campaign, utm_content, utm_term, utm_payload) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                str(order_id),
                int(tg_id),
                int(amount_rub),
                str(package or ""),
                clean["source"],
                clean["medium"],
                clean["campaign"],
                clean["content"],
                clean["term"],
                clean["payload"],
            )

    async def update_payment_status(self, order_id: str, status: str, payment_id: str = "") -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            try:
                if payment_id:
                    tag = await conn.execute(
                        "UPDATE payments SET status = $1, payment_id = $2, updated_at = NOW() WHERE order_id = $3",
                        str(status),
                        str(payment_id),
                        str(order_id),
                    )
                else:
                    tag = await conn.execute(
                        "UPDATE payments SET status = $1, updated_at = NOW() WHERE order_id = $2",
                        str(status),
                        str(order_id),
                    )
                return _rowcount_from_tag(tag) > 0
            except Exception:
                return False

    async def get_payment(self, order_id: str) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, "
                "utm_source, utm_medium, utm_campaign, utm_content, utm_term, utm_payload, "
                "created_at, updated_at "
                "FROM payments WHERE order_id = $1",
                str(order_id),
            )
        if not r:
            return None
        return {
            "id": int(r["id"]),
            "order_id": str(r["order_id"]),
            "tg_id": int(r["tg_id"]),
            "amount_rub": int(r["amount_rub"]),
            "package": str(r["package"] or ""),
            "status": str(r["status"] or ""),
            "payment_id": str(r["payment_id"] or ""),
            "utm_source": str(r["utm_source"] or ""),
            "utm_medium": str(r["utm_medium"] or ""),
            "utm_campaign": str(r["utm_campaign"] or ""),
            "utm_content": str(r["utm_content"] or ""),
            "utm_term": str(r["utm_term"] or ""),
            "utm_payload": str(r["utm_payload"] or ""),
            "created_at": _fmt_ts(r["created_at"]),
            "updated_at": _fmt_ts(r["updated_at"]),
        }

    async def get_payments(self, tg_id: int = 0, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            if tg_id:
                rows = await conn.fetch(
                    "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, "
                    "utm_source, utm_medium, utm_campaign, created_at "
                    "FROM payments WHERE tg_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3",
                    int(tg_id),
                    int(limit),
                    int(offset),
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, "
                    "utm_source, utm_medium, utm_campaign, created_at "
                    "FROM payments ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2",
                    int(limit),
                    int(offset),
                )
        return [
            {
                "id": int(r["id"]),
                "order_id": str(r["order_id"]),
                "tg_id": int(r["tg_id"]),
                "amount_rub": int(r["amount_rub"]),
                "package": str(r["package"] or ""),
                "status": str(r["status"] or ""),
                "payment_id": str(r["payment_id"] or ""),
                "utm_source": str(r["utm_source"] or ""),
                "utm_medium": str(r["utm_medium"] or ""),
                "utm_campaign": str(r["utm_campaign"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def get_pending_payments(self) -> List[Dict[str, Any]]:
        """Return all payments with status 'pending' (not yet confirmed/rejected)."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, is_recurrent, created_at "
                "FROM payments WHERE status = 'pending' ORDER BY created_at ASC",
            )
            return [
                {"id": r["id"], "order_id": r["order_id"], "tg_id": r["tg_id"],
                 "amount_rub": r["amount_rub"], "package": r["package"], "status": r["status"],
                 "payment_id": r["payment_id"], "is_recurrent": bool(r["is_recurrent"]),
                 "created_at": r["created_at"]}
                for r in rows
            ]

    async def save_rebill_id(self, tg_id: int, rebill_id: str) -> None:
        """Save RebillId for recurrent charges on the latest confirmed payment."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE payments SET rebill_id = $1 "
                "WHERE tg_id = $2 AND status = 'confirmed' AND rebill_id = '' "
                "ORDER BY updated_at DESC LIMIT 1",
                str(rebill_id),
                int(tg_id),
            )

    async def get_rebill_id(self, tg_id: int) -> Optional[str]:
        """Get the latest RebillId for a user (from their last recurrent parent payment)."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT rebill_id FROM payments "
                "WHERE tg_id = $1 AND rebill_id <> '' AND is_recurrent = TRUE AND status = 'confirmed' "
                "ORDER BY updated_at DESC LIMIT 1",
                int(tg_id),
            )
        return str(val) if val else None

    async def create_recurrent_payment(
        self,
        order_id: str,
        tg_id: int,
        amount_rub: int,
        package: str,
        utm: Optional[Dict[str, str]] = None,
    ) -> None:
        """Create a payment record marked as recurrent."""
        clean = _clean_utm(utm)
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO payments (order_id, tg_id, amount_rub, package, is_recurrent, "
                "utm_source, utm_medium, utm_campaign, utm_content, utm_term, utm_payload) "
                "VALUES ($1, $2, $3, $4, TRUE, $5, $6, $7, $8, $9, $10)",
                str(order_id),
                int(tg_id),
                int(amount_rub),
                str(package or ""),
                clean["source"],
                clean["medium"],
                clean["campaign"],
                clean["content"],
                clean["term"],
                clean["payload"],
            )

    async def update_rebill_id(self, order_id: str, rebill_id: str) -> None:
        """Set rebill_id on a specific payment order."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE payments SET rebill_id = $1, updated_at = NOW() WHERE order_id = $2",
                str(rebill_id),
                str(order_id),
            )

    async def is_payment_processed(self, payment_id: str, status: str) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM payments WHERE payment_id = $1 AND status = $2 LIMIT 1",
                str(payment_id),
                str(status),
            )
            return row is not None

    async def funnel_summary(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT t.event, COUNT(*) AS cnt "
                "FROM ("
                "  SELECT DISTINCT ON (tg_id) tg_id, event, id "
                "  FROM activity_log "
                "  ORDER BY tg_id, id DESC"
                ") AS t "
                "GROUP BY t.event "
                "ORDER BY cnt DESC"
            )
        return [{"event": str(r["event"] or ""), "count": int(r["cnt"])} for r in rows]

    async def rating_distribution(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT detail, COUNT(*)::BIGINT AS cnt "
                "FROM activity_log "
                "WHERE event = 'rate_video' AND detail <> '' "
                "GROUP BY detail ORDER BY cnt DESC"
            )
        return [{"rating": str(r["detail"] or ""), "count": int(r["cnt"])} for r in rows]

    async def rating_distribution_for_users(self, tg_ids: List[int]) -> List[Dict[str, Any]]:
        if not tg_ids:
            return []
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT detail, COUNT(*)::BIGINT AS cnt "
                "FROM activity_log "
                "WHERE event = 'rate_video' AND detail <> '' "
                "AND tg_id = ANY($1::BIGINT[]) "
                "GROUP BY detail ORDER BY cnt DESC",
                tg_ids,
            )
        return [{"rating": str(r["detail"] or ""), "count": int(r["cnt"])} for r in rows]

    async def funnel_reach_counts(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event, COUNT(DISTINCT tg_id)::BIGINT AS cnt "
                "FROM activity_log GROUP BY event ORDER BY cnt DESC"
            )
        return [{"event": str(r["event"] or ""), "count": int(r["cnt"])} for r in rows]

    async def search_users(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        q = str(query or "").strip().lstrip("@").lower()
        if not q:
            return []
        pool = self._pool_or_fail()
        like_q = f"%{q}%"
        async with pool.acquire() as conn:
            if q.isdigit():
                rows = await conn.fetch(
                    "SELECT tg_id, username, credits, created_at, updated_at, source, "
                    "first_utm_source, first_utm_campaign, last_utm_source, last_utm_campaign "
                    "FROM users "
                    "WHERE tg_id = $1 OR username ILIKE $2 OR source ILIKE $2 "
                    "OR first_utm_source ILIKE $2 OR first_utm_campaign ILIKE $2 "
                    "ORDER BY updated_at DESC, tg_id DESC LIMIT $3",
                    int(q),
                    like_q,
                    int(limit),
                )
            else:
                rows = await conn.fetch(
                    "SELECT tg_id, username, credits, created_at, updated_at, source, "
                    "first_utm_source, first_utm_campaign, last_utm_source, last_utm_campaign "
                    "FROM users "
                    "WHERE username ILIKE $1 OR source ILIKE $1 "
                    "OR first_utm_source ILIKE $1 OR first_utm_campaign ILIKE $1 "
                    "ORDER BY updated_at DESC, tg_id DESC LIMIT $2",
                    like_q,
                    int(limit),
                )
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "credits": int(r["credits"]),
                "created_at": _fmt_ts(r["created_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
                "source": str(r["source"] or ""),
                "first_utm_source": str(r["first_utm_source"] or ""),
                "first_utm_campaign": str(r["first_utm_campaign"] or ""),
                "last_utm_source": str(r["last_utm_source"] or ""),
                "last_utm_campaign": str(r["last_utm_campaign"] or ""),
            }
            for r in rows
        ]

    async def set_user_source(self, tg_id: int, source: str) -> None:
        src = _norm_text(source, max_len=128)
        if not src:
            return
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO users (tg_id, username) VALUES ($1, '') ON CONFLICT (tg_id) DO NOTHING",
                    int(tg_id),
                )
                await conn.execute(
                    "UPDATE users SET source = $1, updated_at = NOW() "
                    "WHERE tg_id = $2 AND (source = '' OR source IS NULL)",
                    src,
                    int(tg_id),
                )

    async def source_distribution(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT COALESCE(NULLIF(source, ''), NULLIF(first_utm_source, ''), '(direct)') AS src, "
                "COUNT(*)::BIGINT AS cnt "
                "FROM users GROUP BY src ORDER BY cnt DESC, src ASC"
            )
        return [{"source": str(r["src"] or ""), "count": int(r["cnt"])} for r in rows]

    async def get_user_source(self, tg_id: int) -> str:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT source, first_utm_source FROM users WHERE tg_id = $1",
                int(tg_id),
            )
        if not row:
            return ""
        direct = str(row["source"] or "").strip()
        if direct:
            return direct
        return str(row["first_utm_source"] or "").strip()

    async def users_by_source(self, source: str) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        src = str(source or "").strip()
        async with pool.acquire() as conn:
            if src == "(direct)":
                rows = await conn.fetch(
                    "SELECT tg_id, username, credits, created_at, updated_at, source "
                    "FROM users "
                    "WHERE (source = '' OR source IS NULL) "
                    "AND (first_utm_source = '' OR first_utm_source IS NULL) "
                    "ORDER BY created_at DESC",
                )
            else:
                rows = await conn.fetch(
                    "SELECT tg_id, username, credits, created_at, updated_at, source "
                    "FROM users "
                    "WHERE source = $1 OR first_utm_source = $1 "
                    "ORDER BY created_at DESC",
                    src,
                )
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "credits": int(r["credits"]),
                "created_at": _fmt_ts(r["created_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
                "source": str(r["source"] or ""),
            }
            for r in rows
        ]

    async def funnel_reach_counts_for_users(self, tg_ids: List[int]) -> List[Dict[str, Any]]:
        if not tg_ids:
            return []
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event, COUNT(DISTINCT tg_id)::BIGINT AS cnt "
                "FROM activity_log WHERE tg_id = ANY($1::BIGINT[]) "
                "GROUP BY event ORDER BY cnt DESC",
                tg_ids,
            )
        return [{"event": str(r["event"]), "count": int(r["cnt"])} for r in rows]

    async def revenue_for_users(self, tg_ids: List[int]) -> int:
        if not tg_ids:
            return 0
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(amount_rub), 0)::BIGINT FROM payments "
                "WHERE tg_id = ANY($1::BIGINT[]) AND status = 'CONFIRMED'",
                tg_ids,
            )
        return int(val or 0)

    async def revenue_breakdown_for_users(self, tg_ids: List[int]) -> Dict[str, int]:
        if not tg_ids:
            return {
                "confirmed_revenue_rub": 0,
                "authorized_revenue_rub": 0,
                "visible_revenue_rub": 0,
            }
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "COALESCE(SUM(amount_rub) FILTER (WHERE status = 'CONFIRMED'), 0)::BIGINT AS confirmed_revenue_rub, "
                "COALESCE(SUM(amount_rub) FILTER (WHERE status = 'AUTHORIZED'), 0)::BIGINT AS authorized_revenue_rub "
                "FROM payments WHERE tg_id = ANY($1::BIGINT[])",
                tg_ids,
            )
        if row is None:
            return {
                "confirmed_revenue_rub": 0,
                "authorized_revenue_rub": 0,
                "visible_revenue_rub": 0,
            }
        confirmed_revenue_rub = int(row["confirmed_revenue_rub"] or 0)
        authorized_revenue_rub = int(row["authorized_revenue_rub"] or 0)
        return {
            "confirmed_revenue_rub": confirmed_revenue_rub,
            "authorized_revenue_rub": authorized_revenue_rub,
            "visible_revenue_rub": confirmed_revenue_rub + authorized_revenue_rub,
        }

    # ── Subscriptions ────────────────────────────────────────────────

    async def create_subscription(
        self, tg_id: int, package: str, rebill_id: str, amount_rub: int,
    ) -> None:
        """Create an active subscription after first recurrent payment."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            # Cancel any existing active subscription for this user
            await conn.execute(
                "UPDATE subscriptions SET status = 'replaced', cancelled_at = NOW(), updated_at = NOW() "
                "WHERE tg_id = $1 AND status = 'active'",
                int(tg_id),
            )
            await conn.execute(
                "INSERT INTO subscriptions (tg_id, package, rebill_id, amount_rub) "
                "VALUES ($1, $2, $3, $4)",
                int(tg_id),
                str(package or ""),
                str(rebill_id),
                int(amount_rub),
            )

    async def get_active_subscription(self, tg_id: int) -> Optional[Dict[str, Any]]:
        """Get the active subscription for a user, if any."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, tg_id, package, rebill_id, amount_rub, status, "
                "next_charge_at, charge_retries, created_at, cancelled_at "
                "FROM subscriptions WHERE tg_id = $1 AND status = 'active' "
                "ORDER BY id DESC LIMIT 1",
                int(tg_id),
            )
        if not r:
            return None
        return {
            "id": int(r["id"]),
            "tg_id": int(r["tg_id"]),
            "package": str(r["package"] or ""),
            "rebill_id": str(r["rebill_id"] or ""),
            "amount_rub": int(r["amount_rub"]),
            "status": str(r["status"]),
            "next_charge_at": r["next_charge_at"],
            "charge_retries": int(r["charge_retries"]),
            "created_at": r["created_at"],
            "cancelled_at": r["cancelled_at"],
        }

    async def get_subscriptions_due(self) -> List[Dict[str, Any]]:
        """Get active subscriptions that are due for charging (next_charge_at <= NOW)."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, tg_id, package, rebill_id, amount_rub, charge_retries, next_charge_at "
                "FROM subscriptions "
                "WHERE status = 'active' AND next_charge_at <= NOW() "
                "ORDER BY next_charge_at ASC",
            )
        return [
            {
                "id": int(r["id"]),
                "tg_id": int(r["tg_id"]),
                "package": str(r["package"] or ""),
                "rebill_id": str(r["rebill_id"] or ""),
                "amount_rub": int(r["amount_rub"]),
                "charge_retries": int(r["charge_retries"]),
                "next_charge_at": r["next_charge_at"],
            }
            for r in rows
        ]

    async def subscription_charge_success(self, sub_id: int) -> None:
        """After successful charge: reset retries, push next_charge_at +30 days."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions "
                "SET next_charge_at = NOW() + INTERVAL '30 days', "
                "    charge_retries = 0, updated_at = NOW() "
                "WHERE id = $1",
                int(sub_id),
            )

    async def subscription_charge_failed(self, sub_id: int, max_retries: int = 3) -> str:
        """After failed charge: increment retries. If max reached, pause subscription.

        Returns new status: 'active' (will retry) or 'paused' (max retries hit).
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT charge_retries FROM subscriptions WHERE id = $1", int(sub_id),
            )
            retries = int(r["charge_retries"]) + 1 if r else 1
            if retries >= max_retries:
                await conn.execute(
                    "UPDATE subscriptions SET status = 'paused', charge_retries = $1, updated_at = NOW() "
                    "WHERE id = $2",
                    retries, int(sub_id),
                )
                return "paused"
            else:
                # Retry in 24 hours
                await conn.execute(
                    "UPDATE subscriptions "
                    "SET charge_retries = $1, next_charge_at = NOW() + INTERVAL '1 day', updated_at = NOW() "
                    "WHERE id = $2",
                    retries, int(sub_id),
                )
                return "active"

    async def cancel_subscription(self, tg_id: int) -> bool:
        """Cancel the active subscription for a user. Returns True if there was one."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            tag = await conn.execute(
                "UPDATE subscriptions SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW() "
                "WHERE tg_id = $1 AND status = 'active'",
                int(tg_id),
            )
            return _rowcount_from_tag(tag) > 0

    # ── Admin audit log ──────────────────────────────────────────────

    async def audit_log(self, admin_user: str, action: str, target: str = "", details: str = "") -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO admin_audit_log (admin_user, action, target, details) VALUES ($1, $2, $3, $4)",
                _norm_text(admin_user, max_len=64),
                _norm_text(action, max_len=64),
                _norm_text(target, max_len=128),
                str(details or "")[:2000],
            )

    async def get_audit_log(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, admin_user, action, target, details, created_at "
                "FROM admin_audit_log ORDER BY id DESC LIMIT $1 OFFSET $2",
                int(limit), int(offset),
            )
        return [
            {
                "id": int(r["id"]),
                "admin_user": str(r["admin_user"] or ""),
                "action": str(r["action"] or ""),
                "target": str(r["target"] or ""),
                "details": str(r["details"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    # ── User tags ────────────────────────────────────────────────────

    async def add_user_tag(self, tg_id: int, tag: str, created_by: str = "") -> bool:
        clean = _norm_text(tag, max_len=64).lower()
        if not clean:
            return False
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO user_tags (tg_id, tag, created_by) VALUES ($1, $2, $3) "
                "ON CONFLICT (tg_id, tag) DO NOTHING RETURNING id",
                int(tg_id), clean, _norm_text(created_by, max_len=64),
            )
        return row is not None

    async def remove_user_tag(self, tg_id: int, tag: str) -> bool:
        clean = _norm_text(tag, max_len=64).lower()
        if not clean:
            return False
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            tag_result = await conn.execute(
                "DELETE FROM user_tags WHERE tg_id = $1 AND tag = $2",
                int(tg_id), clean,
            )
        return _rowcount_from_tag(tag_result) > 0

    async def get_user_tags(self, tg_id: int) -> List[str]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tag FROM user_tags WHERE tg_id = $1 ORDER BY tag",
                int(tg_id),
            )
        return [str(r["tag"]) for r in rows]

    async def list_all_tags(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tag, COUNT(*)::BIGINT AS cnt FROM user_tags GROUP BY tag ORDER BY cnt DESC, tag"
            )
        return [{"tag": str(r["tag"]), "count": int(r["cnt"])} for r in rows]

    async def get_tags_for_users(self, tg_ids: List[int]) -> Dict[int, List[str]]:
        if not tg_ids:
            return {}
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tg_id, tag FROM user_tags WHERE tg_id = ANY($1::BIGINT[]) ORDER BY tag",
                [int(x) for x in tg_ids],
            )
        out: Dict[int, List[str]] = {}
        for r in rows:
            out.setdefault(int(r["tg_id"]), []).append(str(r["tag"]))
        return out

    # ── User notes ───────────────────────────────────────────────────

    async def add_user_note(self, tg_id: int, note: str, created_by: str = "") -> int:
        text = str(note or "").strip()
        if not text:
            return 0
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            nid = await conn.fetchval(
                "INSERT INTO user_notes (tg_id, note, created_by) VALUES ($1, $2, $3) RETURNING id",
                int(tg_id), text[:2000], _norm_text(created_by, max_len=64),
            )
        return int(nid or 0)

    async def delete_user_note(self, note_id: int) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            tag = await conn.execute(
                "DELETE FROM user_notes WHERE id = $1", int(note_id),
            )
        return _rowcount_from_tag(tag) > 0

    async def get_user_notes(self, tg_id: int) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, note, created_by, created_at FROM user_notes "
                "WHERE tg_id = $1 ORDER BY id DESC",
                int(tg_id),
            )
        return [
            {
                "id": int(r["id"]),
                "note": str(r["note"] or ""),
                "created_by": str(r["created_by"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    # ── Manual payments (revenue from outside the bot) ────────────────

    async def add_manual_payment(
        self,
        tg_id: int,
        amount_rub: int,
        note: str = "",
        created_by: str = "",
    ) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO manual_payments (tg_id, amount_rub, note, created_by) "
                "VALUES ($1, $2, $3, $4) RETURNING id",
                int(tg_id),
                int(amount_rub),
                _norm_text(note, max_len=500),
                _norm_text(created_by, max_len=128),
            )
            return int(row["id"]) if row else 0

    async def delete_manual_payment(self, mpid: int) -> bool:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM manual_payments WHERE id = $1", int(mpid))
        return res.endswith(" 1")

    async def list_manual_payments(self, tg_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, tg_id, amount_rub, note, created_by, created_at "
                "FROM manual_payments WHERE tg_id = $1 ORDER BY created_at DESC LIMIT $2",
                int(tg_id),
                int(limit),
            )
        return [
            {
                "id": int(r["id"]),
                "tg_id": int(r["tg_id"]),
                "amount_rub": int(r["amount_rub"]),
                "note": str(r["note"] or ""),
                "created_by": str(r["created_by"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def get_user_purchases(self, tg_id: int) -> Dict[str, Any]:
        """Structured view of paid products: per-package totals + active subscription."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            purchases = await conn.fetch(
                "SELECT package, COUNT(*)::INT AS count, "
                "SUM(amount_rub)::INT AS total_rub, "
                "MAX(created_at) AS last_at "
                "FROM payments WHERE tg_id = $1 AND status = 'CONFIRMED' "
                "GROUP BY package ORDER BY last_at DESC NULLS LAST",
                int(tg_id),
            )
            sub_row = await conn.fetchrow(
                "SELECT s.package, s.amount_rub, s.next_charge_at, s.status, s.created_at, "
                "(SELECT COUNT(*) FROM activity_log a WHERE a.tg_id = $1 AND a.event = 'subscription_charged') AS charges "
                "FROM subscriptions s WHERE s.tg_id = $1 AND s.status = 'active' "
                "ORDER BY s.id DESC LIMIT 1",
                int(tg_id),
            )
        return {
            "purchases": [
                {
                    "package": str(r["package"] or ""),
                    "count": int(r["count"]),
                    "total_rub": int(r["total_rub"] or 0),
                    "last_at": _fmt_ts(r["last_at"]),
                }
                for r in purchases
            ],
            "active_subscription": (
                {
                    "package": str(sub_row["package"] or ""),
                    "amount_rub": int(sub_row["amount_rub"] or 0),
                    "next_charge_at": _fmt_ts(sub_row["next_charge_at"]),
                    "charges_count": int(sub_row["charges"] or 0),
                    "status": str(sub_row["status"]),
                    "created_at": _fmt_ts(sub_row["created_at"]),
                } if sub_row else None
            ),
        }

    async def sum_manual_payments(self, tg_id: int) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(amount_rub), 0)::BIGINT FROM manual_payments WHERE tg_id = $1",
                int(tg_id),
            )
        return int(val or 0)

    # ── CRM: clients (credits > threshold) and stats ─────────────────

    async def list_clients(
        self,
        *,
        min_credits: int = 5,
        limit: int = 100,
        offset: int = 0,
        tag: str = "",
        sort: str = "credits",
        product: str = "",
    ) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        order_sql = {
            "credits": "u.credits DESC, u.updated_at DESC",
            "recent": "u.updated_at DESC, u.tg_id DESC",
            "oldest": "u.created_at ASC, u.tg_id ASC",
        }.get(sort, "u.credits DESC, u.updated_at DESC")
        tag_clean = _norm_text(tag, max_len=64).lower()
        params: List[Any] = [int(min_credits)]
        where = "u.credits >= $1 AND u.tg_id NOT IN (SELECT tg_id FROM admins)"
        if tag_clean:
            params.append(tag_clean)
            where += f" AND EXISTS (SELECT 1 FROM user_tags ut WHERE ut.tg_id = u.tg_id AND ut.tag = ${len(params)})"
        product_filter = _client_product_where("u.tg_id", str(product or "").strip().lower())
        if product_filter:
            where += f" AND {product_filter}"
        params.append(int(limit))
        params.append(int(offset))
        q = (
            f"SELECT u.tg_id, u.username, u.credits, u.created_at, u.updated_at, "
            f"COALESCE(NULLIF(u.source, ''), NULLIF(u.first_utm_source, ''), '') AS source, "
            f"(SELECT COUNT(*) FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'generation_done') AS gens_done, "
            f"(SELECT MAX(created_at) FROM activity_log a WHERE a.tg_id = u.tg_id) AS last_activity_at, "
            f"(SELECT COALESCE(SUM(amount_rub), 0) FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') "
            f"  + (SELECT COALESCE(SUM(amount_rub), 0) FROM manual_payments mp WHERE mp.tg_id = u.tg_id) "
            f"  AS revenue_rub, "
            f"(SELECT array_agg(DISTINCT p.package) FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package <> '') AS bought_packages, "
            f"EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = u.tg_id AND s.status = 'active') AS has_active_subscription "
            f"FROM users u WHERE {where} "
            f"ORDER BY {order_sql} LIMIT ${len(params) - 1} OFFSET ${len(params)}"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(q, *params)
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "credits": int(r["credits"]),
                "created_at": _fmt_ts(r["created_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
                "source": str(r["source"] or ""),
                "gens_done": int(r["gens_done"] or 0),
                "last_activity_at": _fmt_ts(r["last_activity_at"]),
                "revenue_rub": int(r["revenue_rub"] or 0),
                "bought_packages": list(r["bought_packages"] or []),
                "has_active_subscription": bool(r["has_active_subscription"]),
            }
            for r in rows
        ]

    async def count_clients(
        self, *, min_credits: int = 5, tag: str = "", product: str = "",
    ) -> int:
        pool = self._pool_or_fail()
        tag_clean = _norm_text(tag, max_len=64).lower()
        product_filter = _client_product_where("u.tg_id", str(product or "").strip().lower())
        params: List[Any] = [int(min_credits)]
        where = "u.credits >= $1 AND u.tg_id NOT IN (SELECT tg_id FROM admins)"
        if tag_clean:
            params.append(tag_clean)
            where += f" AND EXISTS (SELECT 1 FROM user_tags ut WHERE ut.tg_id = u.tg_id AND ut.tag = ${len(params)})"
        if product_filter:
            where += f" AND {product_filter}"
        async with pool.acquire() as conn:
            val = await conn.fetchval(f"SELECT COUNT(*) FROM users u WHERE {where}", *params)
        return int(val or 0)

    async def clients_summary(self, min_credits: int = 5) -> Dict[str, int]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "COUNT(*)::BIGINT AS clients_count, "
                "COALESCE(SUM(credits), 0)::BIGINT AS credits_on_balance, "
                "COUNT(*) FILTER (WHERE updated_at >= NOW() - INTERVAL '7 days')::BIGINT AS active_7d, "
                "COUNT(*) FILTER (WHERE updated_at < NOW() - INTERVAL '14 days')::BIGINT AS dormant_14d "
                "FROM users WHERE credits >= $1 "
                "AND tg_id NOT IN (SELECT tg_id FROM admins)",
                int(min_credits),
            )
            revenue_paid = await conn.fetchval(
                "SELECT COALESCE(SUM(amount_rub), 0)::BIGINT FROM payments p "
                "WHERE p.status = 'CONFIRMED' "
                "AND p.tg_id IN (SELECT tg_id FROM users WHERE credits >= $1) "
                "AND p.tg_id NOT IN (SELECT tg_id FROM admins)",
                int(min_credits),
            )
            revenue_manual = await conn.fetchval(
                "SELECT COALESCE(SUM(amount_rub), 0)::BIGINT FROM manual_payments mp "
                "WHERE mp.tg_id IN (SELECT tg_id FROM users WHERE credits >= $1) "
                "AND mp.tg_id NOT IN (SELECT tg_id FROM admins)",
                int(min_credits),
            )
        return {
            "clients_count": int(row["clients_count"] or 0) if row else 0,
            "credits_on_balance": int(row["credits_on_balance"] or 0) if row else 0,
            "active_7d": int(row["active_7d"] or 0) if row else 0,
            "dormant_14d": int(row["dormant_14d"] or 0) if row else 0,
            "revenue_rub_total": int(revenue_paid or 0) + int(revenue_manual or 0),
        }

    async def user_health_metrics(self, tg_id: int) -> Dict[str, Any]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "(SELECT MAX(created_at) FROM activity_log WHERE tg_id = $1) AS last_activity_at, "
                "(SELECT MAX(created_at) FROM activity_log WHERE tg_id = $1 AND event = 'generation_done') AS last_gen_at, "
                "(SELECT COUNT(*) FROM activity_log WHERE tg_id = $1 AND event = 'generation_done') AS gens_done, "
                "(SELECT COUNT(*) FROM activity_log WHERE tg_id = $1 AND event = 'generation_done' AND created_at >= NOW() - INTERVAL '30 days') AS gens_done_30d, "
                "(SELECT COALESCE(SUM(amount_rub), 0) FROM payments WHERE tg_id = $1 AND status = 'CONFIRMED') AS revenue_bot, "
                "(SELECT COALESCE(SUM(amount_rub), 0) FROM manual_payments WHERE tg_id = $1) AS revenue_manual, "
                "(SELECT COUNT(*) FROM payments WHERE tg_id = $1 AND status = 'CONFIRMED') AS paid_orders, "
                "(SELECT MAX(created_at) FROM payments WHERE tg_id = $1 AND status = 'CONFIRMED') AS last_payment_at",
                int(tg_id),
            )
        if not row:
            return {
                "last_activity_at": "", "last_gen_at": "", "gens_done": 0,
                "gens_done_30d": 0, "revenue_rub": 0, "revenue_bot": 0, "revenue_manual": 0,
                "paid_orders": 0, "last_payment_at": "",
            }
        revenue_bot = int(row["revenue_bot"] or 0)
        revenue_manual = int(row["revenue_manual"] or 0)
        return {
            "last_activity_at": _fmt_ts(row["last_activity_at"]),
            "last_gen_at": _fmt_ts(row["last_gen_at"]),
            "gens_done": int(row["gens_done"] or 0),
            "gens_done_30d": int(row["gens_done_30d"] or 0),
            "revenue_rub": revenue_bot + revenue_manual,
            "revenue_bot": revenue_bot,
            "revenue_manual": revenue_manual,
            "paid_orders": int(row["paid_orders"] or 0),
            "last_payment_at": _fmt_ts(row["last_payment_at"]),
            "_last_activity_raw": row["last_activity_at"],
            "_last_gen_raw": row["last_gen_at"],
        }

    # ── Audience resolution for broadcasts ───────────────────────────

    async def resolve_audience(self, audience: Dict[str, Any]) -> List[int]:
        """Resolve audience spec → list of tg_ids.

        audience schema:
          { "mode": "all" | "source" | "filter" | "manual" | "tier",
            "source": {"value": "..."},  # matches users.source OR users.first_utm_source; "(direct)" for empty
            "filter": {"credits_min": int, "credits_max": int,
                       "paid": "any"|"yes"|"no",
                       "generated": "any"|"yes"|"no",
                       "created_from": "YYYY-MM-DD", "created_to": "YYYY-MM-DD",
                       "tag": "..."},
            "manual": {"tg_ids": [int, ...], "usernames": [str, ...]},
            "exclude_blocked": bool,
            "exclude_admins": bool (default true) }
        """
        mode = str(audience.get("mode") or "all").lower()
        exclude_blocked = bool(audience.get("exclude_blocked", True))
        exclude_admins = bool(audience.get("exclude_admins", True))
        pool = self._pool_or_fail()

        ids: List[int] = []
        if mode == "all":
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT tg_id FROM users")
            ids = [int(r["tg_id"]) for r in rows]
        elif mode == "tier":
            tier_code = str((audience.get("tier") or {}).get("value", "")).strip().upper()
            if tier_code:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT tg_id FROM user_tiers WHERE tier = $1", tier_code,
                    )
                ids = [int(r["tg_id"]) for r in rows]
        elif mode == "source":
            src = _norm_text((audience.get("source") or {}).get("value", ""), max_len=160)
            async with pool.acquire() as conn:
                if src == "(direct)":
                    rows = await conn.fetch(
                        "SELECT tg_id FROM users "
                        "WHERE (source = '' OR source IS NULL) "
                        "AND (first_utm_source = '' OR first_utm_source IS NULL)"
                    )
                elif src:
                    rows = await conn.fetch(
                        "SELECT tg_id FROM users WHERE source = $1 OR first_utm_source = $1",
                        src,
                    )
                else:
                    rows = await conn.fetch("SELECT tg_id FROM users")
            ids = [int(r["tg_id"]) for r in rows]
        elif mode == "utm":
            # Backward-compat for any old broadcasts saved before the UTM→source migration.
            utm = audience.get("utm") or {}
            src_val = _norm_text(utm.get("source", ""), max_len=160)
            async with pool.acquire() as conn:
                if src_val:
                    rows = await conn.fetch(
                        "SELECT tg_id FROM users WHERE source = $1 OR first_utm_source = $1",
                        src_val,
                    )
                else:
                    rows = await conn.fetch("SELECT tg_id FROM users")
            ids = [int(r["tg_id"]) for r in rows]
        elif mode == "filter":
            f = audience.get("filter") or {}
            conds = ["TRUE"]
            params = []
            if f.get("credits_min") not in (None, ""):
                params.append(int(f["credits_min"]))
                conds.append(f"credits >= ${len(params)}")
            if f.get("credits_max") not in (None, ""):
                params.append(int(f["credits_max"]))
                conds.append(f"credits <= ${len(params)}")
            if f.get("created_from"):
                params.append(str(f["created_from"]))
                conds.append(f"created_at >= ${len(params)}::DATE")
            if f.get("created_to"):
                params.append(str(f["created_to"]))
                conds.append(f"created_at < (${len(params)}::DATE + INTERVAL '1 day')")
            paid = str(f.get("paid") or "any").lower()
            if paid == "yes":
                conds.append("EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = users.tg_id AND p.status = 'CONFIRMED')")
            elif paid == "no":
                conds.append("NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = users.tg_id AND p.status = 'CONFIRMED')")
            generated = str(f.get("generated") or "any").lower()
            if generated == "yes":
                conds.append("EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = users.tg_id AND a.event = 'generation_done')")
            elif generated == "no":
                conds.append("NOT EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = users.tg_id AND a.event = 'generation_done')")
            tag = _norm_text(f.get("tag", ""), max_len=64).lower()
            if tag:
                params.append(tag)
                conds.append(f"EXISTS (SELECT 1 FROM user_tags ut WHERE ut.tg_id = users.tg_id AND ut.tag = ${len(params)})")
            where = " AND ".join(conds)
            async with pool.acquire() as conn:
                rows = await conn.fetch(f"SELECT tg_id FROM users WHERE {where}", *params)
            ids = [int(r["tg_id"]) for r in rows]
        elif mode == "manual":
            m = audience.get("manual") or {}
            raw_ids = [int(x) for x in (m.get("tg_ids") or []) if str(x).strip().lstrip("-").isdigit()]
            usernames = [str(u).lstrip("@").lower() for u in (m.get("usernames") or []) if str(u).strip()]
            collected: set[int] = set(raw_ids)
            if usernames:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT tg_id FROM users WHERE username = ANY($1::TEXT[])", usernames,
                    )
                collected.update(int(r["tg_id"]) for r in rows)
            ids = list(collected)
        else:
            ids = []

        if exclude_blocked and ids:
            async with pool.acquire() as conn:
                blocked_rows = await conn.fetch(
                    "SELECT DISTINCT tg_id FROM activity_log "
                    "WHERE event = 'bot_blocked' AND tg_id = ANY($1::BIGINT[])",
                    ids,
                )
            blocked = {int(r["tg_id"]) for r in blocked_rows}
            ids = [x for x in ids if x not in blocked]

        if exclude_admins and ids:
            async with pool.acquire() as conn:
                admin_rows = await conn.fetch(
                    "SELECT tg_id FROM admins WHERE tg_id = ANY($1::BIGINT[])",
                    ids,
                )
            admin_ids = {int(r["tg_id"]) for r in admin_rows}
            ids = [x for x in ids if x not in admin_ids]

        return sorted(set(ids))

    async def distinct_utm_values(self, column: str, limit: int = 500) -> List[str]:
        allowed = {
            "source": "first_utm_source",
            "medium": "first_utm_medium",
            "campaign": "first_utm_campaign",
            "content": "first_utm_content",
            "term": "first_utm_term",
        }
        col = allowed.get(column)
        if not col:
            return []
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT DISTINCT {col} AS v FROM users WHERE {col} <> '' ORDER BY v LIMIT $1",
                int(limit),
            )
        return [str(r["v"]) for r in rows]

    # ── Broadcasts ───────────────────────────────────────────────────

    async def create_broadcast(
        self,
        *,
        title: str,
        text: str,
        parse_mode: str = "HTML",
        media_type: str = "",
        media_file_id: str = "",
        media_url: str = "",
        buttons: Optional[List[Dict[str, str]]] = None,
        audience: Optional[Dict[str, Any]] = None,
        schedule_at: Optional[datetime] = None,
        created_by: str = "",
    ) -> int:
        audience_json = json.dumps(audience or {"mode": "all"}, ensure_ascii=False)
        buttons_json = json.dumps(buttons or [], ensure_ascii=False)
        sched = schedule_at.replace(tzinfo=None) if (schedule_at and schedule_at.tzinfo) else schedule_at
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            bid = await conn.fetchval(
                "INSERT INTO broadcasts "
                "(title, text, parse_mode, media_type, media_file_id, media_url, buttons_json, "
                "audience_json, schedule_at, status, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'draft', $10) RETURNING id",
                _norm_text(title, max_len=200),
                str(text or "")[:4000],
                str(parse_mode or "HTML"),
                str(media_type or ""),
                str(media_file_id or ""),
                str(media_url or ""),
                buttons_json,
                audience_json,
                sched,
                _norm_text(created_by, max_len=64),
            )
        return int(bid or 0)

    async def update_broadcast(
        self,
        bid: int,
        *,
        title: Optional[str] = None,
        text: Optional[str] = None,
        parse_mode: Optional[str] = None,
        media_type: Optional[str] = None,
        media_file_id: Optional[str] = None,
        media_url: Optional[str] = None,
        buttons: Optional[List[Dict[str, str]]] = None,
        audience: Optional[Dict[str, Any]] = None,
        schedule_at: Optional[datetime] = None,
        clear_schedule: bool = False,
    ) -> None:
        sets: List[str] = []
        params: List[Any] = []
        def add(col: str, val: Any) -> None:
            params.append(val)
            sets.append(f"{col} = ${len(params)}")
        if title is not None:
            add("title", _norm_text(title, max_len=200))
        if text is not None:
            add("text", str(text)[:4000])
        if parse_mode is not None:
            add("parse_mode", str(parse_mode or "HTML"))
        if media_type is not None:
            add("media_type", str(media_type or ""))
        if media_file_id is not None:
            add("media_file_id", str(media_file_id or ""))
        if media_url is not None:
            add("media_url", str(media_url or ""))
        if buttons is not None:
            add("buttons_json", json.dumps(buttons, ensure_ascii=False))
        if audience is not None:
            add("audience_json", json.dumps(audience, ensure_ascii=False))
        if clear_schedule:
            sets.append("schedule_at = NULL")
        elif schedule_at is not None:
            sched = schedule_at.replace(tzinfo=None) if schedule_at.tzinfo else schedule_at
            add("schedule_at", sched)
        if not sets:
            return
        params.append(int(bid))
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE broadcasts SET {', '.join(sets)} WHERE id = ${len(params)}",
                *params,
            )

    async def set_broadcast_status(
        self, bid: int, status: str,
        *, started_at: Optional[datetime] = None, finished_at: Optional[datetime] = None,
        audience_size: Optional[int] = None,
    ) -> None:
        sets = ["status = $1"]
        params: List[Any] = [str(status)]
        if started_at is not None:
            params.append(started_at.replace(tzinfo=None) if started_at.tzinfo else started_at)
            sets.append(f"started_at = ${len(params)}")
        if finished_at is not None:
            params.append(finished_at.replace(tzinfo=None) if finished_at.tzinfo else finished_at)
            sets.append(f"finished_at = ${len(params)}")
        if audience_size is not None:
            params.append(int(audience_size))
            sets.append(f"audience_size = ${len(params)}")
        params.append(int(bid))
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE broadcasts SET {', '.join(sets)} WHERE id = ${len(params)}",
                *params,
            )

    async def get_broadcast(self, bid: int) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, title, text, parse_mode, media_type, media_file_id, media_url, "
                "buttons_json, audience_json, audience_size, schedule_at, status, "
                "sent_count, failed_count, blocked_count, created_by, created_at, started_at, finished_at "
                "FROM broadcasts WHERE id = $1",
                int(bid),
            )
        if not r:
            return None
        try:
            audience = json.loads(r["audience_json"] or "{}")
        except Exception:
            audience = {}
        try:
            buttons = json.loads(r["buttons_json"] or "[]")
        except Exception:
            buttons = []
        return {
            "id": int(r["id"]),
            "title": str(r["title"] or ""),
            "text": str(r["text"] or ""),
            "parse_mode": str(r["parse_mode"] or "HTML"),
            "media_type": str(r["media_type"] or ""),
            "media_file_id": str(r["media_file_id"] or ""),
            "media_url": str(r["media_url"] or ""),
            "buttons": buttons,
            "audience": audience,
            "audience_size": int(r["audience_size"] or 0),
            "schedule_at": _fmt_ts(r["schedule_at"]),
            "_schedule_raw": r["schedule_at"],
            "status": str(r["status"] or "draft"),
            "sent_count": int(r["sent_count"] or 0),
            "failed_count": int(r["failed_count"] or 0),
            "blocked_count": int(r["blocked_count"] or 0),
            "created_by": str(r["created_by"] or ""),
            "created_at": _fmt_ts(r["created_at"]),
            "started_at": _fmt_ts(r["started_at"]),
            "finished_at": _fmt_ts(r["finished_at"]),
        }

    async def list_broadcasts(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, title, status, audience_size, sent_count, failed_count, "
                "schedule_at, created_by, created_at, started_at, finished_at "
                "FROM broadcasts ORDER BY id DESC LIMIT $1 OFFSET $2",
                int(limit), int(offset),
            )
        return [
            {
                "id": int(r["id"]),
                "title": str(r["title"] or ""),
                "status": str(r["status"] or ""),
                "audience_size": int(r["audience_size"] or 0),
                "sent_count": int(r["sent_count"] or 0),
                "failed_count": int(r["failed_count"] or 0),
                "schedule_at": _fmt_ts(r["schedule_at"]),
                "created_by": str(r["created_by"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
                "started_at": _fmt_ts(r["started_at"]),
                "finished_at": _fmt_ts(r["finished_at"]),
            }
            for r in rows
        ]

    async def count_broadcasts(self) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM broadcasts") or 0)

    async def delete_broadcast(self, bid: int) -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM broadcast_deliveries WHERE broadcast_id = $1", int(bid))
                await conn.execute("DELETE FROM broadcasts WHERE id = $1", int(bid))

    async def seed_broadcast_deliveries(self, bid: int, tg_ids: List[int]) -> int:
        if not tg_ids:
            return 0
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            tag = await conn.execute(
                "INSERT INTO broadcast_deliveries (broadcast_id, tg_id) "
                "SELECT $1, x FROM UNNEST($2::BIGINT[]) AS t(x) "
                "ON CONFLICT (broadcast_id, tg_id) DO NOTHING",
                int(bid), [int(x) for x in tg_ids],
            )
        return _rowcount_from_tag(tag)

    async def fetch_pending_deliveries(self, bid: int, batch: int = 100) -> List[int]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tg_id FROM broadcast_deliveries "
                "WHERE broadcast_id = $1 AND status = 'pending' "
                "ORDER BY id LIMIT $2",
                int(bid), int(batch),
            )
        return [int(r["tg_id"]) for r in rows]

    async def mark_delivery(self, bid: int, tg_id: int, status: str, error: str = "") -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE broadcast_deliveries SET status = $1, error = $2, attempts = attempts + 1, sent_at = NOW() "
                "WHERE broadcast_id = $3 AND tg_id = $4",
                str(status), str(error or "")[:500], int(bid), int(tg_id),
            )
            if status == "sent":
                await conn.execute(
                    "UPDATE broadcasts SET sent_count = sent_count + 1 WHERE id = $1", int(bid),
                )
            elif status == "blocked":
                await conn.execute(
                    "UPDATE broadcasts SET blocked_count = blocked_count + 1 WHERE id = $1", int(bid),
                )
            elif status == "failed":
                await conn.execute(
                    "UPDATE broadcasts SET failed_count = failed_count + 1 WHERE id = $1", int(bid),
                )

    async def get_broadcast_deliveries(
        self, bid: int, *, status: str = "", limit: int = 100, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT bd.tg_id, bd.status, bd.error, bd.attempts, bd.sent_at, bd.created_at, u.username "
                    "FROM broadcast_deliveries bd LEFT JOIN users u ON u.tg_id = bd.tg_id "
                    "WHERE bd.broadcast_id = $1 AND bd.status = $2 ORDER BY bd.id LIMIT $3 OFFSET $4",
                    int(bid), str(status), int(limit), int(offset),
                )
            else:
                rows = await conn.fetch(
                    "SELECT bd.tg_id, bd.status, bd.error, bd.attempts, bd.sent_at, bd.created_at, u.username "
                    "FROM broadcast_deliveries bd LEFT JOIN users u ON u.tg_id = bd.tg_id "
                    "WHERE bd.broadcast_id = $1 ORDER BY bd.id LIMIT $2 OFFSET $3",
                    int(bid), int(limit), int(offset),
                )
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "status": str(r["status"] or ""),
                "error": str(r["error"] or ""),
                "attempts": int(r["attempts"] or 0),
                "sent_at": _fmt_ts(r["sent_at"]),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def find_due_broadcasts(self) -> List[Dict[str, Any]]:
        """Return broadcasts ready to process: scheduled whose time arrived, and already-sending ones."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM broadcasts "
                "WHERE status = 'sending' "
                "OR (status = 'scheduled' AND schedule_at IS NOT NULL AND schedule_at <= NOW()) "
                "ORDER BY id"
            )
        return [{"id": int(r["id"])} for r in rows]

    # ── Lifecycle rules ──────────────────────────────────────────────

    async def create_lifecycle_rule(
        self, *, name: str, trigger_type: str, trigger: Dict[str, Any],
        message_text: str, parse_mode: str = "HTML", cooldown_days: int = 7,
        enabled: bool = True, created_by: str = "",
    ) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rid = await conn.fetchval(
                "INSERT INTO lifecycle_rules "
                "(name, trigger_type, trigger_json, message_text, parse_mode, cooldown_days, enabled, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
                _norm_text(name, max_len=120),
                _norm_text(trigger_type, max_len=64),
                json.dumps(trigger or {}, ensure_ascii=False),
                str(message_text or "")[:4000],
                str(parse_mode or "HTML"),
                max(0, int(cooldown_days)),
                bool(enabled),
                _norm_text(created_by, max_len=64),
            )
        return int(rid or 0)

    async def update_lifecycle_rule(
        self, rid: int, *, name: Optional[str] = None, trigger: Optional[Dict[str, Any]] = None,
        message_text: Optional[str] = None, cooldown_days: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        sets: List[str] = ["updated_at = NOW()"]
        params: List[Any] = []
        def add(col: str, val: Any) -> None:
            params.append(val)
            sets.append(f"{col} = ${len(params)}")
        if name is not None:
            add("name", _norm_text(name, max_len=120))
        if trigger is not None:
            add("trigger_json", json.dumps(trigger, ensure_ascii=False))
        if message_text is not None:
            add("message_text", str(message_text)[:4000])
        if cooldown_days is not None:
            add("cooldown_days", max(0, int(cooldown_days)))
        if enabled is not None:
            add("enabled", bool(enabled))
        if len(sets) == 1:
            return
        params.append(int(rid))
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE lifecycle_rules SET {', '.join(sets)} WHERE id = ${len(params)}",
                *params,
            )

    async def delete_lifecycle_rule(self, rid: int) -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM lifecycle_rules WHERE id = $1", int(rid))

    async def list_lifecycle_rules(self) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, trigger_type, trigger_json, message_text, parse_mode, "
                "cooldown_days, enabled, last_run_at, fired_count, created_by, created_at, updated_at "
                "FROM lifecycle_rules ORDER BY id DESC"
            )
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                trig = json.loads(r["trigger_json"] or "{}")
            except Exception:
                trig = {}
            out.append({
                "id": int(r["id"]),
                "name": str(r["name"] or ""),
                "trigger_type": str(r["trigger_type"] or ""),
                "trigger": trig,
                "message_text": str(r["message_text"] or ""),
                "parse_mode": str(r["parse_mode"] or "HTML"),
                "cooldown_days": int(r["cooldown_days"] or 0),
                "enabled": bool(r["enabled"]),
                "last_run_at": _fmt_ts(r["last_run_at"]),
                "fired_count": int(r["fired_count"] or 0),
                "created_by": str(r["created_by"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
            })
        return out

    async def get_lifecycle_rule(self, rid: int) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, name, trigger_type, trigger_json, message_text, parse_mode, "
                "cooldown_days, enabled, last_run_at, fired_count, created_by, created_at, updated_at "
                "FROM lifecycle_rules WHERE id = $1", int(rid),
            )
        if not r:
            return None
        try:
            trig = json.loads(r["trigger_json"] or "{}")
        except Exception:
            trig = {}
        return {
            "id": int(r["id"]),
            "name": str(r["name"] or ""),
            "trigger_type": str(r["trigger_type"] or ""),
            "trigger": trig,
            "message_text": str(r["message_text"] or ""),
            "parse_mode": str(r["parse_mode"] or "HTML"),
            "cooldown_days": int(r["cooldown_days"] or 0),
            "enabled": bool(r["enabled"]),
            "last_run_at": _fmt_ts(r["last_run_at"]),
            "fired_count": int(r["fired_count"] or 0),
            "created_by": str(r["created_by"] or ""),
        }

    async def find_lifecycle_candidates(self, rule: Dict[str, Any], *, limit: int = 200) -> List[int]:
        """Compute tg_ids that match rule's trigger AND haven't been fired within cooldown."""
        trigger_type = str(rule.get("trigger_type") or "")
        trig = rule.get("trigger") or {}
        cooldown = int(rule.get("cooldown_days") or 7)
        rid = int(rule.get("id") or 0)

        pool = self._pool_or_fail()
        sql: str
        params: List[Any]

        if trigger_type == "balance_low":
            threshold = int(trig.get("credits_leq", 2))
            min_balance = int(trig.get("credits_geq", 1))
            sql = (
                "SELECT u.tg_id FROM users u "
                "WHERE u.credits BETWEEN $1 AND $2 "
                "AND EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') "
                "AND NOT EXISTS (SELECT 1 FROM lifecycle_fires f WHERE f.rule_id = $3 AND f.tg_id = u.tg_id "
                "AND f.created_at >= NOW() - ($4::INT * INTERVAL '1 day'))"
            )
            params = [min_balance, threshold, rid, cooldown]
        elif trigger_type == "inactive":
            days = int(trig.get("days", 14))
            sql = (
                "SELECT u.tg_id FROM users u "
                "WHERE NOT EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id "
                "AND a.created_at >= NOW() - ($1::INT * INTERVAL '1 day')) "
                "AND u.created_at <= NOW() - ($1::INT * INTERVAL '1 day') "
                "AND NOT EXISTS (SELECT 1 FROM lifecycle_fires f WHERE f.rule_id = $2 AND f.tg_id = u.tg_id "
                "AND f.created_at >= NOW() - ($3::INT * INTERVAL '1 day'))"
            )
            params = [days, rid, cooldown]
        elif trigger_type == "generated_not_paid":
            min_gens = int(trig.get("min_gens", 3))
            sql = (
                "SELECT u.tg_id FROM users u "
                "WHERE (SELECT COUNT(*) FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'generation_done') >= $1 "
                "AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') "
                "AND NOT EXISTS (SELECT 1 FROM lifecycle_fires f WHERE f.rule_id = $2 AND f.tg_id = u.tg_id "
                "AND f.created_at >= NOW() - ($3::INT * INTERVAL '1 day'))"
            )
            params = [min_gens, rid, cooldown]
        else:
            return []

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql + " LIMIT $" + str(len(params) + 1), *params, int(limit))
        return [int(r["tg_id"]) for r in rows]

    async def record_lifecycle_fire(self, rule_id: int, tg_id: int, status: str, error: str = "") -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO lifecycle_fires (rule_id, tg_id, status, error) VALUES ($1, $2, $3, $4)",
                    int(rule_id), int(tg_id), str(status), str(error or "")[:500],
                )
                if status == "sent":
                    await conn.execute(
                        "UPDATE lifecycle_rules SET fired_count = fired_count + 1, "
                        "last_run_at = NOW(), updated_at = NOW() WHERE id = $1",
                        int(rule_id),
                    )

    async def touch_lifecycle_rule(self, rid: int) -> None:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE lifecycle_rules SET last_run_at = NOW(), updated_at = NOW() WHERE id = $1",
                int(rid),
            )

    # ── Tier system ──────────────────────────────────────────────────

    async def tier_counts(self) -> Dict[str, int]:
        """Return {tier_code: count} for all classified users."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tier, COUNT(*)::BIGINT AS cnt FROM user_tiers WHERE tier IS NOT NULL GROUP BY tier"
            )
        return {str(r["tier"]): int(r["cnt"]) for r in rows}

    async def list_tier_users(self, tier: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Return rows from user_tiers for a given tier."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM user_tiers WHERE tier = $1 "
                "ORDER BY last_active_at DESC NULLS LAST, tg_id DESC LIMIT $2",
                str(tier), int(limit),
            )
        return [dict(r) for r in rows]

    async def get_user_tier(self, tg_id: int) -> Optional[str]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT tier FROM user_tiers WHERE tg_id = $1", int(tg_id))
        return str(val) if val else None

    async def get_user_signals(self, tg_id: int) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM user_tiers WHERE tg_id = $1", int(tg_id))
        return dict(row) if row else None

    async def resolve_tier_audience(self, tier: str, exclude_blocked: bool = True) -> List[int]:
        """Audience resolver for broadcast 'tier' mode."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT tg_id FROM user_tiers WHERE tier = $1", str(tier))
        ids = [int(r["tg_id"]) for r in rows]
        if exclude_blocked and ids:
            async with pool.acquire() as conn:
                blocked_rows = await conn.fetch(
                    "SELECT DISTINCT tg_id FROM activity_log "
                    "WHERE event = 'bot_blocked' AND tg_id = ANY($1::BIGINT[])",
                    ids,
                )
            blocked = {int(r["tg_id"]) for r in blocked_rows}
            ids = [x for x in ids if x not in blocked]
        return sorted(set(ids))

    # ── Tier outreach (S-tier manager workflow) ──────────────────────

    async def upsert_outreach(
        self,
        tg_id: int,
        tier: str,
        status: str,
        assigned_to: str = "",
        note: str = "",
    ) -> None:
        pool = self._pool_or_fail()
        contacted = "NOW()" if status in ("contacted", "converted", "dropped") else "NULL"
        async with pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO tier_outreach (tg_id, tier, status, assigned_to, note, contacted_at, updated_at) "
                f"VALUES ($1, $2, $3, $4, $5, {contacted}, NOW()) "
                f"ON CONFLICT (tg_id, tier) DO UPDATE SET "
                f"status = EXCLUDED.status, "
                f"assigned_to = CASE WHEN EXCLUDED.assigned_to <> '' THEN EXCLUDED.assigned_to ELSE tier_outreach.assigned_to END, "
                f"note = CASE WHEN EXCLUDED.note <> '' THEN EXCLUDED.note ELSE tier_outreach.note END, "
                f"contacted_at = COALESCE(tier_outreach.contacted_at, {contacted}), "
                f"updated_at = NOW()",
                int(tg_id), str(tier), str(status),
                _norm_text(assigned_to, max_len=128),
                _norm_text(note, max_len=500),
            )

    async def get_outreach_map(self, tier: str, tg_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        if not tg_ids:
            return {}
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tg_id, status, assigned_to, note, contacted_at, updated_at "
                "FROM tier_outreach WHERE tier = $1 AND tg_id = ANY($2::BIGINT[])",
                str(tier), tg_ids,
            )
        return {
            int(r["tg_id"]): {
                "status": str(r["status"]),
                "assigned_to": str(r["assigned_to"] or ""),
                "note": str(r["note"] or ""),
                "contacted_at": _fmt_ts(r["contacted_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
            }
            for r in rows
        }

    async def outreach_summary(self, tier: str) -> Dict[str, int]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*)::BIGINT AS cnt FROM tier_outreach "
                "WHERE tier = $1 GROUP BY status",
                str(tier),
            )
        return {str(r["status"]): int(r["cnt"]) for r in rows}
