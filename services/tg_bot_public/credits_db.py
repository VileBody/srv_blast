"""PostgreSQL-backed credits & user tracking for the public Telegram bot."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

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
-- Composite indexes — dramatically speed up the user_signals view, which runs
-- ~17 EXISTS / COUNT / MAX subqueries per user keyed on (tg_id, event[, created_at]).
CREATE INDEX IF NOT EXISTS idx_act_tg_event   ON activity_log(tg_id, event);
CREATE INDEX IF NOT EXISTS idx_act_tg_event_created ON activity_log(tg_id, event, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_act_tg_created ON activity_log(tg_id, created_at DESC);

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
-- Composite — speeds up "EXISTS payments WHERE tg_id=X AND status='CONFIRMED'"
-- which runs once per user inside user_signals.has_purchase.
CREATE INDEX IF NOT EXISTS idx_pay_tg_status   ON payments(tg_id, status);

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
    next_charge_at  TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '1 month'),
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


# payments.package is historically stored as either a numeric code ('5', '15',
# '30', '50') or the human name ('Триал', 'Бласт', 'Глоу', 'Импульс'),
# depending on which code path created the row. Filters and rendering must
# accept BOTH spellings for the same product.
_PKG_ALIASES = {
    "trial":   ("'5'", "'Триал'"),
    "blast":   ("'15'", "'Бласт'"),
    "glow":    ("'30'", "'Глоу'"),
    "impulse": ("'50'", "'Импульс'"),
}


def _client_product_where(tg_id_col: str, product: str) -> str:
    """SQL fragment for filtering clients by purchased product.

    Returns "" when no filter applies. Accepts both legacy numeric codes
    and the Russian package names actually stored by the bot today.
    """
    if not product:
        return ""

    if product in _PKG_ALIASES:
        aliases = _PKG_ALIASES[product]
        in_list = ", ".join(aliases)
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} "
            f"AND p.status = 'CONFIRMED' AND p.package IN ({in_list}))"
        )
    if product == "blast_subscription":
        b_aliases = ", ".join(_PKG_ALIASES["blast"])
        return (
            f"EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = {tg_id_col} "
            f"AND s.status = 'active' AND s.package IN ({b_aliases}))"
        )
    if product == "any":
        return (
            f"EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} "
            f"AND p.status = 'CONFIRMED')"
        )
    if product == "none":
        return (
            f"NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = {tg_id_col} AND p.status = 'CONFIRMED') "
            f"AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = {tg_id_col} AND s.status = 'active')"
        )
    return ""


def normalize_package_code(value: str) -> str:
    """Map a stored payments.package value to a canonical code (5/15/30/50).

    Returns "" if the value isn't recognised.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    name_to_code = {
        "Триал": "5", "Бласт": "15", "Глоу": "30", "Импульс": "50",
        "trial": "5", "blast": "15", "glow": "30", "impulse": "50",
    }
    if s in name_to_code:
        return name_to_code[s]
    if s in {"5", "15", "30", "50"}:
        return s
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
            "tier TEXT NOT NULL DEFAULT '',"
            "exclude_paid BOOLEAN NOT NULL DEFAULT TRUE,"
            "respect_anti_fatigue BOOLEAN NOT NULL DEFAULT TRUE,"
            "created_at TIMESTAMP NOT NULL DEFAULT NOW(),"
            "updated_at TIMESTAMP NOT NULL DEFAULT NOW()"
            ")"
        )
        # Idempotent migrations for older deployments missing the new columns.
        await conn.execute(
            "ALTER TABLE lifecycle_rules ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT ''"
        )
        await conn.execute(
            "ALTER TABLE lifecycle_rules ADD COLUMN IF NOT EXISTS exclude_paid BOOLEAN NOT NULL DEFAULT TRUE"
        )
        await conn.execute(
            "ALTER TABLE lifecycle_rules ADD COLUMN IF NOT EXISTS respect_anti_fatigue BOOLEAN NOT NULL DEFAULT TRUE"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lcr_tier ON lifecycle_rules(tier)"
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
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lcf_tg_status_created "
            "ON lifecycle_fires(tg_id, status, created_at DESC)"
        )

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
              -- has_purchase: canonical "is a paying customer" signal, used to keep
              -- customers out of reactivation tiers (S/A/B/D). Three independent
              -- triggers — any one of them is enough:
              --   1. Confirmed bot payment (payments table).
              --   2. Manually-logged external revenue (manual_payments — any row;
              --      that table is only filled when a manager records real money).
              --   3. Manual credit grants summing to more than 5 (transactions table,
              --      excluding system reasons like initial_grant / payment / subscription).
              --      Threshold > 5 is intentional: small 1-3-credit goodwill grants for
              --      free re-tries shouldn't promote a non-paying user into "customer".
              --      The smallest paid package is 5 credits (Trial), so >5 captures
              --      real activations like Trial(5), Бласт(15), Glow(30), Импульс(50)
              --      and the admin_activate flow.
              (
                EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED')
                OR EXISTS (SELECT 1 FROM manual_payments mp WHERE mp.tg_id = u.tg_id)
                OR (SELECT COALESCE(SUM(t.amount), 0) FROM transactions t WHERE t.tg_id = u.tg_id
                    AND t.amount > 0
                    AND t.reason NOT IN ('initial_grant', 'payment', 'subscription', 'subscription_charge', 'refund')
                   ) > 5
              ) AS has_purchase,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '5') AS bought_trial,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '15') AS bought_blast,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '30') AS bought_glow,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.package = '50') AS bought_impulse,
              (SELECT s.package FROM subscriptions s WHERE s.tg_id = u.tg_id AND s.status = 'active' ORDER BY s.id DESC LIMIT 1) AS active_subscription_pkg,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'survey_opened') AS feedback_form_clicked,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'survey_done') AS feedback_form_filled,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'referral_sent') AS referral_made,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'admin_dm') AS manager_contacted,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'admin_dm' AND a.created_at >= NOW() - INTERVAL '7 days') AS manager_contacted_7d,
              EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'bot_blocked') AS bot_blocked,
              EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED' AND p.created_at >= NOW() - INTERVAL '24 hours') AS paid_within_24h,
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
                -- Reactivation plan v2.0: S → P → A → B → D
                -- Global exclusions enforced INSIDE every tier predicate:
                --   • bot_blocked = false (always; dead leads not classified)
                --   • payment_confirmed → user falls out of all reactivation tiers (S/A/B/D);
                --     P3/P4 use 24h freshness instead so a past-buyer who clicks pay
                --     again counts as a fresh "тыкун".

                -- ── S group (hot, manager-led conversion) ─────────────────
                WHEN NOT us.bot_blocked AND us.gens_done >= 2 AND us.last_rating = 'high'
                     AND us.feedback_form_clicked AND NOT us.has_purchase THEN 'S1'
                WHEN NOT us.bot_blocked AND us.last_rating = 'high' AND us.viewed_package_details
                     AND NOT us.feedback_form_clicked AND NOT us.has_purchase THEN 'S2'
                WHEN NOT us.bot_blocked AND us.gens_done = 2 AND us.last_rating = 'high'
                     AND NOT us.feedback_form_clicked AND NOT us.viewed_package_details
                     AND NOT us.has_purchase THEN 'S3'

                -- ── P group (paying / intent; non-cascading) ──────────────
                -- P3 / P4: pressed pay but no payment_confirmed in the last 24h.
                WHEN NOT us.bot_blocked AND us.purchase_intent AND NOT us.paid_within_24h
                     AND us.gens_done >= 1 AND us.last_rating = 'high' THEN 'P3'
                WHEN NOT us.bot_blocked AND us.purchase_intent AND NOT us.paid_within_24h THEN 'P4'
                -- P1: bought trial only, no upper package, balance ≤ 1.
                WHEN NOT us.bot_blocked AND us.bought_trial AND NOT us.bought_blast
                     AND NOT us.bought_glow AND NOT us.bought_impulse
                     AND us.credits <= 1 THEN 'P1'

                -- ── A group (warm; only for not-yet-purchased users) ──────
                WHEN NOT us.bot_blocked AND us.gens_done = 1 AND us.last_rating = 'high'
                     AND NOT us.referral_made AND NOT us.has_purchase THEN 'A1'
                -- A2: uploaded audio but didn't actually finish a generation. Use
                -- gens_done = 0 (canonical "no completed generations") instead of
                -- NOT generation_started — old data sometimes has missing started
                -- events for completed gens, which let users with gens=2 leak in.
                WHEN NOT us.bot_blocked AND us.audio_uploaded AND us.gens_done = 0
                     AND NOT us.has_purchase THEN 'A2'
                WHEN NOT us.bot_blocked AND us.gens_done = 1 AND us.last_rating IS NULL
                     AND NOT us.has_purchase THEN 'A3'

                -- ── B group (medium; only for not-yet-purchased users) ────
                WHEN NOT us.bot_blocked AND us.gens_done = 1 AND us.last_rating = 'mid_low'
                     AND NOT us.has_purchase THEN 'B1'
                WHEN NOT us.bot_blocked AND us.last_rating = 'low'
                     AND NOT us.feedback_form_clicked
                     AND NOT us.has_purchase THEN 'B2'
                -- B3: explicitly require gens_done = 0 so users who restarted the
                -- bot (clicked /start again after generating) don't get classified
                -- as "стартовали, не подписались".
                WHEN NOT us.bot_blocked AND NOT us.subscribed AND us.gens_done = 0
                     AND NOT us.has_purchase THEN 'B3'

                -- D group is computed via audience-only resolvers (manual broadcasts);
                -- not assigned as a primary tier because it's a passive segment that
                -- shouldn't pull users out of S/A/B if they're still active.
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
                "rebill_id, is_recurrent, "
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
            "rebill_id": str(r["rebill_id"] or ""),
            "is_recurrent": bool(r["is_recurrent"]),
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
        """Return all payments not yet confirmed/rejected for T-Bank polling."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, is_recurrent, created_at "
                "FROM payments "
                "WHERE status = 'pending' OR UPPER(status) IN ('NEW', 'FORM_SHOWED', 'AUTHORIZED') "
                "ORDER BY created_at ASC",
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
                "WHERE id = ("
                "  SELECT id FROM payments "
                "  WHERE tg_id = $2 AND UPPER(status) = 'CONFIRMED' AND rebill_id = '' "
                "  ORDER BY updated_at DESC LIMIT 1"
                ")",
                str(rebill_id),
                int(tg_id),
            )

    async def get_rebill_id(self, tg_id: int) -> Optional[str]:
        """Get the latest RebillId for a user (from their last recurrent parent payment)."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT rebill_id FROM payments "
                "WHERE tg_id = $1 AND rebill_id <> '' AND is_recurrent = TRUE AND UPPER(status) = 'CONFIRMED' "
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
                "SELECT 1 FROM payments WHERE payment_id = $1 AND UPPER(status) = UPPER($2) LIMIT 1",
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
                "SET next_charge_at = NOW() + INTERVAL '1 month', "
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

    # ── Admin dashboard helpers ─────────────────────────────────────────

    async def rating_distribution_v2(self) -> Dict[str, int]:
        """Like rating_distribution but with 4 buckets:
        low (1-4), mid_low (5-6), mid_high (7-8), high (9-10).
        Returns {bucket: count} dict so admin can render a 4-way chart.
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT detail, COUNT(*)::BIGINT AS cnt "
                "FROM activity_log WHERE event = 'rate_video' AND detail <> '' "
                "GROUP BY detail"
            )
        out = {"low": 0, "mid_low": 0, "mid_high": 0, "high": 0}
        # Existing data is stored as either numeric "1".."10" or legacy
        # bucket strings. Map both.
        for r in rows:
            d = str(r["detail"] or "").strip().lower()
            cnt = int(r["cnt"] or 0)
            if d in {"low", "mid_low", "mid_high", "high"}:
                out[d] += cnt
                continue
            try:
                n = int(d.split()[0])
            except Exception:
                continue
            if n <= 4:
                out["low"] += cnt
            elif n <= 6:
                out["mid_low"] += cnt
            elif n <= 8:
                out["mid_high"] += cnt
            else:
                out["high"] += cnt
        return out

    async def revenue_timeseries(
        self, *, bucket: str = "month", periods: int = 12,
    ) -> List[Dict[str, Any]]:
        """Revenue chart series from CONFIRMED payments.

        bucket="month" → current calendar month, one bar per DAY (1..N).
        bucket="week"  → last `periods` weeks, one bar per ISO week.

        Returns list ordered oldest → newest: [{"bucket": "...", "rub": N}, ...]
        """
        bucket = str(bucket or "month").lower()
        if bucket not in {"week", "month"}:
            bucket = "month"
        pool = self._pool_or_fail()
        if bucket == "month":
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    WITH buckets AS (
                        SELECT generate_series(
                            DATE_TRUNC('month', NOW()),
                            (DATE_TRUNC('month', NOW()) + INTERVAL '1 month' - INTERVAL '1 day'),
                            INTERVAL '1 day'
                        ) AS b
                    )
                    SELECT
                      TO_CHAR(b.b, 'DD') AS lbl,
                      b.b AS bucket_start,
                      COALESCE((
                        SELECT SUM(amount_rub)::BIGINT FROM payments p
                         WHERE p.status = 'CONFIRMED'
                           AND p.created_at >= b.b
                           AND p.created_at <  b.b + INTERVAL '1 day'
                      ), 0) AS rub
                    FROM buckets b
                    ORDER BY b.b ASC
                    """,
                )
        else:
            periods = max(1, min(int(periods or 12), 60))
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    WITH buckets AS (
                        SELECT generate_series(
                            DATE_TRUNC('week', NOW()) - ($1::INT - 1) * INTERVAL '1 week',
                            DATE_TRUNC('week', NOW()),
                            INTERVAL '1 week'
                        ) AS b
                    )
                    SELECT
                      TO_CHAR(b.b, 'IYYY-"W"IW') AS lbl,
                      b.b AS bucket_start,
                      COALESCE((
                        SELECT SUM(amount_rub)::BIGINT FROM payments p
                         WHERE p.status = 'CONFIRMED'
                           AND p.created_at >= b.b
                           AND p.created_at <  b.b + INTERVAL '1 week'
                      ), 0) AS rub
                    FROM buckets b
                    ORDER BY b.b ASC
                    """,
                    int(periods),
                )
        return [
            {"bucket": str(r["lbl"]), "rub": int(r["rub"] or 0)}
            for r in rows
        ]

    async def users_timeseries(
        self, *, bucket: str = "month",
    ) -> Dict[str, Any]:
        """Inflow vs outflow users per bucket.

        bucket="month" → current calendar month, one bar per DAY.
        bucket="week"  → last 12 weeks, one bar per ISO week.

        Inflow  = `users.created_at` (new signups in the bucket).
        Outflow = distinct tg_ids with `activity_log.event='bot_blocked'`
                  in the bucket (Telegram MyChatMember kicked/left).

        Returns {"series": [{"bucket": str, "inflow": int, "outflow": int}, ...],
                 "total_users": int, "blocked_total": int, "active_total": int}.
        """
        bucket = str(bucket or "month").lower()
        if bucket not in {"week", "month"}:
            bucket = "month"
        pool = self._pool_or_fail()
        if bucket == "month":
            sql = """
                WITH buckets AS (
                    SELECT generate_series(
                        DATE_TRUNC('month', NOW()),
                        (DATE_TRUNC('month', NOW()) + INTERVAL '1 month' - INTERVAL '1 day'),
                        INTERVAL '1 day'
                    ) AS b
                )
                SELECT
                  TO_CHAR(b.b, 'DD') AS lbl,
                  (SELECT COUNT(*)::BIGINT FROM users u
                     WHERE u.created_at >= b.b
                       AND u.created_at <  b.b + INTERVAL '1 day') AS inflow,
                  (SELECT COUNT(DISTINCT a.tg_id)::BIGINT FROM activity_log a
                     WHERE a.event = 'bot_blocked'
                       AND a.created_at >= b.b
                       AND a.created_at <  b.b + INTERVAL '1 day') AS outflow
                FROM buckets b
                ORDER BY b.b ASC
            """
            params: list = []
        else:
            sql = """
                WITH buckets AS (
                    SELECT generate_series(
                        DATE_TRUNC('week', NOW()) - 11 * INTERVAL '1 week',
                        DATE_TRUNC('week', NOW()),
                        INTERVAL '1 week'
                    ) AS b
                )
                SELECT
                  TO_CHAR(b.b, 'IYYY-"W"IW') AS lbl,
                  (SELECT COUNT(*)::BIGINT FROM users u
                     WHERE u.created_at >= b.b
                       AND u.created_at <  b.b + INTERVAL '1 week') AS inflow,
                  (SELECT COUNT(DISTINCT a.tg_id)::BIGINT FROM activity_log a
                     WHERE a.event = 'bot_blocked'
                       AND a.created_at >= b.b
                       AND a.created_at <  b.b + INTERVAL '1 week') AS outflow
                FROM buckets b
                ORDER BY b.b ASC
            """
            params = []
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            totals = await conn.fetchrow(
                """
                SELECT
                  (SELECT COUNT(*)::BIGINT FROM users) AS total_users,
                  (SELECT COUNT(DISTINCT tg_id)::BIGINT FROM activity_log
                     WHERE event = 'bot_blocked') AS blocked_total
                """
            )
        total_users = int(totals["total_users"] or 0) if totals else 0
        blocked_total = int(totals["blocked_total"] or 0) if totals else 0
        return {
            "series": [
                {"bucket": str(r["lbl"]),
                 "inflow": int(r["inflow"] or 0),
                 "outflow": int(r["outflow"] or 0)}
                for r in rows
            ],
            "total_users": total_users,
            "blocked_total": blocked_total,
            "active_total": max(0, total_users - blocked_total),
        }

    async def list_active_subscriptions(self) -> List[Dict[str, Any]]:
        """All subscriptions (active + paused) sorted by next_charge_at.

        Joins username + last recurrent payment status for the admin
        verification view.
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.id, s.tg_id, s.package, s.amount_rub, s.status,
                       s.next_charge_at, s.charge_retries, s.rebill_id,
                       s.created_at, s.cancelled_at,
                       u.username,
                       (SELECT created_at FROM payments p
                          WHERE p.tg_id = s.tg_id AND p.is_recurrent = TRUE
                          ORDER BY created_at DESC LIMIT 1) AS last_charge_at,
                       (SELECT status FROM payments p
                          WHERE p.tg_id = s.tg_id AND p.is_recurrent = TRUE
                          ORDER BY created_at DESC LIMIT 1) AS last_charge_status
                  FROM subscriptions s
                  LEFT JOIN users u ON u.tg_id = s.tg_id
                 WHERE s.status IN ('active', 'paused')
                 ORDER BY (s.status = 'active') DESC, s.next_charge_at ASC
                """
            )
        return [
            {
                "id": int(r["id"]),
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "package": str(r["package"] or ""),
                "amount_rub": int(r["amount_rub"] or 0),
                "status": str(r["status"] or ""),
                "next_charge_at": _fmt_ts(r["next_charge_at"]),
                "_next_charge_raw": r["next_charge_at"],
                "charge_retries": int(r["charge_retries"] or 0),
                "rebill_id": str(r["rebill_id"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
                "cancelled_at": _fmt_ts(r["cancelled_at"]),
                "last_charge_at": _fmt_ts(r["last_charge_at"]),
                "last_charge_status": str(r["last_charge_status"] or ""),
            }
            for r in rows
        ]

    async def subscriptions_summary(self) -> Dict[str, Any]:
        """Counts + sum for the admin subscriptions dashboard.

        Real auto-charges (rebills) are counted via activity_log events
        `subscription_charged*` / `subscription_charge_failed`, which are
        emitted only by the daily charge loop (or admin manual trigger).
        This avoids counting INITIAL subscription purchases — those also
        have payments.is_recurrent=TRUE but represent a user paying for the
        first time, not an automatic monthly rebill.
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status = 'active')::BIGINT AS active_cnt,
                  COUNT(*) FILTER (WHERE status = 'paused')::BIGINT AS paused_cnt,
                  COUNT(*) FILTER (WHERE status = 'active' AND next_charge_at::date = CURRENT_DATE)::BIGINT AS due_today_cnt,
                  COALESCE(SUM(amount_rub) FILTER (WHERE status = 'active' AND next_charge_at::date = CURRENT_DATE), 0)::BIGINT AS due_today_rub,
                  COUNT(*) FILTER (WHERE status = 'active' AND next_charge_at < NOW() + INTERVAL '7 days')::BIGINT AS due_7d_cnt,
                  COALESCE(SUM(amount_rub) FILTER (WHERE status = 'active' AND next_charge_at < NOW() + INTERVAL '7 days'), 0)::BIGINT AS due_7d_rub,
                  COUNT(*) FILTER (WHERE status = 'active' AND DATE_TRUNC('month', next_charge_at) = DATE_TRUNC('month', NOW()))::BIGINT AS due_this_month_cnt,
                  COALESCE(SUM(amount_rub) FILTER (WHERE status = 'active' AND DATE_TRUNC('month', next_charge_at) = DATE_TRUNC('month', NOW())), 0)::BIGINT AS due_this_month_rub,
                  COUNT(*) FILTER (WHERE status = 'active' AND next_charge_at <= NOW())::BIGINT AS overdue_cnt
                FROM subscriptions
                """
            )
            charge_stats = await conn.fetchrow(
                """
                SELECT
                  COUNT(*) FILTER (WHERE event IN ('subscription_charged', 'subscription_charged_manual'))::BIGINT AS ok_30d,
                  COUNT(*) FILTER (WHERE event = 'subscription_charge_failed')::BIGINT AS fail_30d
                FROM activity_log
                WHERE created_at >= NOW() - INTERVAL '30 days'
                  AND event IN ('subscription_charged', 'subscription_charged_manual', 'subscription_charge_failed')
                """
            )
            # Revenue: sum amount_rub from active subscriptions × successful charge count,
            # or fall back to joining payments with the charge events.
            # Cleanest: payments where order_id matches a successful charge event
            # in the same minute. But each charge writes a NEW payments row with
            # is_recurrent=TRUE and status='confirmed' (lowercase, from the loop).
            # The INITIAL sub payment ends up status='CONFIRMED' (uppercase, from
            # the webhook). So lowercase 'confirmed' on is_recurrent rows reliably
            # marks rebills.
            revenue_row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(amount_rub), 0)::BIGINT AS revenue_30d
                FROM payments
                WHERE is_recurrent = TRUE
                  AND status = 'confirmed'
                  AND created_at >= NOW() - INTERVAL '30 days'
                """
            )
            # Total subscribers ever started (any status), for context.
            ever_row = await conn.fetchrow(
                "SELECT COUNT(*)::BIGINT AS n FROM subscriptions"
            )
            cancelled_row = await conn.fetchrow(
                "SELECT COUNT(*)::BIGINT AS n FROM subscriptions WHERE status = 'cancelled'"
            )
        return {
            "active_cnt": int(row["active_cnt"] or 0) if row else 0,
            "paused_cnt": int(row["paused_cnt"] or 0) if row else 0,
            "ever_cnt": int(ever_row["n"] or 0) if ever_row else 0,
            "cancelled_cnt": int(cancelled_row["n"] or 0) if cancelled_row else 0,
            "due_today_cnt": int(row["due_today_cnt"] or 0) if row else 0,
            "due_today_rub": int(row["due_today_rub"] or 0) if row else 0,
            "due_7d_cnt": int(row["due_7d_cnt"] or 0) if row else 0,
            "due_7d_rub": int(row["due_7d_rub"] or 0) if row else 0,
            "due_this_month_cnt": int(row["due_this_month_cnt"] or 0) if row else 0,
            "due_this_month_rub": int(row["due_this_month_rub"] or 0) if row else 0,
            "overdue_cnt": int(row["overdue_cnt"] or 0) if row else 0,
            "recurrent_ok_30d": int(charge_stats["ok_30d"] or 0) if charge_stats else 0,
            "recurrent_fail_30d": int(charge_stats["fail_30d"] or 0) if charge_stats else 0,
            "recurrent_revenue_30d": int(revenue_row["revenue_30d"] or 0) if revenue_row else 0,
        }

    async def find_orphan_recurrent_payments(self) -> List[Dict[str, Any]]:
        """Confirmed `is_recurrent=TRUE` payments that have no corresponding
        active/paused subscription row.

        Rows with `rebill_id <> ''` are recoverable: the saved card key reached
        us, so the admin panel can bootstrap the missing subscription. Rows
        without RebillId are paid purchases that cannot be auto-charged until
        the user saves a card again or T-Bank replays a notification containing
        RebillId.
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH latest AS (
                  SELECT DISTINCT ON (p.tg_id)
                         p.order_id, p.tg_id, p.amount_rub, p.package,
                         p.payment_id, p.rebill_id, p.created_at,
                         COALESCE(u.username, '') AS username,
                         EXISTS (
                           SELECT 1 FROM transactions t
                            WHERE t.tg_id = p.tg_id
                              AND t.reason = 'payment'
                              AND (
                                t.context_order_id = p.order_id
                                OR t.admin_note LIKE '%' || p.order_id || '%'
                              )
                         ) AS has_payment_transaction
                  FROM payments p
                  LEFT JOIN users u ON u.tg_id = p.tg_id
                  WHERE p.is_recurrent = TRUE
                    AND UPPER(p.status) = 'CONFIRMED'
                    AND p.payment_id <> ''
                    AND NOT EXISTS (
                        SELECT 1 FROM subscriptions s
                         WHERE s.tg_id = p.tg_id
                           AND s.status IN ('active', 'paused')
                    )
                  ORDER BY p.tg_id, p.created_at DESC
                )
                SELECT *
                FROM latest
                ORDER BY created_at DESC
                """
            )
        return [
            {
                "order_id": str(r["order_id"]),
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "amount_rub": int(r["amount_rub"] or 0),
                "package": str(r["package"] or ""),
                "payment_id": str(r["payment_id"] or ""),
                "rebill_id": str(r["rebill_id"] or ""),
                "has_payment_transaction": bool(r["has_payment_transaction"]),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def get_subscription_by_id(self, sub_id: int) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, tg_id, package, rebill_id, amount_rub, status, "
                "next_charge_at, charge_retries, created_at, cancelled_at "
                "FROM subscriptions WHERE id = $1",
                int(sub_id),
            )
        if not r:
            return None
        return {
            "id": int(r["id"]),
            "tg_id": int(r["tg_id"]),
            "package": str(r["package"] or ""),
            "rebill_id": str(r["rebill_id"] or ""),
            "amount_rub": int(r["amount_rub"] or 0),
            "status": str(r["status"] or ""),
            "next_charge_at": r["next_charge_at"],
            "charge_retries": int(r["charge_retries"] or 0),
            "created_at": _fmt_ts(r["created_at"]),
            "cancelled_at": _fmt_ts(r["cancelled_at"]),
        }

    async def user_payments_history(self, tg_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Full payment history for one user (bot + manual)."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT order_id, amount_rub, package, status, is_recurrent, "
                "created_at, updated_at, payment_id "
                "FROM payments WHERE tg_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                int(tg_id), int(limit),
            )
        return [
            {
                "order_id": str(r["order_id"] or ""),
                "amount_rub": int(r["amount_rub"] or 0),
                "package": str(r["package"] or ""),
                "status": str(r["status"] or ""),
                "is_recurrent": bool(r["is_recurrent"]),
                "created_at": _fmt_ts(r["created_at"]),
                "updated_at": _fmt_ts(r["updated_at"]),
                "payment_id": str(r["payment_id"] or ""),
            }
            for r in rows
        ]

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
        # Idempotent on retries / overlapping replicas: only flip a row that's
        # still pending, and only bump the broadcast counter if we actually
        # changed it. Without the WHERE-status guard a concurrent worker that
        # processed the same row would double-count sent_count/blocked_count.
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            tag = await conn.execute(
                "UPDATE broadcast_deliveries SET status = $1, error = $2, attempts = attempts + 1, sent_at = NOW() "
                "WHERE broadcast_id = $3 AND tg_id = $4 AND status = 'pending'",
                str(status), str(error or "")[:500], int(bid), int(tg_id),
            )
            changed = _rowcount_from_tag(tag) > 0
            if not changed:
                return
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
        tier: str = "", exclude_paid: bool = True, respect_anti_fatigue: bool = True,
    ) -> int:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rid = await conn.fetchval(
                "INSERT INTO lifecycle_rules "
                "(name, trigger_type, trigger_json, message_text, parse_mode, cooldown_days, "
                "enabled, created_by, tier, exclude_paid, respect_anti_fatigue) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id",
                _norm_text(name, max_len=120),
                _norm_text(trigger_type, max_len=64),
                json.dumps(trigger or {}, ensure_ascii=False),
                str(message_text or "")[:4000],
                str(parse_mode or "HTML"),
                max(0, int(cooldown_days)),
                bool(enabled),
                _norm_text(created_by, max_len=64),
                _norm_text(tier, max_len=16).upper(),
                bool(exclude_paid),
                bool(respect_anti_fatigue),
            )
        return int(rid or 0)

    async def update_lifecycle_rule(
        self, rid: int, *, name: Optional[str] = None, trigger: Optional[Dict[str, Any]] = None,
        message_text: Optional[str] = None, cooldown_days: Optional[int] = None,
        enabled: Optional[bool] = None, tier: Optional[str] = None,
        exclude_paid: Optional[bool] = None, respect_anti_fatigue: Optional[bool] = None,
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
        if tier is not None:
            add("tier", _norm_text(tier, max_len=16).upper())
        if exclude_paid is not None:
            add("exclude_paid", bool(exclude_paid))
        if respect_anti_fatigue is not None:
            add("respect_anti_fatigue", bool(respect_anti_fatigue))
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

    @staticmethod
    def _row_to_rule(r: Any) -> Dict[str, Any]:
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
            "tier": str(r["tier"] or ""),
            "exclude_paid": bool(r["exclude_paid"]),
            "respect_anti_fatigue": bool(r["respect_anti_fatigue"]),
            "created_at": _fmt_ts(r["created_at"]) if "created_at" in r.keys() else "",
            "updated_at": _fmt_ts(r["updated_at"]) if "updated_at" in r.keys() else "",
        }

    async def list_lifecycle_rules(self, tier: Optional[str] = None) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        cols = (
            "id, name, trigger_type, trigger_json, message_text, parse_mode, "
            "cooldown_days, enabled, last_run_at, fired_count, created_by, "
            "tier, exclude_paid, respect_anti_fatigue, created_at, updated_at"
        )
        async with pool.acquire() as conn:
            if tier:
                rows = await conn.fetch(
                    f"SELECT {cols} FROM lifecycle_rules WHERE UPPER(tier) = $1 ORDER BY id DESC",
                    str(tier).upper(),
                )
            else:
                rows = await conn.fetch(
                    f"SELECT {cols} FROM lifecycle_rules ORDER BY id DESC"
                )
        return [self._row_to_rule(r) for r in rows]

    async def get_lifecycle_rule(self, rid: int) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, name, trigger_type, trigger_json, message_text, parse_mode, "
                "cooldown_days, enabled, last_run_at, fired_count, created_by, "
                "tier, exclude_paid, respect_anti_fatigue, created_at, updated_at "
                "FROM lifecycle_rules WHERE id = $1", int(rid),
            )
        if not r:
            return None
        return self._row_to_rule(r)

    # SQL fragments shared between candidate-finder and breakdown helpers.
    # Global exclusions applied to all auto triggers (PDF v2.0 §"глобальные exclusion-правила"):
    #   • bot_blocked event ever
    #   • admin_dm in last 7 days (user is being worked manually)
    #   • payment_confirmed (when rule.exclude_paid == true)
    #   • anti-fatigue: <1 sent in last 48h AND <2 sent in last 7 days
    #   • this rule's own cooldown (cooldown_days)
    _EXC_BLOCKED = (
        "NOT EXISTS (SELECT 1 FROM activity_log abk WHERE abk.tg_id = u.tg_id "
        "AND abk.event = 'bot_blocked')"
    )
    _EXC_ADMIN_DM_7D = (
        "NOT EXISTS (SELECT 1 FROM activity_log adm WHERE adm.tg_id = u.tg_id "
        "AND adm.event = 'admin_dm' AND adm.created_at >= NOW() - INTERVAL '7 days')"
    )
    # exclude_paid mirrors the has_purchase signal in user_signals view exactly:
    # bot payment OR external revenue logged in manual_payments OR sum of manual
    # credit grants (admin-initiated transactions, system reasons excluded) > 5.
    # The >5 threshold deliberately ignores small goodwill grants of 1-3 credits.
    _EXC_PAID = (
        "NOT EXISTS (SELECT 1 FROM payments pp WHERE pp.tg_id = u.tg_id AND pp.status = 'CONFIRMED') "
        "AND NOT EXISTS (SELECT 1 FROM manual_payments mpp WHERE mpp.tg_id = u.tg_id) "
        "AND COALESCE("
        "  (SELECT SUM(tt.amount) FROM transactions tt WHERE tt.tg_id = u.tg_id "
        "   AND tt.amount > 0 "
        "   AND tt.reason NOT IN ('initial_grant', 'payment', 'subscription', 'subscription_charge', 'refund')), "
        "  0"
        ") <= 5"
    )
    _EXC_ANTI_FATIGUE = (
        "(SELECT COUNT(*) FROM lifecycle_fires lf48 WHERE lf48.tg_id = u.tg_id "
        "AND lf48.status = 'sent' AND lf48.created_at >= NOW() - INTERVAL '48 hours') < 1 "
        "AND (SELECT COUNT(*) FROM lifecycle_fires lf7 WHERE lf7.tg_id = u.tg_id "
        "AND lf7.status = 'sent' AND lf7.created_at >= NOW() - INTERVAL '7 days') < 2"
    )

    @classmethod
    def _global_exclusions_sql(cls, rule: Dict[str, Any]) -> str:
        """Build AND-joined exclusion fragment using table alias `u` for users."""
        parts = [cls._EXC_BLOCKED, cls._EXC_ADMIN_DM_7D]
        if rule.get("exclude_paid", True):
            parts.append(cls._EXC_PAID)
        if rule.get("respect_anti_fatigue", True):
            parts.append(cls._EXC_ANTI_FATIGUE)
        return " AND ".join(parts)

    @staticmethod
    def _cooldown_exclusion_sql(rule_id_param_idx: int, cooldown_param_idx: int) -> str:
        return (
            f"NOT EXISTS (SELECT 1 FROM lifecycle_fires fcd "
            f"WHERE fcd.rule_id = ${rule_id_param_idx} AND fcd.tg_id = u.tg_id "
            f"AND fcd.status = 'sent' "
            f"AND fcd.created_at >= NOW() - (${cooldown_param_idx}::INT * INTERVAL '1 day'))"
        )

    @staticmethod
    def _build_time_after_event_predicate(
        trig: Dict[str, Any],
    ) -> tuple[str, List[Any]]:
        """Return (sql_join_and_where, params) for the trigger event match.

        Semantics — matches user_signals.last_rating exactly so triggers and the
        tier view stay in sync:
          • Find the LATEST `event` of the requested type per user (any detail).
          • If `event_detail` is set, that latest event's detail must equal it
            (a user who rated mid_low at T1 then high at T2 has latest=high
            and is excluded from a rule with event_detail='mid_low').
          • Latest event's timestamp must be in [hours_min, hours_max].
          • blocking_events checks for events strictly AFTER the latest event
            (`>`, not `>=` — the trigger event itself isn't a blocker).
        """
        event = str(trig.get("event") or "").strip()
        detail = trig.get("event_detail")
        hours_min = float(trig.get("hours_min") or 0)
        hours_max = trig.get("hours_max")  # None or float
        blocking_events = trig.get("blocking_events") or []
        if isinstance(blocking_events, str):
            blocking_events = [blocking_events]
        require_gens_eq = trig.get("require_gens_eq")
        require_gens_gte = trig.get("require_gens_gte")
        require_no_referral = bool(trig.get("require_no_referral"))
        require_subscribed = trig.get("require_subscribed")  # None / True / False
        # require_last_rating: str or list[str]; user's MOST RECENT rate_video.detail
        # must equal one of these. Used for S2 (last_rating=high) and similar tier-
        # specific triggers fired by an event that isn't itself rate_video.
        require_last_rating = trig.get("require_last_rating")
        if isinstance(require_last_rating, str):
            require_last_rating = [require_last_rating] if require_last_rating else None
        params: List[Any] = []

        def add(val: Any) -> int:
            params.append(val)
            return len(params)

        # CTE: latest event of `event` type per user (no detail filter here so we
        # can correctly catch users whose newest rating IS NOT the requested one).
        p_event = add(event)
        cte = (
            "WITH last_evt AS ("
            "SELECT DISTINCT ON (a_evt.tg_id) "
            "a_evt.tg_id, a_evt.created_at AS ts, a_evt.detail "
            f"FROM activity_log a_evt WHERE a_evt.event = ${p_event} "
            "ORDER BY a_evt.tg_id, a_evt.created_at DESC"
            ")"
        )

        # Inner predicate: the latest event must satisfy detail + time window.
        inner = ["le.tg_id = u.tg_id"]
        if detail is not None:
            p_detail = add(str(detail))
            inner.append(f"le.detail = ${p_detail}")
        p_hmin = add(float(hours_min))
        inner.append(f"le.ts <= NOW() - (${p_hmin}::REAL * INTERVAL '1 hour')")
        if hours_max is not None:
            p_hmax = add(float(hours_max))
            inner.append(f"le.ts >= NOW() - (${p_hmax}::REAL * INTERVAL '1 hour')")

        where_parts: List[str] = [
            "EXISTS (SELECT 1 FROM last_evt le WHERE " + " AND ".join(inner) + ")"
        ]

        if blocking_events:
            p_block = add(list(blocking_events))
            where_parts.append(
                "NOT EXISTS (SELECT 1 FROM activity_log ab "
                "JOIN last_evt le2 ON le2.tg_id = ab.tg_id "
                f"WHERE ab.tg_id = u.tg_id AND ab.event = ANY(${p_block}::TEXT[]) "
                "AND ab.created_at > le2.ts)"
            )

        if require_gens_eq is not None:
            p_g = add(int(require_gens_eq))
            where_parts.append(
                f"(SELECT COUNT(*) FROM activity_log ag WHERE ag.tg_id = u.tg_id "
                f"AND ag.event = 'generation_done') = ${p_g}"
            )
        if require_gens_gte is not None:
            p_g = add(int(require_gens_gte))
            where_parts.append(
                f"(SELECT COUNT(*) FROM activity_log ag WHERE ag.tg_id = u.tg_id "
                f"AND ag.event = 'generation_done') >= ${p_g}"
            )
        if require_no_referral:
            where_parts.append(
                "NOT EXISTS (SELECT 1 FROM activity_log ar WHERE ar.tg_id = u.tg_id "
                "AND ar.event = 'referral_sent')"
            )
        if require_subscribed is True:
            where_parts.append(
                "EXISTS (SELECT 1 FROM activity_log asub WHERE asub.tg_id = u.tg_id "
                "AND asub.event = 'subscription_ok')"
            )
        elif require_subscribed is False:
            where_parts.append(
                "NOT EXISTS (SELECT 1 FROM activity_log asub WHERE asub.tg_id = u.tg_id "
                "AND asub.event = 'subscription_ok')"
            )
        if require_last_rating:
            p_lr = add(list(require_last_rating))
            where_parts.append(
                "(SELECT a_lr.detail FROM activity_log a_lr WHERE a_lr.tg_id = u.tg_id "
                "AND a_lr.event = 'rate_video' "
                f"ORDER BY a_lr.created_at DESC LIMIT 1) = ANY(${p_lr}::TEXT[])"
            )

        return cte + "\nSELECT u.tg_id FROM users u WHERE " + " AND ".join(where_parts), params

    async def find_lifecycle_candidates(
        self, rule: Dict[str, Any], *, limit: int = 200,
        skip_global_exclusions: bool = False,
    ) -> List[int]:
        """Compute tg_ids that match rule's trigger AND aren't excluded by global rules + cooldown.

        When `skip_global_exclusions=True`, returns the raw predicate match without
        bot_blocked / admin_dm / paid / anti-fatigue / cooldown filters — used by
        the audience breakdown to compute exclusions as Python set differences.
        """
        trigger_type = str(rule.get("trigger_type") or "")
        trig = rule.get("trigger") or {}
        cooldown = int(rule.get("cooldown_days") or 7)
        rid = int(rule.get("id") or 0)

        pool = self._pool_or_fail()
        if skip_global_exclusions:
            global_exc = "TRUE"
            cooldown_clause = "TRUE"
        else:
            global_exc = self._global_exclusions_sql(rule)
            cooldown_clause = None  # filled in per-trigger-type below with proper $idx

        if trigger_type == "time_after_event":
            base_sql, params = self._build_time_after_event_predicate(trig)
            if skip_global_exclusions:
                sql = f"{base_sql} AND TRUE LIMIT ${len(params) + 1}"
                params = [*params, int(limit)]
            else:
                p_rid = len(params) + 1
                p_cd = len(params) + 2
                cd_exc = self._cooldown_exclusion_sql(p_rid, p_cd)
                sql = (
                    f"{base_sql} "
                    f"AND {global_exc} "
                    f"AND {cd_exc} "
                    f"LIMIT ${len(params) + 3}"
                )
                params = [*params, rid, cooldown, int(limit)]

        elif trigger_type == "tier_membership":
            tier = str(trig.get("tier") or "").upper()
            if not tier:
                return []
            if skip_global_exclusions:
                params = [tier, int(limit)]
                sql = (
                    "SELECT u.tg_id FROM users u "
                    "WHERE EXISTS (SELECT 1 FROM user_tiers ut WHERE ut.tg_id = u.tg_id AND ut.tier = $1) "
                    "LIMIT $2"
                )
            else:
                params = [tier, rid, cooldown, int(limit)]
                sql = (
                    "SELECT u.tg_id FROM users u "
                    "WHERE EXISTS (SELECT 1 FROM user_tiers ut WHERE ut.tg_id = u.tg_id AND ut.tier = $1) "
                    f"AND {global_exc} "
                    f"AND {self._cooldown_exclusion_sql(2, 3)} "
                    "LIMIT $4"
                )

        elif trigger_type == "low_balance_trial":
            max_credits = int(trig.get("max_credits", 1))
            base_predicate = (
                "u.credits <= $1 "
                "AND EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id "
                "  AND p.status = 'CONFIRMED' AND p.package = '5') "
                "AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id "
                "  AND p.status = 'CONFIRMED' AND p.package IN ('15','30','50'))"
            )
            if skip_global_exclusions:
                params = [max_credits, int(limit)]
                sql = f"SELECT u.tg_id FROM users u WHERE {base_predicate} LIMIT $2"
            else:
                params = [max_credits, rid, cooldown, int(limit)]
                sql = (
                    f"SELECT u.tg_id FROM users u WHERE {base_predicate} "
                    f"AND {self._cooldown_exclusion_sql(2, 3)} "
                    f"AND {self._EXC_BLOCKED} "
                    f"AND {self._EXC_ADMIN_DM_7D} "
                    + (f"AND {self._EXC_ANTI_FATIGUE} " if rule.get("respect_anti_fatigue", True) else "")
                    + "LIMIT $4"
                )

        elif trigger_type == "balance_low":
            threshold = int(trig.get("credits_leq", 2))
            min_balance = int(trig.get("credits_geq", 1))
            base_predicate = (
                "u.credits BETWEEN $1 AND $2 "
                "AND EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED')"
            )
            if skip_global_exclusions:
                params = [min_balance, threshold, int(limit)]
                sql = f"SELECT u.tg_id FROM users u WHERE {base_predicate} LIMIT $3"
            else:
                params = [min_balance, threshold, rid, cooldown, int(limit)]
                sql = (
                    f"SELECT u.tg_id FROM users u WHERE {base_predicate} "
                    f"AND {self._EXC_BLOCKED} AND {self._EXC_ADMIN_DM_7D} "
                    + (f"AND {self._EXC_ANTI_FATIGUE} " if rule.get("respect_anti_fatigue", True) else "")
                    + f"AND {self._cooldown_exclusion_sql(3, 4)} LIMIT $5"
                )

        elif trigger_type == "inactive":
            days = int(trig.get("days", 14))
            base_predicate = (
                "NOT EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id "
                "  AND a.created_at >= NOW() - ($1::INT * INTERVAL '1 day')) "
                "AND u.created_at <= NOW() - ($1::INT * INTERVAL '1 day')"
            )
            if skip_global_exclusions:
                params = [days, int(limit)]
                sql = f"SELECT u.tg_id FROM users u WHERE {base_predicate} LIMIT $2"
            else:
                params = [days, rid, cooldown, int(limit)]
                sql = (
                    f"SELECT u.tg_id FROM users u WHERE {base_predicate} "
                    f"AND {global_exc} "
                    f"AND {self._cooldown_exclusion_sql(2, 3)} LIMIT $4"
                )

        elif trigger_type == "generated_not_paid":
            min_gens = int(trig.get("min_gens", 3))
            base_predicate = (
                "(SELECT COUNT(*) FROM activity_log a WHERE a.tg_id = u.tg_id "
                "  AND a.event = 'generation_done') >= $1"
            )
            if skip_global_exclusions:
                params = [min_gens, int(limit)]
                sql = f"SELECT u.tg_id FROM users u WHERE {base_predicate} LIMIT $2"
            else:
                params = [min_gens, rid, cooldown, int(limit)]
                sql = (
                    f"SELECT u.tg_id FROM users u WHERE {base_predicate} "
                    f"AND {global_exc} "
                    f"AND {self._cooldown_exclusion_sql(2, 3)} LIMIT $4"
                )

        else:
            return []

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [int(r["tg_id"]) for r in rows]

    async def lifecycle_audience_breakdown(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        """Return a breakdown of how many users match this rule and how many are
        filtered out by each global exclusion. Plus a sample of final candidates.

        Used by the admin "Превью аудитории" button to give confidence in the rule
        before flipping enabled=true.
        """
        # Compute matched-without-exclusions, then per-exclusion delta. We do this
        # with separate "counterfactual" queries — small N (single rule), and the
        # SQL is short-lived so it's OK to run a few of them.
        breakdown = {
            "matched_raw": 0,
            "excluded_blocked": 0,
            "excluded_admin_dm": 0,
            "excluded_paid": 0,
            "excluded_anti_fatigue": 0,
            "excluded_cooldown": 0,
            "final_count": 0,
            "sample": [],  # list of dicts with tg_id, username, last_active_at, last_rating, gens_done
        }
        # Final candidates first (this also limits sample size).
        final_ids = await self.find_lifecycle_candidates(rule, limit=10000)
        breakdown["final_count"] = len(final_ids)

        # "Raw" candidates: same trigger predicate WITHOUT any global exclusion or cooldown.
        raw_ids = await self.find_lifecycle_candidates(
            rule, limit=10000, skip_global_exclusions=True,
        )
        breakdown["matched_raw"] = len(raw_ids)

        if not raw_ids:
            return breakdown

        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            blocked_set = {int(r["tg_id"]) for r in await conn.fetch(
                "SELECT DISTINCT tg_id FROM activity_log "
                "WHERE event = 'bot_blocked' AND tg_id = ANY($1::BIGINT[])",
                raw_ids,
            )}
            admin_dm_set = {int(r["tg_id"]) for r in await conn.fetch(
                "SELECT DISTINCT tg_id FROM activity_log "
                "WHERE event = 'admin_dm' AND tg_id = ANY($1::BIGINT[]) "
                "AND created_at >= NOW() - INTERVAL '7 days'",
                raw_ids,
            )}
            paid_set: set = set()
            if rule.get("exclude_paid", True):
                # Mirror has_purchase exactly: bot payments + manual_payments + sum
                # of admin-initiated credit grants > 5 (small goodwill grants ignored).
                paid_rows = await conn.fetch(
                    "SELECT DISTINCT tg_id FROM ("
                    "  SELECT tg_id FROM payments WHERE status = 'CONFIRMED' AND tg_id = ANY($1::BIGINT[]) "
                    "  UNION "
                    "  SELECT tg_id FROM manual_payments WHERE tg_id = ANY($1::BIGINT[]) "
                    "  UNION "
                    "  SELECT tg_id FROM transactions "
                    "    WHERE tg_id = ANY($1::BIGINT[]) "
                    "    AND amount > 0 "
                    "    AND reason NOT IN ('initial_grant', 'payment', 'subscription', 'subscription_charge', 'refund') "
                    "    GROUP BY tg_id HAVING SUM(amount) > 5"
                    ") src",
                    raw_ids,
                )
                paid_set = {int(r["tg_id"]) for r in paid_rows}
            anti_fatigue_set: set = set()
            if rule.get("respect_anti_fatigue", True):
                anti_fatigue_set = {int(r["tg_id"]) for r in await conn.fetch(
                    "SELECT tg_id FROM lifecycle_fires "
                    "WHERE status = 'sent' AND tg_id = ANY($1::BIGINT[]) "
                    "AND created_at >= NOW() - INTERVAL '48 hours' "
                    "GROUP BY tg_id HAVING COUNT(*) >= 1 "
                    "UNION "
                    "SELECT tg_id FROM lifecycle_fires "
                    "WHERE status = 'sent' AND tg_id = ANY($1::BIGINT[]) "
                    "AND created_at >= NOW() - INTERVAL '7 days' "
                    "GROUP BY tg_id HAVING COUNT(*) >= 2",
                    raw_ids,
                )}
            cooldown_set: set = set()
            rid = int(rule.get("id") or 0)
            cooldown = int(rule.get("cooldown_days") or 0)
            if rid > 0 and cooldown > 0:
                cooldown_set = {int(r["tg_id"]) for r in await conn.fetch(
                    "SELECT DISTINCT tg_id FROM lifecycle_fires "
                    "WHERE rule_id = $1 AND status = 'sent' AND tg_id = ANY($2::BIGINT[]) "
                    "AND created_at >= NOW() - ($3::INT * INTERVAL '1 day')",
                    rid, raw_ids, cooldown,
                )}

        breakdown["excluded_blocked"] = len(blocked_set)
        breakdown["excluded_admin_dm"] = len(admin_dm_set - blocked_set)
        breakdown["excluded_paid"] = len(paid_set - blocked_set - admin_dm_set)
        breakdown["excluded_anti_fatigue"] = len(
            anti_fatigue_set - blocked_set - admin_dm_set - paid_set
        )
        breakdown["excluded_cooldown"] = len(
            cooldown_set - blocked_set - admin_dm_set - paid_set - anti_fatigue_set
        )

        # Sample 10 final candidates with enrichment.
        sample_ids = final_ids[:10]
        if sample_ids:
            async with pool.acquire() as conn:
                sample_rows = await conn.fetch(
                    "SELECT u.tg_id, u.username, "
                    "(SELECT MAX(created_at) FROM activity_log a WHERE a.tg_id = u.tg_id) AS last_active_at, "
                    "(SELECT detail FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'rate_video' "
                    "  ORDER BY created_at DESC LIMIT 1) AS last_rating, "
                    "(SELECT COUNT(*) FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'generation_done') AS gens_done "
                    "FROM users u WHERE u.tg_id = ANY($1::BIGINT[]) "
                    "ORDER BY array_position($1::BIGINT[], u.tg_id)",
                    sample_ids,
                )
            breakdown["sample"] = [
                {
                    "tg_id": int(r["tg_id"]),
                    "username": str(r["username"] or ""),
                    "last_active_at": _fmt_ts(r["last_active_at"]),
                    "last_rating": str(r["last_rating"] or ""),
                    "gens_done": int(r["gens_done"] or 0),
                }
                for r in sample_rows
            ]
        return breakdown

    async def recent_lifecycle_fires(self, rule_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT f.tg_id, u.username, f.status, f.error, f.created_at "
                "FROM lifecycle_fires f LEFT JOIN users u ON u.tg_id = f.tg_id "
                "WHERE f.rule_id = $1 ORDER BY f.created_at DESC LIMIT $2",
                int(rule_id), int(limit),
            )
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "status": str(r["status"] or ""),
                "error": str(r["error"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def recent_lifecycle_fires_for_user(self, tg_id: int, days: int = 7) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT f.rule_id, r.name AS rule_name, r.tier AS rule_tier, "
                "f.status, f.error, f.created_at "
                "FROM lifecycle_fires f LEFT JOIN lifecycle_rules r ON r.id = f.rule_id "
                "WHERE f.tg_id = $1 AND f.created_at >= NOW() - ($2::INT * INTERVAL '1 day') "
                "ORDER BY f.created_at DESC",
                int(tg_id), int(days),
            )
        return [
            {
                "rule_id": int(r["rule_id"]),
                "rule_name": str(r["rule_name"] or "—"),
                "rule_tier": str(r["rule_tier"] or ""),
                "status": str(r["status"] or ""),
                "error": str(r["error"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def lifecycle_rule_stats_24h(self, rule_id: int) -> Dict[str, int]:
        """Return {sent, blocked, failed, throttled} count for the last 24h."""
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*)::BIGINT AS cnt FROM lifecycle_fires "
                "WHERE rule_id = $1 AND created_at >= NOW() - INTERVAL '24 hours' GROUP BY status",
                int(rule_id),
            )
        out = {"sent": 0, "blocked": 0, "failed": 0, "throttled": 0, "test": 0}
        for r in rows:
            out[str(r["status"])] = int(r["cnt"])
        return out

    async def lifecycle_user_recent_counts(self, tg_id: int) -> Dict[str, int]:
        """Per-user count of `sent` lifecycle messages over the last 48h / 7d.

        Used by the worker's last-mile anti-fatigue gate.
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "COALESCE(SUM(CASE WHEN created_at >= NOW() - INTERVAL '48 hours' THEN 1 ELSE 0 END), 0)::INT AS sent_48h, "
                "COALESCE(SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 days' THEN 1 ELSE 0 END), 0)::INT AS sent_7d "
                "FROM lifecycle_fires WHERE tg_id = $1 AND status = 'sent'",
                int(tg_id),
            )
        return {
            "sent_48h": int(row["sent_48h"]) if row else 0,
            "sent_7d": int(row["sent_7d"]) if row else 0,
        }

    async def lifecycle_global_stats_24h(self) -> Dict[str, int]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*)::BIGINT AS cnt FROM lifecycle_fires "
                "WHERE created_at >= NOW() - INTERVAL '24 hours' GROUP BY status"
            )
        out = {"sent": 0, "blocked": 0, "failed": 0, "throttled": 0, "test": 0}
        for r in rows:
            out[str(r["status"])] = int(r["cnt"])
        return out

    async def lifecycle_recent_fires_global(self, limit: int = 100) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT f.rule_id, r.name AS rule_name, r.tier AS rule_tier, "
                "f.tg_id, u.username, f.status, f.error, f.created_at "
                "FROM lifecycle_fires f "
                "LEFT JOIN lifecycle_rules r ON r.id = f.rule_id "
                "LEFT JOIN users u ON u.tg_id = f.tg_id "
                "ORDER BY f.created_at DESC LIMIT $1",
                int(limit),
            )
        return [
            {
                "rule_id": int(r["rule_id"]),
                "rule_name": str(r["rule_name"] or "—"),
                "rule_tier": str(r["rule_tier"] or ""),
                "tg_id": int(r["tg_id"]),
                "username": str(r["username"] or ""),
                "status": str(r["status"] or ""),
                "error": str(r["error"] or ""),
                "created_at": _fmt_ts(r["created_at"]),
            }
            for r in rows
        ]

    async def find_lifecycle_rule_by_tier_name(
        self, tier: str, name: str,
    ) -> Optional[Dict[str, Any]]:
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT id, name, trigger_type, trigger_json, message_text, parse_mode, "
                "cooldown_days, enabled, last_run_at, fired_count, created_by, "
                "tier, exclude_paid, respect_anti_fatigue, created_at, updated_at "
                "FROM lifecycle_rules WHERE UPPER(tier) = $1 AND name = $2 LIMIT 1",
                str(tier).upper(), str(name),
            )
        return self._row_to_rule(r) if r else None

    async def seed_default_lifecycle_rules(
        self,
        defaults: List[Dict[str, Any]],
        legacy_names_to_drop: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, int]:
        """Idempotently sync default lifecycle rules with the in-code spec.

        For each entry in `defaults`:
          • If no rule with (tier, name) exists → INSERT (enabled=False; admin
            flips after preview).
          • If a rule exists AND it's still pristine (created_by='system_seed'
            AND fired_count=0 AND enabled=False AND last_run_at IS NULL) →
            UPDATE its trigger_json, message_text, cooldown_days, exclude_paid,
            respect_anti_fatigue to match the new spec. This lets us evolve seeded
            rule definitions across deploys without leaving stale params behind.
          • Otherwise (admin edited / enabled / it has fired) → LEAVE ALONE.

        For each entry in `legacy_names_to_drop` ([{"tier":..., "name":...}]):
          • If a rule with (tier, name) exists AND it's still pristine → DELETE.
            This cleans up renamed seed entries from older deploys.

        Returns counts: {"inserted", "updated", "dropped"}.
        """
        counts = {"inserted": 0, "updated": 0, "dropped": 0}
        # 1. Drop legacy stale seeds.
        for entry in legacy_names_to_drop or []:
            tier = str(entry.get("tier") or "").upper()
            name = str(entry.get("name") or "").strip()
            if not tier or not name:
                continue
            existing = await self.find_lifecycle_rule_by_tier_name(tier, name)
            if not existing:
                continue
            if not self._is_pristine_seed(existing):
                continue
            await self.delete_lifecycle_rule(existing["id"])
            counts["dropped"] += 1

        # 2. Insert / update active defaults.
        for d in defaults:
            tier = str(d.get("tier") or "").upper()
            name = str(d.get("name") or "").strip()
            if not tier or not name:
                continue
            existing = await self.find_lifecycle_rule_by_tier_name(tier, name)
            if existing is None:
                await self.create_lifecycle_rule(
                    name=name,
                    trigger_type=str(d["trigger_type"]),
                    trigger=dict(d.get("trigger") or {}),
                    message_text=str(d.get("message_text") or ""),
                    parse_mode=str(d.get("parse_mode") or "HTML"),
                    cooldown_days=int(d.get("cooldown_days") or 30),
                    enabled=False,
                    created_by="system_seed",
                    tier=tier,
                    exclude_paid=bool(d.get("exclude_paid", True)),
                    respect_anti_fatigue=bool(d.get("respect_anti_fatigue", True)),
                )
                counts["inserted"] += 1
            elif self._is_pristine_seed(existing):
                await self.update_lifecycle_rule(
                    existing["id"],
                    trigger=dict(d.get("trigger") or {}),
                    message_text=str(d.get("message_text") or ""),
                    cooldown_days=int(d.get("cooldown_days") or 30),
                    exclude_paid=bool(d.get("exclude_paid", True)),
                    respect_anti_fatigue=bool(d.get("respect_anti_fatigue", True)),
                )
                counts["updated"] += 1
        return counts

    @staticmethod
    def _is_pristine_seed(rule: Dict[str, Any]) -> bool:
        """A rule is pristine if it was inserted by the seeder and never touched
        by an admin: still disabled, never fired, no last_run_at."""
        return (
            str(rule.get("created_by") or "") == "system_seed"
            and not rule.get("enabled")
            and int(rule.get("fired_count") or 0) == 0
            and not rule.get("last_run_at")
        )

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

    @asynccontextmanager
    async def broadcast_lock(self, bid: int) -> AsyncIterator[bool]:
        """Postgres advisory lock keyed on broadcast id.

        Mirrors lifecycle_rule_lock — protects against rolling-deploy windows
        where two tg-bot-public replicas overlap and both call
        `fetch_pending_deliveries(bid)`, which would Telegram-deliver the same
        message twice and inflate sent_count.

        Uses the 2-arg advisory lock form with namespace=1 so the keyspace
        cannot collide with the 1-arg lifecycle locks.
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            got = bool(await conn.fetchval(
                "SELECT pg_try_advisory_lock($1, $2)", 1, int(bid),
            ))
            try:
                yield got
            finally:
                if got:
                    try:
                        await conn.execute(
                            "SELECT pg_advisory_unlock($1, $2)", 1, int(bid),
                        )
                    except Exception:
                        pass

    @asynccontextmanager
    async def lifecycle_rule_lock(self, rid: int) -> AsyncIterator[bool]:
        """Postgres advisory lock keyed on the rule id.

        During a deploy `docker compose up -d --build tg-bot-public` recreates
        the container, but the old one keeps ticking until SIGTERM grace
        expires (~10s). For that window two LifecycleWorker instances can race
        on the same candidates and both call `record_lifecycle_fire` — that's
        why some users got duplicate messages.

        The advisory lock fixes it: only one worker holds the lock per rule_id
        at a time. Second worker sees `got=False` and skips this rule for
        this tick. Lock is auto-released on connection close (defensive).
        """
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            got = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", int(rid)))
            try:
                yield got
            finally:
                if got:
                    try:
                        await conn.execute("SELECT pg_advisory_unlock($1)", int(rid))
                    except Exception:
                        # Connection close on pool release will auto-unlock anyway.
                        pass

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
        """Audience resolver for broadcast 'tier' mode.

        For primary tiers (S/P1/P3/P4/A/B), reads from user_tiers view. For
        audience-only tiers (P2 referrers, D1 old-cohort, D2 old-cohort+pkg+high)
        falls through to dedicated SQL since these aren't in the view.
        """
        tier_code = str(tier).upper()
        ids = await self._resolve_audience_only_tier(tier_code)
        if ids is None:
            pool = self._pool_or_fail()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT tg_id FROM user_tiers WHERE tier = $1", tier_code,
                )
            ids = [int(r["tg_id"]) for r in rows]
        if exclude_blocked and ids:
            pool = self._pool_or_fail()
            async with pool.acquire() as conn:
                blocked_rows = await conn.fetch(
                    "SELECT DISTINCT tg_id FROM activity_log "
                    "WHERE event = 'bot_blocked' AND tg_id = ANY($1::BIGINT[])",
                    ids,
                )
            blocked = {int(r["tg_id"]) for r in blocked_rows}
            ids = [x for x in ids if x not in blocked]
        return sorted(set(ids))

    async def _resolve_audience_only_tier(self, tier_code: str) -> Optional[List[int]]:
        """Compute membership for tiers not in user_tiers view (P2, D1, D2).

        Returns None if the tier is a primary tier (caller should fall through to
        the view query).

        All audience-only tiers exclude bot_blocked users, admins, and (for D1/D2)
        users with confirmed payments — same global exclusions as the primary view.
        """
        pool = self._pool_or_fail()
        # Common exclusion fragment: not admin, not bot_blocked.
        common_excl = (
            "u.tg_id NOT IN (SELECT tg_id FROM admins) "
            "AND NOT EXISTS (SELECT 1 FROM activity_log bb WHERE bb.tg_id = u.tg_id AND bb.event = 'bot_blocked')"
        )
        # "Not a paying customer" — same definition as has_purchase=FALSE in
        # user_signals view: no bot payment, no manual_payments row, and total
        # admin-initiated credit grants ≤ 5 (small goodwill grants ignored).
        not_paid_excl = (
            "NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') "
            "AND NOT EXISTS (SELECT 1 FROM manual_payments mp WHERE mp.tg_id = u.tg_id) "
            "AND COALESCE("
            "  (SELECT SUM(t.amount) FROM transactions t WHERE t.tg_id = u.tg_id "
            "   AND t.amount > 0 "
            "   AND t.reason NOT IN ('initial_grant', 'payment', 'subscription', 'subscription_charge', 'refund')), "
            "  0"
            ") <= 5"
        )
        if tier_code == "P2":
            # Referrers — anyone who sent a friend tag. PDF doesn't require excluding
            # paying users (P2 is loyalty / appreciation), so we keep them in.
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT u.tg_id FROM users u "
                    "WHERE EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'referral_sent') "
                    f"AND {common_excl}"
                )
            return [int(r["tg_id"]) for r in rows]
        if tier_code == "D1":
            # Old cohort: last_active < NOW() - 30d AND no payment_confirmed.
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT u.tg_id FROM users u "
                    f"WHERE {common_excl} "
                    f"AND {not_paid_excl} "
                    "AND COALESCE("
                    "  (SELECT MAX(created_at) FROM activity_log a WHERE a.tg_id = u.tg_id), "
                    "  u.created_at"
                    ") < NOW() - INTERVAL '30 days'"
                )
            return [int(r["tg_id"]) for r in rows]
        if tier_code == "D2":
            # D1 ∩ viewed_package + last rating high
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT u.tg_id FROM users u "
                    f"WHERE {common_excl} "
                    f"AND {not_paid_excl} "
                    "AND COALESCE("
                    "  (SELECT MAX(created_at) FROM activity_log a WHERE a.tg_id = u.tg_id), "
                    "  u.created_at"
                    ") < NOW() - INTERVAL '30 days' "
                    "AND EXISTS (SELECT 1 FROM activity_log v WHERE v.tg_id = u.tg_id AND v.event = 'select_package') "
                    "AND (SELECT detail FROM activity_log r WHERE r.tg_id = u.tg_id AND r.event = 'rate_video' "
                    "     ORDER BY r.created_at DESC LIMIT 1) = 'high'"
                )
            return [int(r["tg_id"]) for r in rows]
        return None  # primary tier — caller queries user_tiers

    async def audience_only_tier_count(self, tier_code: str) -> int:
        """Fast COUNT(*) for audience-only tiers without materializing all ids.

        Used by /admin/tiers to render the tile counts without pulling 10k+ tg_ids
        across the network for every page load.
        """
        pool = self._pool_or_fail()
        code = str(tier_code).upper()
        common_excl = (
            "u.tg_id NOT IN (SELECT tg_id FROM admins) "
            "AND NOT EXISTS (SELECT 1 FROM activity_log bb WHERE bb.tg_id = u.tg_id AND bb.event = 'bot_blocked')"
        )
        not_paid_excl = (
            "NOT EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'CONFIRMED') "
            "AND NOT EXISTS (SELECT 1 FROM manual_payments mp WHERE mp.tg_id = u.tg_id) "
            "AND COALESCE("
            "  (SELECT SUM(t.amount) FROM transactions t WHERE t.tg_id = u.tg_id "
            "   AND t.amount > 0 "
            "   AND t.reason NOT IN ('initial_grant', 'payment', 'subscription', 'subscription_charge', 'refund')), "
            "  0"
            ") <= 5"
        )
        async with pool.acquire() as conn:
            if code == "P2":
                val = await conn.fetchval(
                    "SELECT COUNT(*) FROM users u "
                    "WHERE EXISTS (SELECT 1 FROM activity_log a WHERE a.tg_id = u.tg_id AND a.event = 'referral_sent') "
                    f"AND {common_excl}"
                )
                return int(val or 0)
            if code == "D1":
                val = await conn.fetchval(
                    "SELECT COUNT(*) FROM users u "
                    f"WHERE {common_excl} "
                    f"AND {not_paid_excl} "
                    "AND COALESCE("
                    "  (SELECT MAX(created_at) FROM activity_log a WHERE a.tg_id = u.tg_id), "
                    "  u.created_at"
                    ") < NOW() - INTERVAL '30 days'"
                )
                return int(val or 0)
            if code == "D2":
                val = await conn.fetchval(
                    "SELECT COUNT(*) FROM users u "
                    f"WHERE {common_excl} "
                    f"AND {not_paid_excl} "
                    "AND COALESCE("
                    "  (SELECT MAX(created_at) FROM activity_log a WHERE a.tg_id = u.tg_id), "
                    "  u.created_at"
                    ") < NOW() - INTERVAL '30 days' "
                    "AND EXISTS (SELECT 1 FROM activity_log v WHERE v.tg_id = u.tg_id AND v.event = 'select_package') "
                    "AND (SELECT detail FROM activity_log r WHERE r.tg_id = u.tg_id AND r.event = 'rate_video' "
                    "     ORDER BY r.created_at DESC LIMIT 1) = 'high'"
                )
                return int(val or 0)
        return 0

    async def list_audience_only_tier_users(self, tier_code: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Detail rows for P2/D1/D2 (audience-only tiers) in same shape as list_tier_users."""
        ids = await self._resolve_audience_only_tier(str(tier_code).upper())
        if ids is None or not ids:
            return []
        pool = self._pool_or_fail()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM user_signals WHERE tg_id = ANY($1::BIGINT[]) "
                "ORDER BY last_active_at DESC NULLS LAST, tg_id DESC LIMIT $2",
                ids, int(limit),
            )
        return [dict(r) for r in rows]

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
