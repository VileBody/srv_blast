-- Dead-letter для outbox: событие, которое не доставится никогда (бот заблокирован,
-- чата нет, исчерпан лимит попыток), больше не выбирается и не тормозит свежие.
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS dead_at DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending ON notification_outbox (next_attempt) WHERE delivered_at IS NULL AND dead_at IS NULL;
