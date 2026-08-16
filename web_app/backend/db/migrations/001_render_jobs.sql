-- Blast render-очередь на Postgres. Spec: backend/docs/RENDER_JOB_SPEC.md §6/§9.
-- Заменяет in-memory render_store на прод. Claim воркером = FOR UPDATE SKIP LOCKED.

-- Батч = один сабмит визарда (= render_job.json)
CREATE TABLE IF NOT EXISTS render_jobs (
    id              TEXT PRIMARY KEY,               -- batchId (job_xxxxxxxx)
    project_id      TEXT,
    user_id         TEXT NOT NULL,
    idempotency_key TEXT,                           -- дедуп повторного сабмита
    status          TEXT NOT NULL DEFAULT 'queued', -- queued | processing | done | failed
    render_job      JSONB NOT NULL,                 -- полный render_job.json
    attempts        INT  NOT NULL DEFAULT 0,
    worker_id       TEXT,                           -- кто взял в работу (claim)
    heartbeat_at    TIMESTAMPTZ,                    -- живой ли воркер (watchdog реклейм)
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

-- Быстрый claim очереди по FIFO
CREATE INDEX IF NOT EXISTS render_jobs_claim_idx
    ON render_jobs (created_at) WHERE status = 'queued';

-- Идемпотентность: один батч на (user, idempotencyKey)
CREATE UNIQUE INDEX IF NOT EXISTS render_jobs_idem_idx
    ON render_jobs (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Вариация = один ролик
CREATE TABLE IF NOT EXISTS render_variations (
    job_id       TEXT NOT NULL REFERENCES render_jobs(id) ON DELETE CASCADE,
    idx          INT  NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING',  -- контракт фронта: PENDING|PROCESSING|COMPLETED|FAILED
    stage        TEXT NOT NULL DEFAULT 'queued',   -- слой: queued|assembling|rendering|done|failed
    progress     INT  NOT NULL DEFAULT 0,          -- 0..100
    spec         JSONB NOT NULL,                   -- variation (subtitle/background/hook/…)
    download_url TEXT,
    error        TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, idx)
);

-- Атомарный claim одной очередной джобы воркером:
--   UPDATE render_jobs SET status='processing', worker_id=$1, started_at=now(), heartbeat_at=now(), attempts=attempts+1
--   WHERE id = (SELECT id FROM render_jobs WHERE status='queued'
--               ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
--   RETURNING id, render_job;
-- Watchdog-реклейм зависших: status='processing' AND heartbeat_at < now() - interval '2 min' -> 'queued'.
