"""
asyncpg connection pool factory.

Usage (bot startup):
    from .db import create_pool, run_migrations
    pool = await create_pool(dsn)
    await run_migrations(pool)

Usage (orchestrator startup):
    from services.tg_bot_botapi.db import create_pool
    pool = await create_pool(dsn)

Both services share the same PostgreSQL database and the same schema.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import asyncpg

log = logging.getLogger("blast.db")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def create_pool(dsn: str, *, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
    )
    log.info("db_pool_created dsn=%s min=%d max=%d", _redact(dsn), min_size, max_size)
    return pool


async def run_migrations(pool: asyncpg.Pool) -> None:
    """
    Apply all *.sql files in migrations/ in order.
    Each file is idempotent (IF NOT EXISTS) so re-running is safe.
    """
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        log.warning("run_migrations: no migration files found in %s", _MIGRATIONS_DIR)
        return

    async with pool.acquire() as conn:
        for f in sql_files:
            sql = f.read_text(encoding="utf-8")
            log.info("applying_migration %s", f.name)
            await conn.execute(sql)
            log.info("migration_ok %s", f.name)


def _redact(dsn: str) -> str:
    """Hide password from DSN for logging."""
    try:
        import re
        return re.sub(r":[^:@/]+@", ":***@", dsn)
    except Exception:
        return "<dsn>"
