-- Migration 002: season flow (Hooks S1)
-- Apply idempotently. Same DDL is mirrored into user_store._SCHEMA_SEASON
-- and executed at bot startup; this file is a snapshot for psql/manual replay.
--
--   psql $POSTGRES_DSN -f 002_season.sql

BEGIN;

-- ------------------------------------------------------------------
-- blast_users — extend for season onboarding + status flags
-- ------------------------------------------------------------------
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS intro_step        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS intro_completed   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS updates_enabled   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS update_frequency  TEXT    NOT NULL DEFAULT 'finals_only';  -- all | finals_only
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS account_status    TEXT    NOT NULL DEFAULT 'new_free';     -- new_free | exhausted_free | paid_active | paid_churned
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS waitlist          BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS referrer_tier     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS referrals_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS total_gens        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS paid_until        DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE blast_users ADD COLUMN IF NOT EXISTS last_active       DOUBLE PRECISION NOT NULL DEFAULT 0;

-- ------------------------------------------------------------------
-- season_referrals — qualified-after-intro tracking (separate from
-- legacy blast_referrals which gates on first generation activation).
-- One row per invitee; qualified flips to TRUE when intro completes.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS season_referrals (
    invitee_chat_id  BIGINT           PRIMARY KEY,
    inviter_chat_id  BIGINT           NOT NULL,
    qualified        BOOLEAN          NOT NULL DEFAULT FALSE,
    qualified_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    registered_at    DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE INDEX IF NOT EXISTS idx_season_referrals_inviter
    ON season_referrals (inviter_chat_id);

-- ------------------------------------------------------------------
-- season_generations — track-level history for limits + antifraud.
-- metadata_hash = sha256(title|artist|duration_sec) per TZ §7 v1.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS season_generations (
    id            BIGSERIAL        PRIMARY KEY,
    chat_id       BIGINT           NOT NULL,
    metadata_hash TEXT             NOT NULL DEFAULT '',
    status        TEXT             NOT NULL DEFAULT 'pending',  -- pending | success | failed | flagged
    created_at    DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE INDEX IF NOT EXISTS idx_season_generations_chat_ts
    ON season_generations (chat_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_season_generations_meta
    ON season_generations (metadata_hash)
    WHERE metadata_hash != '';

-- ------------------------------------------------------------------
-- content_events — broadcast queue payloads (Update Engine, v2).
-- Created now so admin can stage events before delivery wiring lands.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_events (
    id          BIGSERIAL        PRIMARY KEY,
    event_type  TEXT             NOT NULL,           -- major | mid | minor
    payload     JSONB            NOT NULL DEFAULT '{}'::jsonb,
    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
    delivered   BOOLEAN          NOT NULL DEFAULT FALSE
);

-- ------------------------------------------------------------------
-- season_broadcasts_log — per-user delivery log for content_events.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS season_broadcasts_log (
    id           BIGSERIAL        PRIMARY KEY,
    chat_id      BIGINT           NOT NULL,
    event_id     BIGINT           NOT NULL REFERENCES content_events(id),
    delivered_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
    status       TEXT             NOT NULL DEFAULT 'sent'      -- sent | failed | skipped
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_season_broadcasts_log_chat_event
    ON season_broadcasts_log (chat_id, event_id);

COMMIT;
