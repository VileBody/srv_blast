CREATE SCHEMA IF NOT EXISTS logs;

CREATE OR REPLACE FUNCTION logs.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'append-only table % does not allow %', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TABLE IF NOT EXISTS logs.raw_events (
    id                BIGINT GENERATED ALWAYS AS IDENTITY,
    event_ts          TIMESTAMPTZ NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('loki', 'docker')),
    node_role         TEXT NOT NULL,
    node_name         TEXT NOT NULL,
    service           TEXT NOT NULL DEFAULT '',
    container         TEXT NOT NULL DEFAULT '',
    stream            TEXT NOT NULL DEFAULT '',
    severity          TEXT NOT NULL DEFAULT '',
    job_id            TEXT NOT NULL DEFAULT '',
    request_id        TEXT NOT NULL DEFAULT '',
    message_raw       TEXT NOT NULL,
    message_redacted  TEXT NOT NULL,
    labels_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    attrs_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_fingerprint TEXT NOT NULL,
    s3_bucket         TEXT,
    s3_key            TEXT,
    s3_line_no        INTEGER,
    line_marker       TEXT NOT NULL DEFAULT ''
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS idx_raw_events_event_ts_desc
    ON logs.raw_events (event_ts DESC);

CREATE INDEX IF NOT EXISTS idx_raw_events_service_event_ts_desc
    ON logs.raw_events (service, event_ts DESC);

CREATE INDEX IF NOT EXISTS idx_raw_events_job_id_event_ts_desc
    ON logs.raw_events (job_id, event_ts DESC)
    WHERE job_id <> '';

CREATE INDEX IF NOT EXISTS idx_raw_events_request_id_event_ts_desc
    ON logs.raw_events (request_id, event_ts DESC)
    WHERE request_id <> '';

CREATE INDEX IF NOT EXISTS idx_raw_events_labels_gin
    ON logs.raw_events USING GIN (labels_json);

CREATE INDEX IF NOT EXISTS idx_raw_events_attrs_gin
    ON logs.raw_events USING GIN (attrs_json);

CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_events_event_ts_fingerprint
    ON logs.raw_events (event_ts, event_fingerprint);

DROP TRIGGER IF EXISTS trg_raw_events_reject_mutation ON logs.raw_events;
CREATE TRIGGER trg_raw_events_reject_mutation
BEFORE UPDATE OR DELETE ON logs.raw_events
FOR EACH ROW
EXECUTE FUNCTION logs.reject_mutation();

CREATE TABLE IF NOT EXISTS logs.events_norm (
    norm_id            BIGINT GENERATED ALWAYS AS IDENTITY,
    event_ts           TIMESTAMPTZ NOT NULL,
    raw_event_ts       TIMESTAMPTZ NOT NULL,
    raw_event_id       BIGINT NOT NULL,
    schema_version     INTEGER NOT NULL DEFAULT 1,
    event_domain       TEXT NOT NULL,
    event_name         TEXT NOT NULL,
    outcome            TEXT NOT NULL DEFAULT '',
    job_id             TEXT NOT NULL DEFAULT '',
    request_id         TEXT NOT NULL DEFAULT '',
    queue_name         TEXT NOT NULL DEFAULT '',
    chat_id            BIGINT,
    duration_ms        BIGINT,
    cost_value         NUMERIC(18, 6),
    message_redacted   TEXT NOT NULL,
    attrs_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS idx_events_norm_event_ts_desc
    ON logs.events_norm (event_ts DESC);

CREATE INDEX IF NOT EXISTS idx_events_norm_domain_name_event_ts_desc
    ON logs.events_norm (event_domain, event_name, event_ts DESC);

CREATE INDEX IF NOT EXISTS idx_events_norm_job_id_event_ts_desc
    ON logs.events_norm (job_id, event_ts DESC)
    WHERE job_id <> '';

CREATE INDEX IF NOT EXISTS idx_events_norm_request_id_event_ts_desc
    ON logs.events_norm (request_id, event_ts DESC)
    WHERE request_id <> '';

CREATE INDEX IF NOT EXISTS idx_events_norm_attrs_gin
    ON logs.events_norm USING GIN (attrs_json);

CREATE UNIQUE INDEX IF NOT EXISTS ux_events_norm_raw_event_ref
    ON logs.events_norm (event_ts, raw_event_ts, raw_event_id);

DROP TRIGGER IF EXISTS trg_events_norm_reject_mutation ON logs.events_norm;
CREATE TRIGGER trg_events_norm_reject_mutation
BEFORE UPDATE OR DELETE ON logs.events_norm
FOR EACH ROW
EXECUTE FUNCTION logs.reject_mutation();

CREATE TABLE IF NOT EXISTS logs.ingest_cursor (
    source_kind      TEXT NOT NULL CHECK (source_kind IN ('loki', 'docker')),
    node_name        TEXT NOT NULL,
    node_role        TEXT NOT NULL,
    cursor_key       TEXT NOT NULL,
    window_from_ts   TIMESTAMPTZ NOT NULL,
    window_to_ts     TIMESTAMPTZ NOT NULL,
    last_event_ts    TIMESTAMPTZ,
    last_line_marker TEXT NOT NULL DEFAULT '',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_kind, node_name, node_role, cursor_key)
);

CREATE INDEX IF NOT EXISTS idx_ingest_cursor_updated_at
    ON logs.ingest_cursor (updated_at DESC);

CREATE TABLE IF NOT EXISTS logs.ingest_runs (
    run_id          UUID PRIMARY KEY,
    run_kind        TEXT NOT NULL,
    node_name       TEXT NOT NULL,
    node_role       TEXT NOT NULL,
    window_from_ts  TIMESTAMPTZ NOT NULL,
    window_to_ts    TIMESTAMPTZ NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    raw_events_cnt  BIGINT NOT NULL DEFAULT 0,
    norm_events_cnt BIGINT NOT NULL DEFAULT 0,
    s3_objects_cnt  BIGINT NOT NULL DEFAULT 0,
    error_text      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_started_at_desc
    ON logs.ingest_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_status_started_at_desc
    ON logs.ingest_runs (status, started_at DESC);

CREATE TABLE IF NOT EXISTS logs.s3_objects (
    object_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('loki', 'docker')),
    node_name       TEXT NOT NULL,
    node_role       TEXT NOT NULL,
    bucket          TEXT NOT NULL,
    object_key      TEXT NOT NULL,
    row_count       INTEGER NOT NULL,
    sha256          TEXT NOT NULL,
    window_from_ts  TIMESTAMPTZ NOT NULL,
    window_to_ts    TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    manifest_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (bucket, object_key)
);

CREATE INDEX IF NOT EXISTS idx_s3_objects_window_to_desc
    ON logs.s3_objects (window_to_ts DESC);
