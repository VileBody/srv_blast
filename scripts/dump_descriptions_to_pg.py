#!/usr/bin/env python
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
import ssl as ssl_mod

import asyncpg
from dotenv import load_dotenv

# ---- подхватываем .env из корня проекта ----

ROOT = Path(__file__).resolve().parents[1]
dotenv_path = ROOT / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.library_store import DescriptionFile, DescriptionResponse  # noqa: E402

DESCRIPTIONS_DIR = Path(os.getenv("DESCRIPTIONS_DIR", "./descriptions"))


async def get_conn() -> asyncpg.Connection:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB")
    sslmode = os.getenv("POSTGRES_SSLMODE", "prefer").lower()

    ssl_ctx = None
    if sslmode in ("require", "verify-full", "verify-ca"):
        # простой вариант: доверяем системным корням
        ssl_ctx = ssl_mod.create_default_context()

    print(f"Connecting to Postgres: host={host} port={port} db={database} user={user} sslmode={sslmode}")

    conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        ssl=ssl_ctx,
    )
    return conn


async def ensure_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS video_descriptions (
            prefix      TEXT PRIMARY KEY,
            summary     TEXT NOT NULL,
            tags        TEXT[] NOT NULL,
            response    JSONB NOT NULL,
            options     JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


async def upsert_description(
    conn: asyncpg.Connection,
    prefix: str,
    summary: str,
    tags: list[str],
    response: dict,
    options: list[dict],
) -> None:
    await conn.execute(
        """
        INSERT INTO video_descriptions(prefix, summary, tags, response, options, updated_at)
        VALUES($1, $2, $3, $4::jsonb, $5::jsonb, NOW())
        ON CONFLICT(prefix) DO UPDATE
        SET summary   = EXCLUDED.summary,
            tags      = EXCLUDED.tags,
            response  = EXCLUDED.response,
            options   = EXCLUDED.options,
            updated_at = NOW();
        """,
        prefix,
        summary,
        tags,
        json.dumps(response),
        json.dumps(options),
    )


async def main():
    if not DESCRIPTIONS_DIR.exists():
        print(f"Descriptions dir {DESCRIPTIONS_DIR} does not exist")
        return

    conn = await get_conn()
    try:
        await ensure_table(conn)

        json_files = sorted(DESCRIPTIONS_DIR.glob("*.json"))
        print(f"Found {len(json_files)} description files in {DESCRIPTIONS_DIR}")

        for path in json_files:
            raw = path.read_text(encoding="utf-8")
            model = DescriptionFile.model_validate_json(raw)

            # нормализуем response
            if isinstance(model.response, DescriptionResponse):
                resp_obj = model.response
            elif isinstance(model.response, list) and model.response:
                resp_obj = model.response[0]
            else:
                resp_obj = DescriptionResponse()  # пустой

            summary = resp_obj.summary or ""
            tags = resp_obj.tags or []

            response_dict = resp_obj.model_dump()
            options_list = [opt.model_dump() for opt in model.options]

            await upsert_description(
                conn,
                prefix=path.stem,
                summary=summary,
                tags=tags,
                response=response_dict,
                options=options_list,
            )
            print(f"Upserted {path.name}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
