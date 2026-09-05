"""Подключение к БД и раннер миграций.

Два диалекта с одинаковым интерфейсом:
- **SQLite** (по умолчанию, файл `backend/data/blast.db`) — stdlib, ничего ставить не нужно.
  Этого достаточно для одного процесса приложения и переживает рестарт.
- **Postgres** (`DATABASE_URL=postgresql://…`) — прод. Требует `psycopg[binary]`.

Миграции лежат в `backend/db/migrations`. Имя файла определяет, к какому диалекту он
относится: `002_app_state.postgres.sql` / `002_app_state.sqlite.sql`. Файл без суффикса
(`001_render_jobs.sql`) считается постгресовым — так исторически заведена рендер-очередь,
и путь к нему зашит в HANDOFF_render_node.md, поэтому файл не переезжает.

Применённые миграции отмечаются в `schema_migrations`, повторный запуск ничего не ломает.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BACKEND_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BACKEND_ROOT / "db" / "migrations"
DEFAULT_SQLITE_PATH = BACKEND_ROOT / "data" / "blast.db"

_lock = threading.RLock()
_local = threading.local()
_ready = False

# Production Postgres closes idle sessions (`idle_session_timeout`).  A single
# request thread can outlive that timeout, so checking only `connection.closed`
# is insufficient: psycopg reports the server-side close on the next query.
# Keep the check bounded to avoid an extra round-trip for every cursor created.
_POSTGRES_HEALTHCHECK_SEC = 60.0


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def dialect() -> str:
    return "postgres" if database_url() else "sqlite"


def sqlite_path() -> Path:
    override = (os.getenv("BLAST_DB_PATH") or "").strip()
    return Path(override) if override else DEFAULT_SQLITE_PATH


def _new_connection():
    if dialect() == "postgres":
        import psycopg  # локальный импорт: в SQLite-режиме зависимость не нужна

        return psycopg.connect(database_url(), autocommit=False)
    path = sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    # WAL: читатели не блокируют писателя — важно, потому что рендер-воркер пишет
    # прогресс из фонового потока параллельно с запросами API.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _connection():
    """Одно соединение на поток: sqlite3-объект нельзя гонять между потоками свободно."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _new_connection()
        _local.conn = conn
        _local.db_health_checked_at = time.monotonic()
    elif dialect() == "postgres":
        checked_at = float(getattr(_local, "db_health_checked_at", 0.0))
        if time.monotonic() - checked_at >= _POSTGRES_HEALTHCHECK_SEC:
            try:
                if getattr(conn, "closed", False):
                    raise RuntimeError("postgres connection is closed")
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                # `_new_connection` uses an explicit transaction.  Finish the
                # health-check transaction before handing the connection out.
                conn.commit()
                _local.db_health_checked_at = time.monotonic()
            except Exception:
                # Discard exactly this broken session.  Reconnect is explicit
                # and bounded: if the new connection cannot be established, the
                # caller receives the real database error instead of a loop.
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _new_connection()
                _local.conn = conn
                _local.db_health_checked_at = time.monotonic()
    return conn


@contextmanager
def transaction() -> Iterator[Any]:
    """Курсор в транзакции: коммит на выходе, откат на исключении."""
    conn = _connection()
    if dialect() == "sqlite":
        conn.execute("BEGIN IMMEDIATE")
    cursor = conn.cursor()
    try:
        yield cursor
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        cursor.close()


@contextmanager
def read() -> Iterator[Any]:
    """Курсор только для чтения — без явной транзакции."""
    cursor = _connection().cursor()
    try:
        yield cursor
    finally:
        cursor.close()


def sql(query: str) -> str:
    """Плейсхолдеры пишем в стиле psycopg (%s); для SQLite переводим в ?."""
    return query if dialect() == "postgres" else query.replace("%s", "?")


def json_param(value: Any) -> Any:
    """Значение для JSON-колонки: JSONB в Postgres, текст в SQLite."""
    if dialect() == "postgres":
        from psycopg.types.json import Json

        return Json(value)
    return json.dumps(value, ensure_ascii=False)


def json_value(raw: Any) -> Any:
    """Обратное преобразование: Postgres отдаёт уже разобранный JSONB, SQLite — строку."""
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw


def upsert(table: str, key_columns: list[str], columns: list[str]) -> str:
    """INSERT … ON CONFLICT DO UPDATE — синтаксис общий для SQLite и Postgres."""
    updates = ", ".join(f"{name}=EXCLUDED.{name}" for name in columns if name not in key_columns)
    return sql(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
        f"ON CONFLICT ({', '.join(key_columns)}) DO UPDATE SET {updates}"
    )


def _migration_files() -> list[Path]:
    current = dialect()
    chosen: list[Path] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.fullmatch(r"(\d+_[a-z0-9_]+?)(?:\.(postgres|sqlite))?", path.stem)
        if not match:
            continue
        # файл без суффикса — постгресовый (историческая рендер-очередь)
        if (match.group(2) or "postgres") == current:
            chosen.append(path)
    return chosen


def _split_statements(script: str) -> list[str]:
    """Разбивка по «;» вне строковых литералов — sqlite3 умеет только один statement за раз."""
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(script):
        char = script[index]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            buffer.append(char)
        elif char == "-" and script[index:index + 2] == "--":
            end = script.find("\n", index)
            index = len(script) if end < 0 else end
            continue
        elif char == ";":
            statements.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return [item for item in statements if item]


def migrate() -> None:
    """Применить недостающие миграции. Идемпотентно."""
    global _ready
    with _lock:
        if _ready:
            return
        with transaction() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " name TEXT PRIMARY KEY,"
                " applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            cursor.execute("SELECT name FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}
        for path in _migration_files():
            if path.name in applied:
                continue
            script = path.read_text(encoding="utf-8")
            with transaction() as cursor:
                for statement in _split_statements(script):
                    cursor.execute(statement)
                cursor.execute(sql("INSERT INTO schema_migrations (name) VALUES (%s)"), (path.name,))
        _ready = True


def reset_for_tests() -> None:
    """Закрыть соединение текущего потока (используется в тестах и при смене BLAST_DB_PATH)."""
    global _ready
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
        _local.db_health_checked_at = 0.0
    _ready = False
