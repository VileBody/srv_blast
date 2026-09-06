CREATE TABLE IF NOT EXISTS notification_outbox (
    event_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt DOUBLE PRECISION NOT NULL DEFAULT 0,
    delivered_at DOUBLE PRECISION,
    last_error TEXT NOT NULL DEFAULT ''
);
