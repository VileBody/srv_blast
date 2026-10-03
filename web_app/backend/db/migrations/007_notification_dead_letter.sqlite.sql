-- Dead-letter для outbox (см. postgres-версию).
ALTER TABLE notification_outbox ADD COLUMN dead_at DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending ON notification_outbox (next_attempt) WHERE delivered_at IS NULL AND dead_at IS NULL;
