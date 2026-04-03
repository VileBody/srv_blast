-- Migration 001: credit system initial schema
-- Apply once: psql $POSTGRES_DSN -f 001_init.sql

BEGIN;

-- ------------------------------------------------------------------
-- Users (credit balances + activation state)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blast_users (
    chat_id             BIGINT          PRIMARY KEY,
    username            TEXT            NOT NULL DEFAULT '',
    credits             INTEGER         NOT NULL DEFAULT 0 CHECK (credits >= 0),
    is_activated        BOOLEAN         NOT NULL DEFAULT FALSE,
    activated_at        DOUBLE PRECISION NOT NULL DEFAULT 0,
    referrer_chat_id    BIGINT          NOT NULL DEFAULT 0,
    referral_activation_count INTEGER   NOT NULL DEFAULT 0,
    created_at          DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

-- O(1) username lookup (case-insensitive, only for non-empty usernames)
CREATE UNIQUE INDEX IF NOT EXISTS idx_blast_users_username
    ON blast_users (lower(username))
    WHERE username != '';

-- ------------------------------------------------------------------
-- Ledger (append-only transaction log)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blast_ledger (
    tx_id           TEXT            PRIMARY KEY,
    chat_id         BIGINT          NOT NULL REFERENCES blast_users(chat_id),
    tx_type         TEXT            NOT NULL,   -- payment|deduction|refund|admin_adjustment|referral_bonus|manual_activation
    amount          INTEGER         NOT NULL,   -- positive = credit added, negative = credit removed
    balance_before  INTEGER         NOT NULL,
    balance_after   INTEGER         NOT NULL,
    ref_id          TEXT            NOT NULL DEFAULT '',
    ts              DOUBLE PRECISION NOT NULL,
    note            TEXT            NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_blast_ledger_chat_ts
    ON blast_ledger (chat_id, ts DESC);

-- ------------------------------------------------------------------
-- Orders (payment idempotency sentinel)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blast_orders (
    order_id        TEXT            PRIMARY KEY,
    chat_id         BIGINT          NOT NULL,
    credits         INTEGER         NOT NULL,
    status          TEXT            NOT NULL DEFAULT 'pending',  -- pending|confirmed|rejected
    created_at      DOUBLE PRECISION NOT NULL,
    confirmed_at    DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_blast_orders_chat
    ON blast_orders (chat_id);

-- ------------------------------------------------------------------
-- Referrals (who referred whom)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blast_referrals (
    invitee_chat_id BIGINT          PRIMARY KEY,
    inviter_chat_id BIGINT          NOT NULL,
    registered_at   DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

-- Idempotency guard: bonus granted at most once per invitee
CREATE TABLE IF NOT EXISTS blast_referral_bonuses (
    invitee_chat_id BIGINT          PRIMARY KEY,
    inviter_chat_id BIGINT          NOT NULL,
    granted_at      DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

COMMIT;
