"""SQLite-backed credits & user tracking for the public Telegram bot."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiosqlite

log = logging.getLogger("credits_db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT    NOT NULL DEFAULT '',
    credits     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id       INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    admin_note  TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tx_tg_id      ON transactions(tg_id);
CREATE INDEX IF NOT EXISTS idx_tx_created_at  ON transactions(created_at);

CREATE TABLE IF NOT EXISTS admins (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT    NOT NULL DEFAULT '',
    added_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id       INTEGER NOT NULL,
    event       TEXT    NOT NULL,
    detail      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_act_tg_id      ON activity_log(tg_id);
CREATE INDEX IF NOT EXISTS idx_act_created_at  ON activity_log(created_at);
CREATE INDEX IF NOT EXISTS idx_act_event       ON activity_log(event);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT    NOT NULL UNIQUE,
    tg_id       INTEGER NOT NULL,
    amount_rub  INTEGER NOT NULL,
    package     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'NEW',
    payment_id  TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pay_tg_id    ON payments(tg_id);
CREATE INDEX IF NOT EXISTS idx_pay_status   ON payments(status);
CREATE INDEX IF NOT EXISTS idx_pay_payment_id ON payments(payment_id);
"""


class CreditsDB:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        log.info("credits_db: initialized at %s", self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "CreditsDB not initialized"
        return self._db

    # ── Users ────────────────────────────────────────────────────────

    async def ensure_user(self, tg_id: int, username: str = "") -> bool:
        """Create user if not exists. Returns True if new user was created."""
        db = self._conn()
        clean = username.lstrip("@").lower()
        cur = await db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        if row:
            if clean:
                await db.execute(
                    "UPDATE users SET username = ?, updated_at = datetime('now') WHERE tg_id = ?",
                    (clean, tg_id),
                )
                await db.commit()
            return False
        await db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (tg_id, clean),
        )
        await db.commit()
        return True

    async def has_paid(self, tg_id: int) -> bool:
        """Check if user has any confirmed payment transaction."""
        db = self._conn()
        cur = await db.execute(
            "SELECT 1 FROM transactions WHERE tg_id = ? AND reason = 'payment' LIMIT 1",
            (tg_id,),
        )
        return await cur.fetchone() is not None

    async def get_balance(self, tg_id: int) -> int:
        db = self._conn()
        cur = await db.execute("SELECT credits FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def add_credits(self, tg_id: int, amount: int, reason: str, admin_note: str = "") -> int:
        """Add (or subtract if negative) credits. Returns new balance."""
        db = self._conn()
        await self.ensure_user(tg_id)
        await db.execute(
            "UPDATE users SET credits = MAX(0, credits + ?), updated_at = datetime('now') WHERE tg_id = ?",
            (amount, tg_id),
        )
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason, admin_note) VALUES (?, ?, ?, ?)",
            (tg_id, amount, reason, admin_note),
        )
        await db.commit()
        return await self.get_balance(tg_id)

    async def deduct_credit(self, tg_id: int) -> bool:
        """Atomically deduct 1 credit if available. Returns True on success."""
        db = self._conn()
        cur = await db.execute(
            "UPDATE users SET credits = credits - 1, updated_at = datetime('now') "
            "WHERE tg_id = ? AND credits >= 1",
            (tg_id,),
        )
        if cur.rowcount == 0:
            return False
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason) VALUES (?, -1, 'generation')",
            (tg_id,),
        )
        await db.commit()
        return True

    # ── Queries for admin panel ──────────────────────────────────────

    async def list_users(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        db = self._conn()
        cur = await db.execute(
            "SELECT tg_id, username, credits, created_at, updated_at "
            "FROM users ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cur.fetchall()
        return [
            {"tg_id": r[0], "username": r[1], "credits": r[2], "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    async def count_users(self) -> int:
        db = self._conn()
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_user(self, tg_id: int) -> Optional[Dict[str, Any]]:
        db = self._conn()
        cur = await db.execute(
            "SELECT tg_id, username, credits, created_at, updated_at FROM users WHERE tg_id = ?",
            (tg_id,),
        )
        r = await cur.fetchone()
        if not r:
            return None
        return {"tg_id": r[0], "username": r[1], "credits": r[2], "created_at": r[3], "updated_at": r[4]}

    async def get_transactions(self, tg_id: int = 0, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        db = self._conn()
        if tg_id:
            cur = await db.execute(
                "SELECT id, tg_id, amount, reason, admin_note, created_at "
                "FROM transactions WHERE tg_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (tg_id, limit, offset),
            )
        else:
            cur = await db.execute(
                "SELECT id, tg_id, amount, reason, admin_note, created_at "
                "FROM transactions ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cur.fetchall()
        return [
            {"id": r[0], "tg_id": r[1], "amount": r[2], "reason": r[3], "admin_note": r[4], "created_at": r[5]}
            for r in rows
        ]

    # ── Admin management ─────────────────────────────────────────────

    async def is_admin(self, tg_id: int) -> bool:
        db = self._conn()
        cur = await db.execute("SELECT tg_id FROM admins WHERE tg_id = ?", (tg_id,))
        return (await cur.fetchone()) is not None

    async def add_admin(self, tg_id: int, username: str = "") -> None:
        db = self._conn()
        clean = username.lstrip("@").lower()
        await db.execute(
            "INSERT OR IGNORE INTO admins (tg_id, username) VALUES (?, ?)",
            (tg_id, clean),
        )
        await db.commit()

    async def remove_admin(self, tg_id: int) -> None:
        db = self._conn()
        await db.execute("DELETE FROM admins WHERE tg_id = ?", (tg_id,))
        await db.commit()

    async def list_admins(self) -> List[Dict[str, Any]]:
        db = self._conn()
        cur = await db.execute("SELECT tg_id, username, added_at FROM admins ORDER BY added_at")
        rows = await cur.fetchall()
        return [{"tg_id": r[0], "username": r[1], "added_at": r[2]} for r in rows]

    # ── Activity log ──────────────────────────────────────────────────

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO activity_log (tg_id, event, detail) VALUES (?, ?, ?)",
            (tg_id, event, detail),
        )
        await db.commit()

    async def get_activity(self, tg_id: int = 0, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        db = self._conn()
        if tg_id:
            cur = await db.execute(
                "SELECT id, tg_id, event, detail, created_at "
                "FROM activity_log WHERE tg_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (tg_id, limit, offset),
            )
        else:
            cur = await db.execute(
                "SELECT id, tg_id, event, detail, created_at "
                "FROM activity_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cur.fetchall()
        return [
            {"id": r[0], "tg_id": r[1], "event": r[2], "detail": r[3], "created_at": r[4]}
            for r in rows
        ]

    # ── Payments ───────────────────────────────────────────────────

    async def create_payment(self, order_id: str, tg_id: int, amount_rub: int, package: str) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO payments (order_id, tg_id, amount_rub, package) VALUES (?, ?, ?, ?)",
            (order_id, tg_id, amount_rub, package),
        )
        await db.commit()

    async def update_payment_status(self, order_id: str, status: str, payment_id: str = "") -> bool:
        """Update payment status. Returns True if updated (not a duplicate)."""
        db = self._conn()
        try:
            if payment_id:
                await db.execute(
                    "UPDATE payments SET status = ?, payment_id = ?, updated_at = datetime('now') WHERE order_id = ?",
                    (status, payment_id, order_id),
                )
            else:
                await db.execute(
                    "UPDATE payments SET status = ?, updated_at = datetime('now') WHERE order_id = ?",
                    (status, order_id),
                )
            await db.commit()
            return True
        except Exception:
            return False

    async def get_payment(self, order_id: str) -> Optional[Dict[str, Any]]:
        db = self._conn()
        cur = await db.execute(
            "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, created_at, updated_at "
            "FROM payments WHERE order_id = ?",
            (order_id,),
        )
        r = await cur.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "order_id": r[1], "tg_id": r[2], "amount_rub": r[3],
            "package": r[4], "status": r[5], "payment_id": r[6],
            "created_at": r[7], "updated_at": r[8],
        }

    async def get_payments(self, tg_id: int = 0, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        db = self._conn()
        if tg_id:
            cur = await db.execute(
                "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, created_at "
                "FROM payments WHERE tg_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (tg_id, limit, offset),
            )
        else:
            cur = await db.execute(
                "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, created_at "
                "FROM payments ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cur.fetchall()
        return [
            {"id": r[0], "order_id": r[1], "tg_id": r[2], "amount_rub": r[3],
             "package": r[4], "status": r[5], "payment_id": r[6], "created_at": r[7]}
            for r in rows
        ]

    async def is_payment_processed(self, payment_id: str, status: str) -> bool:
        """Check if this payment_id+status combo was already processed."""
        db = self._conn()
        cur = await db.execute(
            "SELECT id FROM payments WHERE payment_id = ? AND status = ?",
            (payment_id, status),
        )
        return (await cur.fetchone()) is not None

    async def funnel_summary(self) -> List[Dict[str, Any]]:
        """Count users per latest event (approximate funnel position)."""
        db = self._conn()
        cur = await db.execute(
            "SELECT event, COUNT(*) as cnt FROM ("
            "  SELECT tg_id, event FROM activity_log "
            "  WHERE id IN (SELECT MAX(id) FROM activity_log GROUP BY tg_id)"
            ") GROUP BY event ORDER BY cnt DESC"
        )
        rows = await cur.fetchall()
        return [{"event": r[0], "count": r[1]} for r in rows]
