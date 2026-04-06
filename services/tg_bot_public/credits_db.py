"""PostgreSQL-backed credits & user tracking for the public Telegram bot."""

from __future__ import annotations

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
                "  (SELECT COALESCE(SUM(p.amount_rub), 0)::BIGINT FROM payments p, cutoff c WHERE p.status = 'CONFIRMED' AND p.created_at >= c.ts) AS revenue_rub",
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
        }

    async def period_stats_range(self, date_from: datetime, date_to: datetime) -> Dict[str, int]:
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
                "  (SELECT COALESCE(SUM(amount_rub), 0)::BIGINT FROM payments WHERE status = 'CONFIRMED' AND created_at >= $1 AND created_at < $2) AS revenue_rub",
                date_from,
                date_to,
            )
        empty = {
            "users_new": 0, "starts_users": 0,
            "generation_started_users": 0, "generation_done_users": 0,
            "generation_failed_users": 0, "purchase_intent_users": 0,
            "paid_orders": 0, "revenue_rub": 0,
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
                "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, created_at "
                "FROM payments WHERE status = 'pending' ORDER BY created_at ASC",
            )
            return [
                {"id": r["id"], "order_id": r["order_id"], "tg_id": r["tg_id"],
                 "amount_rub": r["amount_rub"], "package": r["package"], "status": r["status"],
                 "payment_id": r["payment_id"], "created_at": r["created_at"]}
                for r in rows
            ]

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

    async def users_by_source(self, source: str, limit: int = 200) -> List[Dict[str, Any]]:
        pool = self._pool_or_fail()
        src = str(source or "").strip()
        async with pool.acquire() as conn:
            if src == "(direct)":
                rows = await conn.fetch(
                    "SELECT tg_id, username, credits, created_at, updated_at, source "
                    "FROM users "
                    "WHERE (source = '' OR source IS NULL) "
                    "AND (first_utm_source = '' OR first_utm_source IS NULL) "
                    "ORDER BY created_at DESC LIMIT $1",
                    int(limit),
                )
            else:
                rows = await conn.fetch(
                    "SELECT tg_id, username, credits, created_at, updated_at, source "
                    "FROM users "
                    "WHERE source = $1 OR first_utm_source = $1 "
                    "ORDER BY created_at DESC LIMIT $2",
                    src,
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
