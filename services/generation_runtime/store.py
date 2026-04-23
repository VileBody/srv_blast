from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

log = logging.getLogger("generation_runtime")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS generation_runs (
    run_id                    TEXT PRIMARY KEY,
    surface                   TEXT        NOT NULL,
    chat_id                   BIGINT      NOT NULL,
    batch_id                  TEXT        NOT NULL DEFAULT '',
    status                    TEXT        NOT NULL DEFAULT 'queued',
    versions_total            INTEGER     NOT NULL DEFAULT 1,
    next_version_to_enqueue   INTEGER     NOT NULL DEFAULT 1,
    current_stage             TEXT        NOT NULL DEFAULT '',
    last_error_code           TEXT        NOT NULL DEFAULT '',
    last_error_text           TEXT        NOT NULL DEFAULT '',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_runs_surface_batch
    ON generation_runs(surface, batch_id)
    WHERE batch_id <> '';

CREATE INDEX IF NOT EXISTS idx_generation_runs_surface_status_updated
    ON generation_runs(surface, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS generation_versions (
    run_id                    TEXT        NOT NULL REFERENCES generation_runs(run_id) ON DELETE CASCADE,
    version_index             INTEGER     NOT NULL,
    job_id                    TEXT        NOT NULL DEFAULT '',
    job_status                TEXT        NOT NULL DEFAULT 'NEW',
    job_stage                 TEXT        NOT NULL DEFAULT '',
    worker_type               TEXT        NOT NULL DEFAULT '',
    origin_node               TEXT        NOT NULL DEFAULT '',
    build_queue               TEXT        NOT NULL DEFAULT '',
    render_queue              TEXT        NOT NULL DEFAULT '',
    result_url                TEXT        NOT NULL DEFAULT '',
    archive_url               TEXT        NOT NULL DEFAULT '',
    resume_source_job_id      TEXT        NOT NULL DEFAULT '',
    resume_state              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    resume_state_source       TEXT        NOT NULL DEFAULT '',
    resume_state_checksum     TEXT        NOT NULL DEFAULT '',
    resume_state_updated_at   TIMESTAMPTZ,
    last_error_text           TEXT        NOT NULL DEFAULT '',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, version_index)
);

ALTER TABLE generation_versions
    ADD COLUMN IF NOT EXISTS resume_state JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE generation_versions
    ADD COLUMN IF NOT EXISTS resume_state_source TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_versions
    ADD COLUMN IF NOT EXISTS resume_state_checksum TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_versions
    ADD COLUMN IF NOT EXISTS resume_state_updated_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_versions_job_id
    ON generation_versions(job_id)
    WHERE job_id <> '';

CREATE INDEX IF NOT EXISTS idx_generation_versions_run_status
    ON generation_versions(run_id, job_status, version_index);

CREATE TABLE IF NOT EXISTS delivery_outbox (
    outbox_id                 BIGSERIAL PRIMARY KEY,
    run_id                    TEXT        NOT NULL REFERENCES generation_runs(run_id) ON DELETE CASCADE,
    job_id                    TEXT        NOT NULL DEFAULT '',
    surface                   TEXT        NOT NULL,
    kind                      TEXT        NOT NULL,
    dedupe_key                TEXT        NOT NULL UNIQUE,
    payload                   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status                    TEXT        NOT NULL DEFAULT 'pending',
    attempt_count             INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by                 TEXT        NOT NULL DEFAULT '',
    locked_at                 TIMESTAMPTZ,
    last_error                TEXT        NOT NULL DEFAULT '',
    sent_at                   TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_delivery_outbox_status_next_attempt
    ON delivery_outbox(status, next_attempt_at, surface, kind);

CREATE INDEX IF NOT EXISTS idx_delivery_outbox_run_id
    ON delivery_outbox(run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS run_events (
    event_id                  BIGSERIAL PRIMARY KEY,
    run_id                    TEXT        NOT NULL REFERENCES generation_runs(run_id) ON DELETE CASCADE,
    surface                   TEXT        NOT NULL,
    job_id                    TEXT        NOT NULL DEFAULT '',
    event_type                TEXT        NOT NULL,
    payload                   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id_created
    ON run_events(run_id, created_at DESC);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonb(value: Optional[Dict[str, Any]]) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def resume_state_checksum(value: Optional[Dict[str, Any]]) -> str:
    payload = json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        out = dict(row)
    else:
        try:
            out = dict(row.items())
        except Exception:
            try:
                out = dict(row)
            except Exception:
                return {}
    for key in ("payload", "resume_state"):
        raw = out.get(key)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                out[key] = parsed if isinstance(parsed, dict) else {}
            except Exception:
                out[key] = {}
        elif raw is None:
            out[key] = {}
    return out


class GenerationRuntimeStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    async def init_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def upsert_run(
        self,
        *,
        run_id: str,
        surface: str,
        chat_id: int,
        batch_id: str,
        status: str,
        versions_total: int,
        next_version_to_enqueue: int,
        current_stage: str,
        last_error_code: str = "",
        last_error_text: str = "",
    ) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO generation_runs (
                    run_id,
                    surface,
                    chat_id,
                    batch_id,
                    status,
                    versions_total,
                    next_version_to_enqueue,
                    current_stage,
                    last_error_code,
                    last_error_text
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (run_id) DO UPDATE
                SET
                    surface = EXCLUDED.surface,
                    chat_id = EXCLUDED.chat_id,
                    batch_id = EXCLUDED.batch_id,
                    status = EXCLUDED.status,
                    versions_total = EXCLUDED.versions_total,
                    next_version_to_enqueue = EXCLUDED.next_version_to_enqueue,
                    current_stage = EXCLUDED.current_stage,
                    last_error_code = EXCLUDED.last_error_code,
                    last_error_text = EXCLUDED.last_error_text,
                    updated_at = NOW()
                RETURNING *
                """,
                str(run_id),
                str(surface),
                int(chat_id),
                str(batch_id or ""),
                str(status or "queued"),
                max(1, int(versions_total)),
                max(1, int(next_version_to_enqueue)),
                str(current_stage or ""),
                str(last_error_code or ""),
                str(last_error_text or ""),
            )
        return _row_to_dict(row)

    async def update_run(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        next_version_to_enqueue: Optional[int] = None,
        current_stage: Optional[str] = None,
        last_error_code: Optional[str] = None,
        last_error_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE generation_runs
                SET
                    status = COALESCE($2, status),
                    next_version_to_enqueue = COALESCE($3, next_version_to_enqueue),
                    current_stage = COALESCE($4, current_stage),
                    last_error_code = COALESCE($5, last_error_code),
                    last_error_text = COALESCE($6, last_error_text),
                    updated_at = NOW()
                WHERE run_id = $1
                RETURNING *
                """,
                str(run_id),
                str(status or "") or None,
                int(next_version_to_enqueue) if next_version_to_enqueue is not None else None,
                str(current_stage or "") or None,
                str(last_error_code or "") if last_error_code is not None else None,
                str(last_error_text or "") if last_error_text is not None else None,
            )
        return _row_to_dict(row)

    async def get_run(self, run_id: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM generation_runs WHERE run_id = $1",
                str(run_id),
            )
        return _row_to_dict(row)

    async def get_run_by_batch(self, *, surface: str, batch_id: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM generation_runs
                WHERE surface = $1 AND batch_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                str(surface),
                str(batch_id or ""),
            )
        return _row_to_dict(row)

    async def list_runs(
        self,
        *,
        surface: str,
        status: str = "",
        include_terminal: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = [
            "SELECT * FROM generation_runs WHERE surface = $1",
        ]
        params: List[Any] = [str(surface)]
        param_index = 2
        if status:
            query.append(f"AND status = ${param_index}")
            params.append(str(status))
            param_index += 1
        elif not include_terminal:
            query.append("AND status NOT IN ('succeeded', 'failed', 'cancelled')")
        query.append(f"ORDER BY updated_at DESC LIMIT ${param_index} OFFSET ${param_index + 1}")
        params.append(max(1, int(limit)))
        params.append(max(0, int(offset)))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(" ".join(query), *params)
        return [_row_to_dict(row) for row in rows]

    async def list_incomplete_runs(self, *, surface: str, limit: int = 200) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM generation_runs
                WHERE surface = $1 AND status NOT IN ('succeeded', 'failed', 'cancelled')
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                str(surface),
                max(1, int(limit)),
            )
        return [_row_to_dict(row) for row in rows]

    async def upsert_version(
        self,
        *,
        run_id: str,
        version_index: int,
        job_id: str = "",
        job_status: str = "NEW",
        job_stage: str = "",
        worker_type: str = "",
        origin_node: str = "",
        build_queue: str = "",
        render_queue: str = "",
        result_url: str = "",
        archive_url: str = "",
        resume_source_job_id: str = "",
        resume_state: Optional[Dict[str, Any]] = None,
        resume_state_source: str = "",
        resume_state_checksum_value: str = "",
        last_error_text: str = "",
    ) -> Dict[str, Any]:
        resume_obj = dict(resume_state or {})
        resume_checksum = str(resume_state_checksum_value or "").strip()
        if resume_obj and not resume_checksum:
            resume_checksum = resume_state_checksum(resume_obj)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO generation_versions (
                    run_id,
                    version_index,
                    job_id,
                    job_status,
                    job_stage,
                    worker_type,
                    origin_node,
                    build_queue,
                    render_queue,
                    result_url,
                    archive_url,
                    resume_source_job_id,
                    resume_state,
                    resume_state_source,
                    resume_state_checksum,
                    resume_state_updated_at,
                    last_error_text
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13::jsonb,
                    $14,
                    $15,
                    CASE WHEN $15 <> '' THEN NOW() ELSE NULL END,
                    $16
                )
                ON CONFLICT (run_id, version_index) DO UPDATE
                SET
                    job_id = CASE
                        WHEN EXCLUDED.job_id <> '' THEN EXCLUDED.job_id
                        ELSE generation_versions.job_id
                    END,
                    job_status = EXCLUDED.job_status,
                    job_stage = EXCLUDED.job_stage,
                    worker_type = CASE
                        WHEN EXCLUDED.worker_type <> '' THEN EXCLUDED.worker_type
                        ELSE generation_versions.worker_type
                    END,
                    origin_node = CASE
                        WHEN EXCLUDED.origin_node <> '' THEN EXCLUDED.origin_node
                        ELSE generation_versions.origin_node
                    END,
                    build_queue = CASE
                        WHEN EXCLUDED.build_queue <> '' THEN EXCLUDED.build_queue
                        ELSE generation_versions.build_queue
                    END,
                    render_queue = CASE
                        WHEN EXCLUDED.render_queue <> '' THEN EXCLUDED.render_queue
                        ELSE generation_versions.render_queue
                    END,
                    result_url = CASE
                        WHEN EXCLUDED.result_url <> '' THEN EXCLUDED.result_url
                        ELSE generation_versions.result_url
                    END,
                    archive_url = CASE
                        WHEN EXCLUDED.archive_url <> '' THEN EXCLUDED.archive_url
                        ELSE generation_versions.archive_url
                    END,
                    resume_source_job_id = CASE
                        WHEN EXCLUDED.resume_source_job_id <> '' THEN EXCLUDED.resume_source_job_id
                        ELSE generation_versions.resume_source_job_id
                    END,
                    resume_state = CASE
                        WHEN EXCLUDED.resume_state_checksum <> '' THEN EXCLUDED.resume_state
                        ELSE generation_versions.resume_state
                    END,
                    resume_state_source = CASE
                        WHEN EXCLUDED.resume_state_checksum <> '' THEN EXCLUDED.resume_state_source
                        ELSE generation_versions.resume_state_source
                    END,
                    resume_state_checksum = CASE
                        WHEN EXCLUDED.resume_state_checksum <> '' THEN EXCLUDED.resume_state_checksum
                        ELSE generation_versions.resume_state_checksum
                    END,
                    resume_state_updated_at = CASE
                        WHEN EXCLUDED.resume_state_checksum <> '' THEN NOW()
                        ELSE generation_versions.resume_state_updated_at
                    END,
                    last_error_text = CASE
                        WHEN EXCLUDED.last_error_text <> '' THEN EXCLUDED.last_error_text
                        ELSE generation_versions.last_error_text
                    END,
                    updated_at = NOW()
                RETURNING *
                """,
                str(run_id),
                max(1, int(version_index)),
                str(job_id or ""),
                str(job_status or "NEW"),
                str(job_stage or ""),
                str(worker_type or ""),
                str(origin_node or ""),
                str(build_queue or ""),
                str(render_queue or ""),
                str(result_url or ""),
                str(archive_url or ""),
                str(resume_source_job_id or ""),
                _jsonb(resume_obj),
                str(resume_state_source or ""),
                resume_checksum,
                str(last_error_text or ""),
            )
        return _row_to_dict(row)

    async def update_version_resume_state(
        self,
        *,
        job_id: str,
        resume_state: Dict[str, Any],
        resume_state_source: str,
        resume_state_checksum_value: str = "",
    ) -> Dict[str, Any]:
        jid = str(job_id or "").strip()
        if not jid:
            return {}
        resume_obj = dict(resume_state or {})
        if not resume_obj:
            return {}
        checksum = str(resume_state_checksum_value or "").strip() or resume_state_checksum(resume_obj)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE generation_versions
                SET
                    resume_state = $2::jsonb,
                    resume_state_source = $3,
                    resume_state_checksum = $4,
                    resume_state_updated_at = NOW(),
                    updated_at = NOW()
                WHERE job_id = $1
                RETURNING *
                """,
                jid,
                _jsonb(resume_obj),
                str(resume_state_source or ""),
                checksum,
            )
        return _row_to_dict(row)

    async def get_versions(self, run_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM generation_versions
                WHERE run_id = $1
                ORDER BY version_index
                """,
                str(run_id),
            )
        return [_row_to_dict(row) for row in rows]

    async def get_version_by_job(self, job_id: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM generation_versions WHERE job_id = $1",
                str(job_id or ""),
            )
        return _row_to_dict(row)

    async def record_event(
        self,
        *,
        run_id: str,
        surface: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        job_id: str = "",
    ) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO run_events (run_id, surface, job_id, event_type, payload)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING *
                """,
                str(run_id),
                str(surface),
                str(job_id or ""),
                str(event_type or "unknown"),
                _jsonb(payload),
            )
        return _row_to_dict(row)

    async def list_events(
        self,
        run_id: str,
        *,
        event_type: str = "",
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        query = [
            "SELECT * FROM run_events WHERE run_id = $1",
        ]
        params: List[Any] = [str(run_id)]
        param_index = 2
        if event_type:
            query.append(f"AND event_type = ${param_index}")
            params.append(str(event_type))
            param_index += 1
        query.append(f"ORDER BY created_at ASC LIMIT ${param_index}")
        params.append(max(1, int(limit)))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(" ".join(query), *params)
        return [_row_to_dict(row) for row in rows]

    async def ensure_outbox_item(
        self,
        *,
        run_id: str,
        surface: str,
        kind: str,
        dedupe_key: str,
        payload: Optional[Dict[str, Any]] = None,
        job_id: str = "",
        next_attempt_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        next_at = next_attempt_at or _utc_now()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO delivery_outbox (
                    run_id,
                    job_id,
                    surface,
                    kind,
                    dedupe_key,
                    payload,
                    next_attempt_at
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                ON CONFLICT (dedupe_key) DO UPDATE
                SET
                    payload = CASE
                        WHEN delivery_outbox.status = 'sent' THEN delivery_outbox.payload
                        ELSE EXCLUDED.payload
                    END,
                    next_attempt_at = CASE
                        WHEN delivery_outbox.status = 'sent' THEN delivery_outbox.next_attempt_at
                        ELSE LEAST(delivery_outbox.next_attempt_at, EXCLUDED.next_attempt_at)
                    END,
                    updated_at = NOW()
                RETURNING *
                """,
                str(run_id),
                str(job_id or ""),
                str(surface),
                str(kind),
                str(dedupe_key),
                _jsonb(payload),
                next_at,
            )
        return _row_to_dict(row)

    async def get_outbox_item(self, dedupe_key: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM delivery_outbox WHERE dedupe_key = $1",
                str(dedupe_key),
            )
        return _row_to_dict(row)

    async def claim_outbox_item(
        self,
        *,
        dedupe_key: str,
        owner_id: str,
        lease_s: int = 900,
        allow_stale_lease: bool = False,
    ) -> Dict[str, Any]:
        expired_before = _utc_now() - timedelta(seconds=max(1, int(lease_s)))
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE delivery_outbox
                SET
                    status = 'leased',
                    locked_by = $2,
                    locked_at = NOW(),
                    attempt_count = attempt_count + 1,
                    updated_at = NOW()
                WHERE dedupe_key = $1
                  AND status <> 'sent'
                  AND (
                      status IN ('pending', 'failed')
                      OR (
                          $3
                          AND status = 'leased'
                          AND (locked_at IS NULL OR locked_at < $4)
                      )
                  )
                RETURNING *
                """,
                str(dedupe_key),
                str(owner_id),
                bool(allow_stale_lease),
                expired_before,
            )
        return _row_to_dict(row)

    async def claim_ready_outbox_items(
        self,
        *,
        surface: str,
        owner_id: str,
        limit: int = 50,
        stale_lease_s: int = 1800,
    ) -> List[Dict[str, Any]]:
        stale_before = _utc_now() - timedelta(seconds=max(1, int(stale_lease_s)))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH candidates AS (
                    SELECT outbox_id
                    FROM delivery_outbox
                    WHERE surface = $1
                      AND status <> 'sent'
                      AND (
                          (
                              status IN ('pending', 'failed')
                              AND next_attempt_at <= NOW()
                          )
                          OR (
                              status = 'leased'
                              AND (locked_at IS NULL OR locked_at < $2)
                          )
                      )
                    ORDER BY next_attempt_at ASC, outbox_id ASC
                    LIMIT $3
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE delivery_outbox AS outbox
                SET
                    status = 'leased',
                    locked_by = $4,
                    locked_at = NOW(),
                    attempt_count = outbox.attempt_count + 1,
                    updated_at = NOW()
                FROM candidates
                WHERE outbox.outbox_id = candidates.outbox_id
                RETURNING outbox.*
                """,
                str(surface),
                stale_before,
                max(1, int(limit)),
                str(owner_id),
            )
        return [_row_to_dict(row) for row in rows]

    async def mark_outbox_sent(
        self,
        *,
        dedupe_key: str,
        payload_patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        current = await self.get_outbox_item(dedupe_key)
        merged_payload = dict(current.get("payload") or {})
        merged_payload.update(payload_patch or {})
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE delivery_outbox
                SET
                    status = 'sent',
                    payload = $2::jsonb,
                    sent_at = NOW(),
                    locked_by = '',
                    locked_at = NULL,
                    last_error = '',
                    updated_at = NOW()
                WHERE dedupe_key = $1
                RETURNING *
                """,
                str(dedupe_key),
                _jsonb(merged_payload),
            )
        return _row_to_dict(row)

    async def mark_outbox_failed(
        self,
        *,
        dedupe_key: str,
        error_text: str,
        retry_delay_s: int = 0,
        keep_leased: bool = False,
    ) -> Dict[str, Any]:
        next_at = _utc_now() + timedelta(seconds=max(0, int(retry_delay_s)))
        next_status = "leased" if keep_leased else "failed"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE delivery_outbox
                SET
                    status = $2,
                    next_attempt_at = $3,
                    last_error = $4,
                    locked_by = CASE WHEN $5 THEN locked_by ELSE '' END,
                    locked_at = CASE WHEN $5 THEN locked_at ELSE NULL END,
                    updated_at = NOW()
                WHERE dedupe_key = $1
                RETURNING *
                """,
                str(dedupe_key),
                next_status,
                next_at,
                str(error_text or ""),
                bool(keep_leased),
            )
        return _row_to_dict(row)

    async def list_outbox_items(
        self,
        *,
        surface: str,
        run_id: str = "",
        status: Optional[str] = None,
        kind: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        query = [
            "SELECT * FROM delivery_outbox WHERE surface = $1",
        ]
        params: List[Any] = [str(surface)]
        param_index = 2
        if run_id:
            query.append(f"AND run_id = ${param_index}")
            params.append(str(run_id))
            param_index += 1
        if status:
            query.append(f"AND status = ${param_index}")
            params.append(str(status))
            param_index += 1
        if kind:
            query.append(f"AND kind = ${param_index}")
            params.append(str(kind))
            param_index += 1
        query.append(f"ORDER BY created_at DESC LIMIT ${param_index}")
        params.append(max(1, int(limit)))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(" ".join(query), *params)
        return [_row_to_dict(row) for row in rows]

    async def get_runtime_stats(self, *, surface: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            run_rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS count
                FROM generation_runs
                WHERE surface = $1
                GROUP BY status
                """,
                str(surface),
            )
            outbox_rows = await conn.fetch(
                """
                SELECT
                    status,
                    COUNT(*) AS count,
                    EXTRACT(EPOCH FROM (NOW() - MIN(next_attempt_at)))::BIGINT AS oldest_due_age_s
                FROM delivery_outbox
                WHERE surface = $1
                GROUP BY status
                """,
                str(surface),
            )
            old_rows = await conn.fetch(
                """
                SELECT current_stage, COUNT(*) AS count
                FROM generation_runs
                WHERE surface = $1
                  AND status NOT IN ('succeeded', 'failed', 'cancelled')
                  AND updated_at < NOW() - INTERVAL '15 minutes'
                GROUP BY current_stage
                """,
                str(surface),
            )
        return {
            "run_status_counts": {
                str(row.get("status") if isinstance(row, dict) else row["status"]): int(
                    row.get("count") if isinstance(row, dict) else row["count"]
                )
                for row in run_rows
            },
            "outbox_status_counts": {
                str(row.get("status") if isinstance(row, dict) else row["status"]): int(
                    row.get("count") if isinstance(row, dict) else row["count"]
                )
                for row in outbox_rows
            },
            "outbox_oldest_due_age_s": {
                str(row.get("status") if isinstance(row, dict) else row["status"]): max(
                    0,
                    int((row.get("oldest_due_age_s") if isinstance(row, dict) else row["oldest_due_age_s"]) or 0),
                )
                for row in outbox_rows
            },
            "old_incomplete_runs_by_stage": {
                str(row.get("current_stage") if isinstance(row, dict) else row["current_stage"]): int(
                    row.get("count") if isinstance(row, dict) else row["count"]
                )
                for row in old_rows
            },
        }
