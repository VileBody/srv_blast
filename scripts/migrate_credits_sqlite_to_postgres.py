#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

import asyncpg

from services.tg_bot_public.config import SETTINGS
from services.tg_bot_public.credits_db import _SCHEMA


TABLES = ("users", "transactions", "admins", "activity_log", "payments")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _fetch_all(conn: sqlite3.Connection, table: str, sql: str) -> list[sqlite3.Row]:
    if not _table_exists(conn, table):
        return []
    return list(conn.execute(sql).fetchall())


def _safe_ts(raw: object) -> datetime:
    text = str(raw or "").strip()
    if text:
        normalized = text.replace("T", " ").replace("Z", "")
        try:
            return datetime.fromisoformat(normalized)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(normalized, fmt)
            except Exception:
                continue
    return datetime.utcnow()


def _counts_summary(**kwargs: int) -> str:
    return ", ".join(f"{k}={v}" for k, v in kwargs.items())


async def migrate(*, sqlite_path: Path, dsn: str, dry_run: bool) -> None:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"sqlite source not found: {sqlite_path}")

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    try:
        users = _fetch_all(src, "users", "SELECT tg_id, username, credits, created_at, updated_at FROM users")
        transactions = _fetch_all(
            src,
            "transactions",
            "SELECT id, tg_id, amount, reason, admin_note, created_at FROM transactions ORDER BY id",
        )
        admins = _fetch_all(src, "admins", "SELECT tg_id, username, added_at FROM admins")
        activity = _fetch_all(
            src,
            "activity_log",
            "SELECT id, tg_id, event, detail, created_at FROM activity_log ORDER BY id",
        )
        payments = _fetch_all(
            src,
            "payments",
            "SELECT id, order_id, tg_id, amount_rub, package, status, payment_id, created_at, updated_at FROM payments ORDER BY id",
        )
    finally:
        src.close()

    print(
        "sqlite_counts:",
        _counts_summary(
            users=len(users),
            transactions=len(transactions),
            admins=len(admins),
            activity_log=len(activity),
            payments=len(payments),
        ),
    )

    if dry_run:
        print("dry_run=1, no writes to postgres")
        return

    conn = await asyncpg.connect(dsn=dsn)
    try:
        await conn.execute("SET TIME ZONE 'UTC'")
        await conn.execute(_SCHEMA)

        async with conn.transaction():
            for r in users:
                await conn.execute(
                    "INSERT INTO users (tg_id, username, credits, created_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (tg_id) DO UPDATE SET "
                    "username = EXCLUDED.username, credits = EXCLUDED.credits, "
                    "updated_at = GREATEST(users.updated_at, EXCLUDED.updated_at)",
                    int(r["tg_id"]),
                    str(r["username"] or ""),
                    int(r["credits"] or 0),
                    _safe_ts(r["created_at"]),
                    _safe_ts(r["updated_at"]),
                )

            for r in transactions:
                await conn.execute(
                    "INSERT INTO transactions (id, tg_id, amount, reason, admin_note, created_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6) "
                    "ON CONFLICT (id) DO NOTHING",
                    int(r["id"]),
                    int(r["tg_id"]),
                    int(r["amount"]),
                    str(r["reason"] or ""),
                    str(r["admin_note"] or ""),
                    _safe_ts(r["created_at"]),
                )

            for r in admins:
                await conn.execute(
                    "INSERT INTO admins (tg_id, username, added_at) VALUES ($1, $2, $3) "
                    "ON CONFLICT (tg_id) DO NOTHING",
                    int(r["tg_id"]),
                    str(r["username"] or ""),
                    _safe_ts(r["added_at"]),
                )

            for r in activity:
                await conn.execute(
                    "INSERT INTO activity_log (id, tg_id, event, detail, created_at) "
                    "VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (id) DO NOTHING",
                    int(r["id"]),
                    int(r["tg_id"]),
                    str(r["event"] or ""),
                    str(r["detail"] or ""),
                    _safe_ts(r["created_at"]),
                )

            for r in payments:
                await conn.execute(
                    "INSERT INTO payments (id, order_id, tg_id, amount_rub, package, status, payment_id, created_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                    "ON CONFLICT (order_id) DO UPDATE SET "
                    "tg_id = EXCLUDED.tg_id, amount_rub = EXCLUDED.amount_rub, package = EXCLUDED.package, "
                    "status = EXCLUDED.status, payment_id = EXCLUDED.payment_id, "
                    "updated_at = GREATEST(payments.updated_at, EXCLUDED.updated_at)",
                    int(r["id"]),
                    str(r["order_id"]),
                    int(r["tg_id"]),
                    int(r["amount_rub"]),
                    str(r["package"] or ""),
                    str(r["status"] or "NEW"),
                    str(r["payment_id"] or ""),
                    _safe_ts(r["created_at"]),
                    _safe_ts(r["updated_at"]),
                )

            for table in ("transactions", "activity_log", "payments"):
                await conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 0), 1), true)"
                )

        pg_counts = {}
        for table in TABLES:
            pg_counts[table] = int(await conn.fetchval(f"SELECT COUNT(*) FROM {table}"))
        print("postgres_counts:", _counts_summary(**pg_counts))
    finally:
        await conn.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Migrate tg_bot_public credits DB from SQLite to Postgres")
    p.add_argument("--sqlite", default="/app/work/credits.db", help="Path to source sqlite DB")
    p.add_argument("--dsn", default="", help="Target Postgres DSN (defaults to CREDITS_DB_URL/POSTGRES_*)")
    p.add_argument("--dry-run", action="store_true", help="Only read source and print counts")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    dsn = str(args.dsn or SETTINGS.credits_db_url or "").strip()
    if not dsn:
        raise SystemExit("Postgres DSN is empty. Set CREDITS_DB_URL or POSTGRES_* in environment.")
    asyncio.run(migrate(sqlite_path=Path(args.sqlite), dsn=dsn, dry_run=bool(args.dry_run)))


if __name__ == "__main__":
    main()
